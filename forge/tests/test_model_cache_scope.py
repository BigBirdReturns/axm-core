"""Scoped, fenced model caches: identity split, isolation, and invalidation.

Core governs an opaque (namespace, scope) pair supplied by a caller. It never
interprets it. Two identities stay distinct: ``request_digest`` is the semantic
request, ``cache_key`` is physical placement bound to the scope epoch. Without
that split, advancing an epoch would make the same semantic request look like a
different model request.
"""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from axm_forge import model_cache_scope as scope_mod
from axm_forge import model_runner as runner
from axm_forge.model_cache_scope import CacheScopeError

from test_model_runner import Handler, request

NS = "axm-chat/semantic-plan@1"


@contextmanager
def server(mode: str = "ollama"):
    Handler.calls = []
    Handler.mode = mode
    instance = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{instance.server_address[1]}"
    finally:
        instance.shutdown()
        instance.server_close()


@pytest.fixture
def cache(monkeypatch, tmp_path: Path) -> Path:
    root = tmp_path / "cache"
    monkeypatch.setenv("AXM_MODEL_TRANSPORT", "ollama-native")
    monkeypatch.setenv("AXM_MODEL_CACHE", str(root))
    return root


def _scoped(base: str, scope: str):
    return request(base_url=base, cache_namespace=NS, cache_scope=scope)


# 1
def test_same_scope_and_epoch_is_one_call_then_a_hit(cache: Path):
    with server() as base:
        first = runner.generate(_scoped(base, "plan-A"))
        second = runner.generate(_scoped(base, "plan-A"))
    assert first.receipt["cache_hit"] is False
    assert second.receipt["cache_hit"] is True
    assert len(Handler.calls) == 1


# 2
def test_two_scopes_share_request_identity_but_not_placement(cache: Path):
    with server() as base:
        a = runner.generate(_scoped(base, "plan-A"))
        b = runner.generate(_scoped(base, "plan-B"))
    assert a.receipt["request_digest"] == b.receipt["request_digest"]
    assert a.receipt["cache_key"] != b.receipt["cache_key"]
    assert len(Handler.calls) == 2
    for name in ("plan-A", "plan-B"):
        report = scope_mod.inspect_cache_scope(NS, name, root=cache)
        assert report["entry_count"] == 1


# 3
def test_scope_metadata_and_receipts_are_body_free(cache: Path):
    with server() as base:
        result = runner.generate(_scoped(base, "plan-A"))
    blob = json.dumps(scope_mod.inspect_cache_scope(NS, "plan-A", root=cache))
    receipt = json.dumps(result.receipt)
    for text in (blob, receipt):
        assert "system" not in text or "system_sha256" in text
        assert '[{"ok":true}]' not in text
        assert "127.0.0.1" not in text
        assert "Authorization" not in text
    # The scope must never reach the provider.
    assert all("cache_scope" not in call["payload"] for call in Handler.calls)
    assert all("cache_namespace" not in call["payload"] for call in Handler.calls)


# 4
def test_dry_run_reports_the_effect_and_mutates_nothing(cache: Path):
    with server() as base:
        runner.generate(_scoped(base, "plan-A"))
    preview = scope_mod.invalidate_cache_scope(
        NS, "plan-A", reason="dry", dry_run=True, root=cache
    )
    assert preview["dry_run"] is True
    assert preview["entry_count"] == 1
    assert preview["epoch_before"] == 0 and preview["epoch_after"] == 0
    assert preview["deleted_bytes"] == 0
    after = scope_mod.inspect_cache_scope(NS, "plan-A", root=cache)
    assert after["current_epoch"] == 0
    assert after["entry_count"] == 1


# 5, 6, 7
def test_invalidating_one_scope_leaves_the_other_untouched(cache: Path):
    with server() as base:
        runner.generate(_scoped(base, "plan-A"))
        runner.generate(_scoped(base, "plan-B"))
        assert len(Handler.calls) == 2

        receipt = scope_mod.invalidate_cache_scope(
            NS, "plan-A", reason="compiler change", root=cache
        )
        assert receipt["epoch_before"] == 0 and receipt["epoch_after"] == 1
        assert receipt["verified_count"] == 1 and receipt["refused_count"] == 0

        # 5: B is unchanged.
        b_state = scope_mod.inspect_cache_scope(NS, "plan-B", root=cache)
        assert b_state["current_epoch"] == 0 and b_state["entry_count"] == 1

        # 6: A recomputes exactly once, under epoch 1.
        a_again = runner.generate(_scoped(base, "plan-A"))
        assert a_again.receipt["cache_hit"] is False
        assert a_again.receipt["cache_epoch"] == 1
        assert len(Handler.calls) == 3

        # 7: B is still a hit, no new call.
        b_again = runner.generate(_scoped(base, "plan-B"))
        assert b_again.receipt["cache_hit"] is True
        assert len(Handler.calls) == 3

    # The semantic identity did not move when placement did.
    assert a_again.receipt["request_digest"] == b_again.receipt["request_digest"]


# 8
def test_a_tampered_object_refuses_invalidation_and_holds_the_epoch(cache: Path):
    with server() as base:
        runner.generate(_scoped(base, "plan-A"))
    target = next(
        scope_mod.epoch_dir(cache, NS, "plan-A", 0).rglob("*.json")
    )
    value = json.loads(target.read_text(encoding="utf-8"))
    value["receipt"]["response_sha256"] = "0" * 64
    value["response_sha256"] = "0" * 64
    target.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(CacheScopeError):
        scope_mod.invalidate_cache_scope(NS, "plan-A", reason="attempt", root=cache)
    assert scope_mod.inspect_cache_scope(NS, "plan-A", root=cache)["current_epoch"] == 0


# 9
def test_a_call_started_under_epoch_zero_cannot_write_after_invalidation(cache: Path):
    """The fence is state, not timing: invalidate mid-flight, then write."""
    original = runner._invoke_ollama

    def invalidate_then_answer(req, model, endpoint):
        scope_mod.invalidate_cache_scope(
            NS, "plan-A", reason="raced", root=cache
        )
        return original(req, model, endpoint)

    with server() as base:
        runner.generate(_scoped(base, "plan-A"))  # seed epoch 0
        scope_mod.invalidate_cache_scope(NS, "plan-A", reason="reset", root=cache)
        runner._invoke_ollama = invalidate_then_answer
        try:
            raced = runner.generate(_scoped(base, "plan-A"))
        finally:
            runner._invoke_ollama = original

    assert raced.text  # the caller still receives the result
    assert raced.receipt["cacheable"] is False
    assert raced.receipt["cache_write_outcome"] == "REFUSED"
    assert raced.receipt["cache_write_reason"] == "scope_epoch_changed"
    report = scope_mod.inspect_cache_scope(NS, "plan-A", root=cache)
    assert report["current_epoch"] == 2
    assert report["entry_count"] == 0


# 10
def test_existing_unscoped_objects_remain_readable(cache: Path):
    with server() as base:
        first = runner.generate(request(base_url=base))
        second = runner.generate(request(base_url=base))
    assert first.receipt["cache_hit"] is False
    assert second.receipt["cache_hit"] is True
    assert len(Handler.calls) == 1
    # Unscoped placement still equals the semantic identity.
    assert first.receipt["cache_key"] == first.receipt["request_digest"]
    assert (cache / first.cache_key[:2] / f"{first.cache_key}.json").is_file()


def test_all_or_neither_scope_is_enforced(cache: Path):
    with server() as base:
        with pytest.raises(CacheScopeError):
            runner.generate(request(base_url=base, cache_namespace=NS))
        with pytest.raises(CacheScopeError):
            runner.generate(request(base_url=base, cache_scope="plan-A"))


def test_zero_entry_scope_is_still_invalidatable(cache: Path):
    receipt = scope_mod.invalidate_cache_scope(
        NS, "never-used", reason="chat has a sealed shard but no cache object", root=cache
    )
    assert receipt["entry_count"] == 0
    assert receipt["epoch_after"] == 1


def test_invalidation_requires_a_reason_and_an_exact_scope(cache: Path):
    with pytest.raises(CacheScopeError):
        scope_mod.invalidate_cache_scope(NS, "plan-A", reason="  ", root=cache)
    with pytest.raises(CacheScopeError):
        scope_mod.invalidate_cache_scope(NS, "", reason="no wildcards", root=cache)


def test_receipt_chain_links_successive_invalidations(cache: Path):
    first = scope_mod.invalidate_cache_scope(NS, "plan-A", reason="one", root=cache)
    second = scope_mod.invalidate_cache_scope(NS, "plan-A", reason="two", root=cache)
    assert first["prior_scope_receipt_sha256"] == ""
    assert second["prior_scope_receipt_sha256"] == first["receipt_sha256"]
    state = scope_mod.read_state(cache, NS, "plan-A")
    assert state["last_invalidation_receipt_sha256"] == second["receipt_sha256"]
    assert state["current_epoch"] == 2


def test_corrupt_state_is_refused(cache: Path):
    scope_mod.invalidate_cache_scope(NS, "plan-A", reason="seed", root=cache)
    path = scope_mod.scope_dir(cache, NS, "plan-A") / "state.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["current_epoch"] = 99
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(CacheScopeError):
        scope_mod.read_state(cache, NS, "plan-A")
