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
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from axm_forge import model_cache_scope as _scope
from axm_forge.model_cache_scope import CacheScopeError  # re-exported

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
    num_ctx: int | None = None
    temperature: float = 0.0
    seed: int = 0
    # Opaque caller-owned cache placement scope. All-or-neither; empty values
    # preserve the unscoped contract exactly. Core never interprets them, and
    # they never reach a provider: a semantic plan key is local operational
    # metadata, not information the model needs.
    cache_namespace: str = ""
    cache_scope: str = ""


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


def _body_free_usage(value: Any) -> Any:
    """Retain numeric accounting while refusing provider-supplied text bodies."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, Mapping):
        return {
            str(key): sanitized
            for key, item in value.items()
            if (sanitized := _body_free_usage(item)) is not None
        }
    if isinstance(value, (list, tuple)):
        return [
            sanitized
            for item in value
            if (sanitized := _body_free_usage(item)) is not None
        ]
    return None


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


def _route_identity(transport: str, endpoint: str) -> str:
    """Return a body-free identity for the selected invocation route.

    HTTP endpoints are already part of the cache material. The additional
    operator-supplied route identity can bind a deployment or mutable alias.
    Command routes have no useful endpoint, so their exact command text is
    hashed to prevent two different wrappers from sharing a cache key.
    """
    declared = os.environ.get("AXM_MODEL_ROUTE_ID", "").strip()
    if declared:
        return declared
    if transport == "command":
        command = os.environ.get("AXM_MODEL_COMMAND", "").strip()
        if not command:
            raise ModelRunnerError("AXM_MODEL_COMMAND is required for command transport")
        return "command-sha256:" + _sha256(command)
    return f"{transport}-endpoint-sha256:" + _sha256(endpoint)


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


def _resolve_num_ctx(value: int | None) -> int:
    """Resolve the effective context window and fail closed on bad config."""
    raw: object = (
        os.environ.get("AXM_MODEL_NUM_CTX", "8192")
        if value is None
        else value
    )
    try:
        resolved = int(raw)
    except (TypeError, ValueError) as exc:
        raise ModelRunnerError("AXM_MODEL_NUM_CTX must be an integer") from exc
    return max(4096, resolved)


def _cache_material(
    request: GenerationRequest,
    *,
    transport: str,
    endpoint: str,
    route_identity: str,
    model: str,
) -> dict[str, Any]:
    return {
        "contract": CONTRACT_VERSION,
        "profile": request.profile,
        "purpose": request.purpose,
        "response_schema": request.response_schema,
        "transport": transport,
        "endpoint_sha256": _sha256(endpoint),
        "route_identity": route_identity,
        "model": model,
        "system_sha256": _sha256(request.system),
        "user_sha256": _sha256(request.user),
        "temperature": float(request.temperature),
        "seed": int(request.seed),
        "max_output_tokens": int(request.max_output_tokens),
        "num_ctx": int(request.num_ctx),
    }


def _read_cache(
    root: Path | None,
    key: str,
    path: Path | None = None,
    expected_request_digest: str | None = None,
) -> GenerationResult | None:
    if root is None:
        return None
    if path is None:
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
    if str(receipt.get("cache_key") or "") != key:
        return None
    stored_request_digest = str(receipt.get("request_digest") or "")
    if not stored_request_digest:
        return None
    # The semantic identity is verified explicitly. It no longer equals the
    # cache key once scopes exist, so it cannot be checked by placement alone.
    if expected_request_digest is not None and stored_request_digest != expected_request_digest:
        return None
    if str(receipt.get("response_sha256") or "") != _sha256(text):
        return None
    if receipt.get("cacheable") is not True:
        return None
    if receipt.get("model_identity_match") is not True:
        return None
    if str(receipt.get("model_actual") or "") != str(value.get("model") or ""):
        return None
    if str(receipt.get("transport") or "") != str(value.get("transport") or ""):
        return None
    receipt = {**receipt, "cache_hit": True, "cache_read_at": _utc_now()}
    return GenerationResult(
        text=text,
        model=str(value.get("model", "")),
        transport=str(value.get("transport", "")),
        cache_key=key,
        receipt=receipt,
    )


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _write_cache(
    root: Path | None,
    result: GenerationResult,
    path: Path | None = None,
) -> None:
    if root is None:
        return
    if path is None:
        path = root / result.cache_key[:2] / f"{result.cache_key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {
        "text": result.text,
        "response_sha256": _sha256(result.text),
        "model": result.model,
        "transport": result.transport,
        "receipt": dict(result.receipt),
    }
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=".t",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(_canonical(value) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _invoke_ollama(request: GenerationRequest, model: str, endpoint: str) -> tuple[str, str, dict[str, Any]]:
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
            "num_ctx": int(request.num_ctx),
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
    if os.name == "nt":
        # posix=False keeps the surrounding quotes inside each token, and
        # Windows cannot exec an argv[0] that literally begins with a quote.
        command = [
            token[1:-1]
            if len(token) >= 2 and token[0] == token[-1] == '"'
            else token
            for token in command
        ]
    if not command:
        raise ModelRunnerError("AXM_MODEL_COMMAND parsed to an empty command")
    payload = asdict(request)
    # Cache placement is local operational metadata. It must not reach the
    # adapter's stdin envelope or any provider request.
    payload.pop("cache_namespace", None)
    payload.pop("cache_scope", None)
    envelope = {
        "schema": CONTRACT_VERSION,
        **payload,
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
    num_ctx: int | None = None,
) -> dict[str, Any]:
    """Resolve a body-free route identity and actual model without generation."""
    transport = _transport(base_url)
    endpoint = _endpoint(transport, base_url)
    route_identity = _route_identity(transport, endpoint)
    actual = _resolve_model(model, transport, base_url, timeout)
    return {
        "schema": CONTRACT_VERSION,
        "profile": profile,
        "transport": transport,
        "endpoint_sha256": _sha256(endpoint),
        "route_identity": route_identity,
        "model": actual,
        "num_ctx": _resolve_num_ctx(num_ctx),
    }

def generate(request: GenerationRequest) -> GenerationResult:
    request = replace(request, num_ctx=_resolve_num_ctx(request.num_ctx))
    transport = _transport(request.base_url)
    endpoint = _endpoint(transport, request.base_url)
    route_identity = _route_identity(transport, endpoint)
    model = _resolve_model(request.model, transport, request.base_url, request.timeout)
    material = _cache_material(
        request,
        transport=transport,
        endpoint=endpoint,
        route_identity=route_identity,
        model=model,
    )
    request_digest = _sha256(_canonical(material))
    root = _cache_root()

    namespace, scope = _scope.normalise(request.cache_namespace, request.cache_scope)
    cache_key = _scope.derive_cache_key(request_digest, namespace, scope)
    cached = _read_cache(root, cache_key, None, request_digest)
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
    usage = _body_free_usage(usage) or {}
    model_identity_match = actual_model == model
    receipt = {
        "schema": RECEIPT_VERSION,
        # Semantic request identity: stable across scope invalidation.
        "request_digest": request_digest,
        # Physical placement identity: moves when the scope epoch advances.
        "cache_key": cache_key,
        "cache_namespace": namespace,
        "cache_scope": scope,
        "cache_epoch": 0,
        "response_sha256": response_sha256,
        "profile": request.profile,
        "purpose": request.purpose,
        "response_schema": request.response_schema,
        "transport": transport,
        "endpoint_sha256": _sha256(endpoint),
        "route_identity": route_identity,
        "model_requested": request.model,
        "model_actual": actual_model,
        "model_identity_match": model_identity_match,
        "temperature": float(request.temperature),
        "seed": int(request.seed),
        "max_output_tokens": int(request.max_output_tokens),
        "num_ctx": int(request.num_ctx),
        "started_at": started_at,
        "duration_ms": duration_ms,
        "cache_hit": False,
        "cacheable": model_identity_match,
        "usage": usage,
    }
    result = GenerationResult(
        text=text,
        model=actual_model,
        transport=transport,
        cache_key=cache_key,
        receipt=receipt,
    )
    if not model_identity_match:
        receipt["cache_write_outcome"] = "REFUSED"
        receipt["cache_write_reason"] = "model_identity_drift"
        return result
    _write_cache(root, result)
    receipt["cache_write_outcome"] = "WRITTEN"
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
    num_ctx: int | None = None,
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
            num_ctx=_resolve_num_ctx(num_ctx),
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
