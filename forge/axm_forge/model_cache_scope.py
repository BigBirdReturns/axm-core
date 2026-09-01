"""Opaque, fenced cache scopes for the AXM Core model runner.

A caller may supply an opaque ``(cache_namespace, cache_scope)`` pair. Core
governs it mechanically and never interprets it: the namespace and scope are
strings a spoke owns the meaning of. Core owns only the cache objects, their
placement, their fencing generation, and the invalidation receipt.

Two identities are kept distinct:

``request_digest``
    The semantic request identity: model, prompts, schema, purpose, route, and
    decoding controls. It does not move when a scope is invalidated.

``cache_key``
    The physical placement identity: ``request_digest`` combined with the
    namespace, scope, and current epoch. Advancing the epoch therefore retires
    a generation of objects without making the same semantic request look like
    a different request.

Layout under the cache root::

    scopes/<namespace-sha256>/<scope-sha256>/
        state.json
        epochs/<08d epoch>/<key[:2]>/<cache_key>.json
        receipts/<invalidation-id>.json
    locks/<namespace-sha256>.<scope-sha256>.lock

Paths use digests so an arbitrary caller-supplied string can never traverse the
filesystem or produce a platform-invalid name; ``state.json`` retains the exact
namespace and scope so a digest collision is detectable rather than silent.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

STATE_SCHEMA = "axm-core/model-cache-scope-state@1"
INVALIDATION_SCHEMA = "axm-core/model-cache-scope-invalidation@1"
INSPECTION_SCHEMA = "axm-core/model-cache-scope-inspection@1"

_LOCK_TIMEOUT_SECONDS = 30.0
_LOCK_STALE_SECONDS = 120.0
_LOCK_POLL_SECONDS = 0.02


class CacheScopeError(RuntimeError):
    """A scope operation was refused. Nothing was mutated."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(value: str | bytes) -> str:
    data = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def normalise(namespace: str, scope: str) -> tuple[str, str]:
    """Validate the all-or-neither rule and return the exact pair."""
    namespace = str(namespace or "")
    scope = str(scope or "")
    if bool(namespace) != bool(scope):
        raise CacheScopeError(
            "cache_namespace and cache_scope must be supplied together or not at all"
        )
    return namespace, scope


# Directory components are a digest PREFIX, not the full digest. Two 64-char
# components plus a deep cache root exceed the Windows MAX_PATH limit of 260
# characters and fail with WinError 206. 64 bits is ample for placement, and a
# collision is not silent: state.json retains the exact namespace and scope and
# read_state() refuses a mismatch.
_DIR_TOKEN_CHARS = 16


def _token(value: str) -> str:
    return _sha256(value)[:_DIR_TOKEN_CHARS]


def scope_dir(root: Path, namespace: str, scope: str) -> Path:
    return root / "scopes" / _token(namespace) / _token(scope)


def _lock_path(root: Path, namespace: str, scope: str) -> Path:
    return root / "locks" / f"{_token(namespace)}.{_token(scope)}.lock"


class _ScopeLock:
    """Short-held exclusive lock. Never held across a model call."""

    def __init__(self, root: Path, namespace: str, scope: str) -> None:
        self.path = _lock_path(root, namespace, scope)
        self._fd: int | None = None

    def __enter__(self) -> "_ScopeLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
        while True:
            try:
                self._fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.write(self._fd, str(os.getpid()).encode("ascii"))
                return self
            except FileExistsError:
                try:
                    age = time.time() - self.path.stat().st_mtime
                except OSError:
                    age = 0.0
                if age > _LOCK_STALE_SECONDS:
                    # The holder died. Break it rather than deadlock forever.
                    self.path.unlink(missing_ok=True)
                    continue
                if time.monotonic() > deadline:
                    raise CacheScopeError(f"timed out acquiring cache scope lock: {self.path}")
                time.sleep(_LOCK_POLL_SECONDS)

    def __exit__(self, *_exc: Any) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        self.path.unlink(missing_ok=True)


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=".t", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _seal_state(state: dict) -> dict:
    body = {key: value for key, value in state.items() if key != "state_sha256"}
    state["state_sha256"] = _sha256(_canonical(body))
    return state


def _state_path(root: Path, namespace: str, scope: str) -> Path:
    return scope_dir(root, namespace, scope) / "state.json"


def read_state(root: Path, namespace: str, scope: str) -> dict:
    """Return the scope state, creating the initial epoch-0 state if absent."""
    path = _state_path(root, namespace, scope)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = None
    if not isinstance(state, dict):
        return _seal_state(
            {
                "schema": STATE_SCHEMA,
                "cache_namespace": namespace,
                "cache_scope": scope,
                "namespace_sha256": _sha256(namespace),
                "scope_sha256": _sha256(scope),
                "current_epoch": 0,
                "previous_state_sha256": "",
                "last_invalidation_receipt_sha256": "",
            }
        )
    body = {key: value for key, value in state.items() if key != "state_sha256"}
    if _sha256(_canonical(body)) != str(state.get("state_sha256") or ""):
        raise CacheScopeError(f"cache scope state is corrupt: {path}")
    # A digest collision must be detectable, not silent.
    if state.get("cache_namespace") != namespace or state.get("cache_scope") != scope:
        raise CacheScopeError(
            "cache scope state does not match the requested namespace/scope"
        )
    return state


def epoch_dir(root: Path, namespace: str, scope: str, epoch: int) -> Path:
    return scope_dir(root, namespace, scope) / "epochs" / f"{int(epoch):08d}"


def object_path(root: Path, namespace: str, scope: str, epoch: int, cache_key: str) -> Path:
    return epoch_dir(root, namespace, scope, epoch) / cache_key[:2] / f"{cache_key}.json"


def derive_cache_key(request_digest: str, namespace: str, scope: str, epoch: int) -> str:
    """Physical placement identity. Unscoped callers keep request_digest."""
    if not namespace and not scope:
        return request_digest
    return _sha256(
        _canonical(
            {
                "request_digest": request_digest,
                "cache_namespace": namespace,
                "cache_scope": scope,
                "cache_epoch": int(epoch),
            }
        )
    )


__all__ = [
    "CacheScopeError",
    "derive_cache_key",
    "epoch_dir",
    "normalise",
    "object_path",
    "read_state",
    "scope_dir",
]
