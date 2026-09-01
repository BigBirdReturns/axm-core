from __future__ import annotations

import json
import os
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from axm_forge import model_runner as runner


class Handler(BaseHTTPRequestHandler):
    calls: list[dict] = []
    mode = "ollama"

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        self.__class__.calls.append(
            {
                "path": self.path,
                "payload": payload,
                "authorization": self.headers.get("Authorization"),
            }
        )
        if self.__class__.mode == "ollama":
            body = {
                "model": "local-cheap:1",
                "message": {"content": "[{\"ok\":true}]"},
                "prompt_eval_count": 17,
                "eval_count": 8,
            }
        else:
            body = {
                "model": "remote-cheap-2026",
                "choices": [{"message": {"content": "[]"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            }
        encoded = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *_args):
        pass


@contextmanager
def server(mode: str):
    Handler.calls = []
    Handler.mode = mode
    instance = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{instance.server_port}"
    finally:
        instance.shutdown()
        thread.join(timeout=5)
        instance.server_close()


def request(**overrides):
    values = {
        "system": "system",
        "user": "user",
        "model": "cheap-test",
        "profile": "luna.semantic@1",
        "purpose": "test/lens@1",
        "response_schema": "array@1",
        "temperature": 0.0,
        "seed": 29,
        "max_output_tokens": 333,
    }
    values.update(overrides)
    return runner.GenerationRequest(**values)


def test_ollama_native_controls_and_cache(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("AXM_MODEL_TRANSPORT", "ollama-native")
    monkeypatch.setenv("AXM_MODEL_CACHE", str(tmp_path / "cache"))
    with server("ollama") as base:
        result = runner.generate(request(base_url=base))
        assert result.text == '[{"ok":true}]'
        assert result.model == "local-cheap:1"
        assert result.receipt["cache_hit"] is False
        assert len(Handler.calls) == 1
        call = Handler.calls[0]
        assert call["path"] == "/api/chat"
        assert call["payload"]["think"] is False
        assert call["payload"]["options"]["temperature"] == 0.0
        assert call["payload"]["options"]["seed"] == 29
        assert call["payload"]["options"]["num_predict"] == 333

        cached = runner.generate(request(base_url=base))
        assert cached.text == result.text
        assert cached.receipt["cache_hit"] is True
        assert len(Handler.calls) == 1


def test_openai_compatible_contract_and_secret_redaction(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("AXM_MODEL_TRANSPORT", "openai-compatible")
    monkeypatch.setenv("AXM_MODEL_API_KEY", "secret-never-receipted")
    monkeypatch.setenv("AXM_MODEL_CACHE", str(tmp_path / "cache"))
    with server("openai") as base:
        result = runner.generate(request(base_url=base + "/v1"))
    call = Handler.calls[0]
    assert call["path"] == "/v1/chat/completions"
    assert call["authorization"] == "Bearer secret-never-receipted"
    assert call["payload"]["temperature"] == 0.0
    assert call["payload"]["seed"] == 29
    assert call["payload"]["max_tokens"] == 333
    assert result.text == "[]"
    serialized = json.dumps(result.to_dict())
    assert "secret-never-receipted" not in serialized
    cache_text = next((tmp_path / "cache").rglob("*.json")).read_text()
    assert "secret-never-receipted" not in cache_text
    assert "system" not in result.receipt
    assert "user" not in result.receipt


def test_command_transport_is_fresh_and_structured(monkeypatch, tmp_path: Path):
    helper = tmp_path / "model_helper.py"
    helper.write_text(
        """
import json, sys
request = json.load(sys.stdin)
assert request['profile'] == 'luna.semantic@1'
assert request['seed'] == 29
print(json.dumps({'text': '[1]', 'model': 'cli-haiku', 'usage': {'calls': 1}}))
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("AXM_MODEL_TRANSPORT", "command")
    monkeypatch.setenv("AXM_MODEL_COMMAND", f'"{sys.executable}" "{helper}"')
    monkeypatch.setenv("AXM_MODEL_CACHE", "off")
    result = runner.generate(request())
    assert result.text == "[1]"
    assert result.model == "cli-haiku"
    assert result.transport == "command"
    assert result.receipt["usage"] == {"calls": 1}
    assert result.receipt["endpoint"] == "command://local"


def test_cache_key_changes_with_schema_prompt_and_controls(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("AXM_MODEL_TRANSPORT", "command")
    helper = tmp_path / "echo.py"
    helper.write_text("print('[]')\n", encoding="utf-8")
    monkeypatch.setenv("AXM_MODEL_COMMAND", f'"{sys.executable}" "{helper}"')
    monkeypatch.setenv("AXM_MODEL_CACHE", "off")
    keys = {
        runner.generate(request()).cache_key,
        runner.generate(request(user="other")).cache_key,
        runner.generate(request(response_schema="array@2")).cache_key,
        runner.generate(request(seed=30)).cache_key,
    }
    assert len(keys) == 4


def test_auto_model_refuses_unknown_remote_identity(monkeypatch, tmp_path: Path):
    helper = tmp_path / "echo.py"
    helper.write_text("print('[]')\n", encoding="utf-8")
    monkeypatch.setenv("AXM_MODEL_TRANSPORT", "command")
    monkeypatch.setenv("AXM_MODEL_COMMAND", f'"{sys.executable}" "{helper}"')
    monkeypatch.setenv("AXM_MODEL_CACHE", "off")
    result = runner.generate(request(model="auto"))
    assert result.model == "auto"
    assert result.receipt["model_requested"] == "auto"
