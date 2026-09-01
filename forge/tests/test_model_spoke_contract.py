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


def _verify_persisted_receipt(value: dict) -> None:
    body = {key: item for key, item in value.items() if key != "receipt_sha256"}
    assert value["receipt_sha256"] == scope_mod._sha256(scope_mod._canonical(body))


def test_invalidation_and_cleanup_are_separately_sealed(monkeypatch, tmp_path: Path):
    cache = tmp_path / "cache"
    monkeypatch.setenv("AXM_MODEL_TRANSPORT", "ollama-native")
    monkeypatch.setenv("AXM_MODEL_CACHE", str(cache))
    with server() as base:
        _generate_text(base)

    result = runner.invalidate_cache_scope(NS, "plan-A", reason="separate claims")
    receipt_dir = scope_mod.scope_dir(cache, NS, "plan-A") / "receipts"
    logical_path = next(receipt_dir.glob("*.invalidation.json"))
    cleanup_path = next(receipt_dir.glob("*.cleanup.json"))
    logical = __import__("json").loads(logical_path.read_text(encoding="utf-8"))
    cleanup = __import__("json").loads(cleanup_path.read_text(encoding="utf-8"))

    _verify_persisted_receipt(logical)
    _verify_persisted_receipt(cleanup)
    assert logical["schema"] == scope_mod.INVALIDATION_SCHEMA
    assert "physically_deleted" not in logical
    assert "inaccessible_residue" not in logical
    assert cleanup["schema"] == scope_mod.CLEANUP_SCHEMA
    assert cleanup["invalidation_receipt_sha256"] == logical["receipt_sha256"]
    assert cleanup["physically_deleted"] is True
    assert cleanup["deleted_bytes"] == logical["retired_bytes"]
    assert result["receipt_sha256"] == logical["receipt_sha256"]
    assert result["cleanup_receipt_sha256"] == cleanup["receipt_sha256"]
    assert result["cleanup_receipt_persisted"] is True


def test_cleanup_failure_is_receipted_without_rolling_back_epoch(monkeypatch, tmp_path: Path):
    cache = tmp_path / "cache"
    monkeypatch.setenv("AXM_MODEL_TRANSPORT", "ollama-native")
    monkeypatch.setenv("AXM_MODEL_CACHE", str(cache))
    with server() as base:
        _generate_text(base)

    def refuse_cleanup(_path):
        raise OSError("simulated locked file")

    monkeypatch.setattr(scope_mod.shutil, "rmtree", refuse_cleanup)
    result = runner.invalidate_cache_scope(NS, "plan-A", reason="logical first")
    assert result["epoch_after"] == 1
    assert result["physically_deleted"] is False
    assert result["deleted_bytes"] == 0
    assert result["inaccessible_residue"].startswith("r/")
    assert result["cleanup_receipt_persisted"] is True

    inspection = runner.inspect_cache_scope(NS, "plan-A")
    assert inspection["current_epoch"] == 1
    assert inspection["entry_count"] == 0
    assert inspection["last_invalidation_receipt_sha256"] == result["receipt_sha256"]

    receipt_dir = scope_mod.scope_dir(cache, NS, "plan-A") / "receipts"
    cleanup = __import__("json").loads(
        next(receipt_dir.glob("*.cleanup.json")).read_text(encoding="utf-8")
    )
    _verify_persisted_receipt(cleanup)
    assert cleanup["physically_deleted"] is False
    assert cleanup["inaccessible_residue"] == result["inaccessible_residue"]


def test_inspection_exposes_verified_last_retirement_witness(monkeypatch, tmp_path: Path):
    cache = tmp_path / "cache"
    monkeypatch.setenv("AXM_MODEL_TRANSPORT", "ollama-native")
    monkeypatch.setenv("AXM_MODEL_CACHE", str(cache))
    with server() as base:
        _generate_text(base)

    result = runner.invalidate_cache_scope(NS, "plan-A", reason="recoverable prepare")
    inspection = runner.inspect_cache_scope(NS, "plan-A")
    logical = inspection["last_invalidation_receipt"]
    cleanup = inspection["last_cleanup_receipt"]

    assert logical["receipt_sha256"] == result["receipt_sha256"]
    assert logical["epoch_before"] == 0 and logical["epoch_after"] == 1
    assert logical["cache_store_sha256"] == inspection["cache_store_sha256"]
    assert cleanup["invalidation_receipt_sha256"] == logical["receipt_sha256"]
    assert inspection["cleanup_receipt_persisted"] is True


def test_inspection_refuses_missing_or_tampered_state_bound_receipt(monkeypatch, tmp_path: Path):
    cache = tmp_path / "cache"
    monkeypatch.setenv("AXM_MODEL_CACHE", str(cache))
    runner.invalidate_cache_scope(NS, "plan-A", reason="seed")
    receipt_dir = scope_mod.scope_dir(cache, NS, "plan-A") / "receipts"
    logical_path = next(receipt_dir.glob("*.invalidation.json"))
    value = __import__("json").loads(logical_path.read_text(encoding="utf-8"))
    value["reason"] = "tampered"
    logical_path.write_text(__import__("json").dumps(value), encoding="utf-8")

    with pytest.raises(scope_mod.CacheScopeError, match="digest is invalid"):
        runner.inspect_cache_scope(NS, "plan-A")


def test_cached_object_preserves_written_outcome(monkeypatch, tmp_path: Path):
    cache = tmp_path / "cache"
    monkeypatch.setenv("AXM_MODEL_TRANSPORT", "ollama-native")
    monkeypatch.setenv("AXM_MODEL_CACHE", str(cache))
    with server() as base:
        first = runner.generate(
            runner.GenerationRequest(
                system="system",
                user="user",
                model="cheap-test",
                profile="luna.semantic@1",
                purpose="test/write-outcome@1",
                response_schema="array@1",
                base_url=base,
                cache_namespace=NS,
                cache_scope="plan-A",
                num_ctx=8192,
            )
        )
        second = runner.generate(
            runner.GenerationRequest(
                system="system",
                user="user",
                model="cheap-test",
                profile="luna.semantic@1",
                purpose="test/write-outcome@1",
                response_schema="array@1",
                base_url=base,
                cache_namespace=NS,
                cache_scope="plan-A",
                num_ctx=8192,
            )
        )
    assert first.receipt["cache_write_outcome"] == "WRITTEN"
    assert second.receipt["cache_hit"] is True
    assert second.receipt["cache_write_outcome"] == "WRITTEN"
