"""Provider-neutral, deterministic model invocation for AXM Forge.

Spokes own domain prompts and schemas. Forge owns transport selection, fixed
decoding controls, cache identity, bounded invocation, and body-free receipts.
The module is standard-library only so adding the runner does not widen Forge's
installation requirements.
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

CONTRACT_VERSION = "axm-core/model-runner@1"
RECEIPT_VERSION = "axm-core/model-invocation-receipt@1"
DEFAULT_PROFILE = "luna.semantic@1"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_CACHE = Path.home() / ".axm" / "runtime" / "model-cache"


class ModelRunnerError(RuntimeError):
    """Typed boundary error for one failed invocation."""


@dataclass(frozen=True)
class GenerationRequest:
    system: str
    user: str
    model: str = "auto"
    profile: str = DEFAULT_PROFILE
    purpose: str = "unspecified"
    response_schema: str = "text@1"
    base_url: str | None = None
    timeout: int = 180
    max_output_tokens: int = 2048
    temperature: float = 0.0
    seed: int = 0


@dataclass(frozen=True)
class GenerationResult:
    text: str
    model: str
    transport: str
    cache_key: str
    receipt: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "model": self.model,
            "transport": self.transport,
            "cache_key": self.cache_key,
            "receipt": dict(self.receipt),
        }


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: bytes | str) -> str:
    data = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _transport(base_url: str | None) -> str:
    explicit = os.environ.get("AXM_MODEL_TRANSPORT", "").strip().lower()
    aliases = {
        "ollama": "ollama-native",
        "ollama-native": "ollama-native",
        "openai": "openai-compatible",
        "openai-compatible": "openai-compatible",
        "command": "command",
        "cli": "command",
    }
    if explicit:
        if explicit not in aliases:
            raise ModelRunnerError(
                "AXM_MODEL_TRANSPORT must be ollama-native, "
                "openai-compatible, or command"
            )
        return aliases[explicit]
    if os.environ.get("AXM_MODEL_COMMAND"):
        return "command"
    candidate = (base_url or os.environ.get("AXM_MODEL_BASE_URL") or "").lower()
    return "ollama-native" if not candidate or "11434" in candidate else "openai-compatible"


def _endpoint(transport: str, base_url: str | None) -> str:
    base = (
        base_url
        or os.environ.get("AXM_MODEL_BASE_URL")
        or (DEFAULT_OLLAMA_URL if transport == "ollama-native" else "")
    ).rstrip("/")
    if transport == "command":
        return "command://local"
    if not base:
        raise ModelRunnerError("AXM_MODEL_BASE_URL is required for openai-compatible transport")
    if transport == "ollama-native":
        return base if base.endswith("/api/chat") else base + "/api/chat"
    return base if base.endswith("/chat/completions") else base + "/chat/completions"


def _http_json(
    endpoint: str,
    payload: Mapping[str, Any],
    *,
    timeout: int,
    api_key: str = "",
) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        endpoint,
        data=_canonical(payload),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise ModelRunnerError(f"model endpoint returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ModelRunnerError(f"model endpoint unavailable at {endpoint}: {exc}") from exc
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ModelRunnerError("model endpoint returned non-JSON output") from exc
    if not isinstance(value, dict):
        raise ModelRunnerError("model endpoint returned a non-object JSON envelope")
    return value


def _ollama_tags(base_url: str, timeout: int) -> list[str]:
    endpoint = base_url.rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(endpoint, timeout=min(timeout, 30)) as response:
            value = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise ModelRunnerError(
            "model='auto' requires AXM_MODEL_NAME or a reachable Ollama /api/tags"
        ) from exc
    names = [
        str(row.get("name", ""))
        for row in value.get("models", [])
        if isinstance(row, dict) and row.get("name")
    ]
    if not names:
        raise ModelRunnerError("Ollama reports no installed models")
    preferences = [
        item.strip()
        for item in os.environ.get("AXM_MODEL_PREFERENCE", "").split(",")
        if item.strip()
    ]
    for preference in preferences:
        exact = next((name for name in names if name == preference), None)
        if exact:
            return [exact]
        prefix = next((name for name in names if name.split(":", 1)[0] == preference), None)
        if prefix:
            return [prefix]
    return sorted(names)


def _resolve_model(requested: str, transport: str, base_url: str | None, timeout: int) -> str:
    if requested and requested.lower() not in {"auto", "default"}:
        return requested
    configured = os.environ.get("AXM_MODEL_NAME", "").strip()
    if configured:
        return configured
    if transport == "ollama-native":
        base = base_url or os.environ.get("AXM_MODEL_BASE_URL") or DEFAULT_OLLAMA_URL
        return _ollama_tags(base, timeout)[0]
    raise ModelRunnerError(
        "model='auto' is not a stable identity for command or "
        "openai-compatible transport; set AXM_MODEL_NAME or pass model="
    )


def _cache_root() -> Path | None:
    setting = os.environ.get("AXM_MODEL_CACHE", "").strip()
    if setting.lower() in {"off", "false", "0", "none"}:
        return None
    return Path(setting) if setting else DEFAULT_CACHE


def _cache_material(
    request: GenerationRequest,
    *,
    transport: str,
    endpoint: str,
    model: str,
) -> dict[str, Any]:
    return {
        "contract": CONTRACT_VERSION,
        "profile": request.profile,
        "purpose": request.purpose,
        "response_schema": request.response_schema,
        "transport": transport,
        "endpoint": endpoint,
        "model": model,
        "system_sha256": _sha256(request.system),
        "user_sha256": _sha256(request.user),
        "temperature": float(request.temperature),
        "seed": int(request.seed),
        "max_output_tokens": int(request.max_output_tokens),
    }


def _read_cache(root: Path | None, key: str) -> GenerationResult | None:
    if root is None:
        return None
    path = root / key[:2] / f"{key}.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    text = str(value.get("text", ""))
    if _sha256(text) != str(value.get("response_sha256", "")):
        return None
    receipt = value.get("receipt") if isinstance(value.get("receipt"), dict) else {}
    receipt = {**receipt, "cache_hit": True, "cache_read_at": _utc_now()}
    return GenerationResult(
        text=text,
        model=str(value.get("model", "")),
        transport=str(value.get("transport", "")),
        cache_key=key,
        receipt=receipt,
    )


def _write_cache(root: Path | None, result: GenerationResult) -> None:
    if root is None:
        return
    path = root / result.cache_key[:2] / f"{result.cache_key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {
        "text": result.text,
        "response_sha256": _sha256(result.text),
        "model": result.model,
        "transport": result.transport,
        "receipt": dict(result.receipt),
    }
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(_canonical(value) + b"\n")
    os.replace(temporary, path)


def _invoke_ollama(request: GenerationRequest, model: str, endpoint: str) -> tuple[str, str, dict[str, Any]]:
    try:
        num_ctx = max(4096, int(os.environ.get("AXM_MODEL_NUM_CTX", "8192")))
    except ValueError:
        num_ctx = 8192
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": request.system},
            {"role": "user", "content": request.user},
        ],
        "stream": False,
        "think": False,
        "options": {
            "temperature": float(request.temperature),
            "seed": int(request.seed),
            "num_ctx": num_ctx,
            "num_predict": int(request.max_output_tokens),
        },
    }
    body = _http_json(endpoint, payload, timeout=request.timeout)
    text = str((body.get("message") or {}).get("content", ""))
    if not text and body.get("done_reason") not in {"stop", None}:
        raise ModelRunnerError(f"Ollama returned no content: {body.get('done_reason')}")
    usage = {
        key: body[key]
        for key in ("prompt_eval_count", "eval_count", "total_duration")
        if key in body
    }
    return text, str(body.get("model") or model), usage


def _invoke_openai(request: GenerationRequest, model: str, endpoint: str) -> tuple[str, str, dict[str, Any]]:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": request.system},
            {"role": "user", "content": request.user},
        ],
        "stream": False,
        "temperature": float(request.temperature),
        "seed": int(request.seed),
        "max_tokens": int(request.max_output_tokens),
    }
    body = _http_json(
        endpoint,
        payload,
        timeout=request.timeout,
        api_key=os.environ.get("AXM_MODEL_API_KEY", ""),
    )
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ModelRunnerError("OpenAI-compatible response has no choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    text = str((message or {}).get("content", ""))
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    return text, str(body.get("model") or model), dict(usage)


def _invoke_command(request: GenerationRequest, model: str) -> tuple[str, str, dict[str, Any]]:
    command_text = os.environ.get("AXM_MODEL_COMMAND", "").strip()
    if not command_text:
        raise ModelRunnerError("AXM_MODEL_COMMAND is required for command transport")
    command = shlex.split(command_text, posix=os.name != "nt")
    if not command:
        raise ModelRunnerError("AXM_MODEL_COMMAND parsed to an empty command")
    envelope = {
        "schema": CONTRACT_VERSION,
        **asdict(request),
        "model": model,
    }
    try:
        completed = subprocess.run(
            command,
            input=_canonical(envelope),
            capture_output=True,
            timeout=request.timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ModelRunnerError(f"model command failed to execute: {exc}") from exc
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace")[:500]
        raise ModelRunnerError(
            f"model command exited {completed.returncode}: {stderr}"
        )
    stdout = completed.stdout.decode("utf-8", errors="strict").strip()
    try:
        value: Any = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout, model, {}
    if not isinstance(value, dict):
        return stdout, model, {}
    text = str(value.get("text") or value.get("content") or "")
    if not text and isinstance(value.get("message"), dict):
        text = str(value["message"].get("content", ""))
    actual = str(value.get("model") or model)
    usage = value.get("usage") if isinstance(value.get("usage"), dict) else {}
    return text, actual, dict(usage)



def describe_route(
    *,
    model: str = "auto",
    profile: str = DEFAULT_PROFILE,
    base_url: str | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """Resolve transport, endpoint, and actual model without generation."""
    transport = _transport(base_url)
    endpoint = _endpoint(transport, base_url)
    actual = _resolve_model(model, transport, base_url, timeout)
    return {
        "schema": CONTRACT_VERSION,
        "profile": profile,
        "transport": transport,
        "endpoint": endpoint,
        "model": actual,
    }

def generate(request: GenerationRequest) -> GenerationResult:
    transport = _transport(request.base_url)
    endpoint = _endpoint(transport, request.base_url)
    model = _resolve_model(request.model, transport, request.base_url, request.timeout)
    material = _cache_material(
        request,
        transport=transport,
        endpoint=endpoint,
        model=model,
    )
    cache_key = _sha256(_canonical(material))
    root = _cache_root()
    cached = _read_cache(root, cache_key)
    if cached is not None:
        return cached

    started_at = _utc_now()
    start = time.monotonic()
    if transport == "ollama-native":
        text, actual_model, usage = _invoke_ollama(request, model, endpoint)
    elif transport == "openai-compatible":
        text, actual_model, usage = _invoke_openai(request, model, endpoint)
    else:
        text, actual_model, usage = _invoke_command(request, model)
    duration_ms = round((time.monotonic() - start) * 1000, 3)
    response_sha256 = _sha256(text)
    receipt = {
        "schema": RECEIPT_VERSION,
        "request_digest": cache_key,
        "response_sha256": response_sha256,
        "profile": request.profile,
        "purpose": request.purpose,
        "response_schema": request.response_schema,
        "transport": transport,
        "endpoint": endpoint,
        "model_requested": request.model,
        "model_actual": actual_model,
        "temperature": float(request.temperature),
        "seed": int(request.seed),
        "max_output_tokens": int(request.max_output_tokens),
        "started_at": started_at,
        "duration_ms": duration_ms,
        "cache_hit": False,
        "usage": usage,
    }
    result = GenerationResult(
        text=text,
        model=actual_model,
        transport=transport,
        cache_key=cache_key,
        receipt=receipt,
    )
    _write_cache(root, result)
    return result


def generate_text(
    system: str,
    user: str,
    *,
    model: str = "auto",
    profile: str = DEFAULT_PROFILE,
    purpose: str,
    response_schema: str,
    base_url: str | None = None,
    timeout: int = 180,
    max_output_tokens: int = 2048,
    temperature: float = 0.0,
    seed: int = 0,
) -> dict[str, Any]:
    """Stable function-level contract consumed by AXM spokes."""
    return generate(
        GenerationRequest(
            system=system,
            user=user,
            model=model,
            profile=profile,
            purpose=purpose,
            response_schema=response_schema,
            base_url=base_url,
            timeout=timeout,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            seed=seed,
        )
    ).to_dict()


__all__ = [
    "GenerationRequest",
    "GenerationResult",
    "ModelRunnerError",
    "describe_route",
    "generate",
    "generate_text",
]
