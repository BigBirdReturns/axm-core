"""Public spoke-facing cache-scope contract and placement witnesses."""
from __future__ import annotations

import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from axm_forge import model_cache_scope as scope_mod
from axm_forge import model_runner as runner
from test_model_runner import Handler

NS = "axm-chat/semantic-plan@1"


@contextmanager
def server():
    Handler.calls = []
    Handler.mode = "ollama"
    instance = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{instance.server_address[1]}"
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=5)


def _generate_text(base: str, **overrides):
    values = {
        "system": "system-private",
        "user": "user-private",
        "model": "cheap-test",
        "profile": "luna.semantic@1",
        "purpose": "test/spoke@1",
        "response_schema": "array@1",
        "base_url": base,
        "num_ctx": 8192,
        "cache_namespace": NS,
        "cache_scope": "plan-A",
    }
    values.update(overrides)
    return runner.generate_text(**values)


def test_generate_text_forwards_scope_without_provider_disclosure(monkeypatch, tmp_path: Path):
    cache = tmp_path / "cache"
    monkeypatch.setenv("AXM_MODEL_TRANSPORT", "ollama-native")
    monkeypatch.setenv("AXM_MODEL_CACHE", str(cache))
    with server() as base:
        result = _generate_text(base)
    assert result["receipt"]["cache_namespace"] == NS
    assert result["receipt"]["cache_scope"] == "plan-A"
    assert result["receipt"]["cache_store_sha256"] == scope_mod.cache_store_sha256(cache)
    assert len(Handler.calls) == 1
    payload = Handler.calls[0]["payload"]
    assert "cache_namespace" not in payload
    assert "cache_scope" not in payload
    assert "plan-A" not in str(payload)


def test_generate_text_enforces_all_or_neither(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("AXM_MODEL_TRANSPORT", "ollama-native")
    monkeypatch.setenv("AXM_MODEL_CACHE", str(tmp_path / "cache"))
    with server() as base:
        with pytest.raises(scope_mod.CacheScopeError):
            _generate_text(base, cache_scope="")
        with pytest.raises(scope_mod.CacheScopeError):
            _generate_text(base, cache_namespace="")
    assert Handler.calls == []


def test_scope_api_is_public_and_state_persistence_is_honest(monkeypatch, tmp_path: Path):
    cache = tmp_path / "cache"
    monkeypatch.setenv("AXM_MODEL_CACHE", str(cache))
    assert runner.inspect_cache_scope is scope_mod.inspect_cache_scope
    assert runner.invalidate_cache_scope is scope_mod.invalidate_cache_scope
    assert runner.CacheScopeError is scope_mod.CacheScopeError

    fresh = runner.inspect_cache_scope(NS, "fresh")
    assert fresh["current_epoch"] == 0
    assert fresh["state_persisted"] is False
    assert fresh["cache_store_sha256"] == scope_mod.cache_store_sha256(cache)

    runner.invalidate_cache_scope(NS, "fresh", reason="persist epoch")
    persisted = runner.inspect_cache_scope(NS, "fresh")
    assert persisted["current_epoch"] == 1
    assert persisted["state_persisted"] is True


def test_cache_store_identity_changes_with_root_and_unscoped_placement_survives(
    monkeypatch, tmp_path: Path
):
    first_root = tmp_path / "one"
    second_root = tmp_path / "two"
    assert scope_mod.cache_store_sha256(first_root) != scope_mod.cache_store_sha256(second_root)

    monkeypatch.setenv("AXM_MODEL_TRANSPORT", "ollama-native")
    monkeypatch.setenv("AXM_MODEL_CACHE", str(first_root))
    with server() as base:
        result = runner.generate(
            runner.GenerationRequest(
                system="system",
                user="user",
                model="cheap-test",
                profile="luna.semantic@1",
                purpose="test/unscoped@1",
                response_schema="array@1",
                base_url=base,
                num_ctx=8192,
            )
        )
    assert result.cache_key == result.receipt["request_digest"]
    assert result.receipt["cache_store_sha256"] == scope_mod.cache_store_sha256(first_root)
    assert (first_root / result.cache_key[:2] / f"{result.cache_key}.json").is_file()
