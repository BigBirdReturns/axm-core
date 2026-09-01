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


def test_two_scopes_share_request_identity_but_not_placement(cache: Path):
    with server() as base:
        a = runner.generate(_scoped(base, "plan-A"))
        b = runner.generate(_scoped(base, "plan-B"))
    assert a.receipt["request_digest"] == b.receipt["request_digest"]
    assert a.receipt["cache_key"] != b.receipt["cache_key"]
    assert len(Handler.calls) == 2

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

