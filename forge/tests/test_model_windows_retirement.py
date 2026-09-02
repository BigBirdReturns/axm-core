"""Windows-bounded cache retirement coordinates.

The active cache object may already sit close to the legacy MAX_PATH boundary.
Retirement must never make its path longer.  The old
``retired/<32-hex-invalidation-id>`` coordinate added enough characters to turn
a writable object into an undeletable one on Windows; the bounded
``r/<08d-epoch>`` coordinate avoids that transition.
"""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from axm_forge import model_cache_scope as scope_mod
from axm_forge import model_runner as runner

NS = "axm-chat/semantic-plan@1"
SCOPE = "plan-windows-path"


class _Handler(BaseHTTPRequestHandler):
    calls = 0

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        type(self).calls += 1
        body = json.dumps(
            {
                "model": request["model"],
                "message": {"content": "[]"},
                "prompt_eval_count": 1,
                "eval_count": 1,
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        pass


@contextmanager
def _server():
    _Handler.calls = 0
    instance = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{instance.server_address[1]}"
    finally:
        instance.shutdown()
        thread.join(timeout=5)
        instance.server_close()


def _near_legacy_limit_root(tmp_path: Path) -> Path:
    """Choose a root whose active object remains writable below 260 chars."""
    root = tmp_path / "cache"
    dummy = "a" * 64
    while len(str(scope_mod.object_path(root, NS, SCOPE, 0, dummy))) < 238:
        root = root / "padding0123456789"
    active = scope_mod.object_path(root, NS, SCOPE, 0, dummy)
    if len(str(active)) > 255:
        # Pytest's own basetemp can be unusually deep. The structural witness
        # below still proves the invariant, while avoiding an impossible setup.
        return tmp_path / "cache"
    return root


def test_retired_coordinate_never_lengthens_a_nested_object(tmp_path: Path) -> None:
    root = _near_legacy_limit_root(tmp_path)
    cache_key = "a" * 64
    relative = Path(cache_key[:2]) / f"{cache_key}.json"
    active = scope_mod.epoch_dir(root, NS, SCOPE, 123) / relative
    retired = scope_mod._retired_dir(root, NS, SCOPE, 123) / relative
    legacy = (
        scope_mod.scope_dir(root, NS, SCOPE)
        / "retired"
        / ("f" * 32)
        / relative
    )

    assert len(str(retired)) <= len(str(active))
    assert len(str(legacy)) > len(str(active))
    assert retired.parent.parent.parent.name == "r"


def test_invalidation_cleans_a_near_limit_generation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    cache = _near_legacy_limit_root(tmp_path)
    monkeypatch.setenv("AXM_MODEL_TRANSPORT", "ollama-native")
    monkeypatch.setenv("AXM_MODEL_CACHE", str(cache))

    with _server() as base:
        first = runner.generate(
            runner.GenerationRequest(
                system="system",
                user="user",
                model="cheap-test",
                profile="luna.semantic@1",
                purpose="test/windows-retirement@1",
                response_schema="array@1",
                base_url=base,
                num_ctx=8192,
                cache_namespace=NS,
                cache_scope=SCOPE,
            )
        )

    active = scope_mod.object_path(
        cache,
        NS,
        SCOPE,
        0,
        first.cache_key,
    )
    assert active.is_file()
    assert len(str(scope_mod._retired_dir(cache, NS, SCOPE, 0))) <= len(
        str(scope_mod.epoch_dir(cache, NS, SCOPE, 0))
    )

    result = runner.invalidate_cache_scope(
        NS,
        SCOPE,
        reason="prove bounded Windows cleanup",
    )
    assert result["epoch_before"] == 0
    assert result["epoch_after"] == 1
    assert result["cleanup_receipt"]["physically_deleted"] is True
    assert not scope_mod._retired_dir(cache, NS, SCOPE, 0).exists()
