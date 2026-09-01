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
INVALIDATION_SCHEMA = "axm-core/model-cache-scope-invalidation@2"
CLEANUP_SCHEMA = "axm-core/model-cache-scope-cleanup@1"
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


def _normalised_cache_root(root: Path) -> str:
    """Return a platform-canonical cache-store coordinate.

    The coordinate itself is never exposed in receipts. Windows case folding and
    path normalization ensure aliases of the same local store share one identity.
    """
    resolved = Path(root).expanduser().resolve(strict=False)
    return os.path.normcase(os.path.normpath(str(resolved)))


def cache_store_sha256(root: Path) -> str:
    """Body-free identity of the resolved cache root."""
    return _sha256(_normalised_cache_root(root))


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


def _iter_objects(directory: Path):
    if not directory.is_dir():
        return
    for path in sorted(directory.rglob("*.json")):
        if path.is_file():
            yield path


def _verify_object(path: Path, state: dict, epoch: int) -> dict:
    """Full-object verification. Any contradiction refuses the operation."""
    cache_key = path.stem
    problems: list[str] = []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"cache_key": cache_key, "path": str(path), "problems": [f"unreadable: {exc}"]}
    if not isinstance(value, dict):
        return {"cache_key": cache_key, "path": str(path), "problems": ["not an object"]}

    receipt = value.get("receipt") if isinstance(value.get("receipt"), dict) else {}
    text = str(value.get("text", ""))

    if path.parent.name != cache_key[:2]:
        problems.append("path digest does not match cache_key")
    if str(receipt.get("cache_key") or "") != cache_key:
        problems.append("receipt cache_key does not match object")
    if not str(receipt.get("request_digest") or ""):
        problems.append("request digest missing")
    if _sha256(text) != str(value.get("response_sha256") or ""):
        problems.append("response digest does not match response text")
    if str(receipt.get("cache_namespace") or "") != state["cache_namespace"]:
        problems.append("namespace does not match state")
    if str(receipt.get("cache_scope") or "") != state["cache_scope"]:
        problems.append("scope does not match state")
    if int(receipt.get("cache_epoch", -1)) != int(epoch):
        problems.append("epoch does not match current state")
    if receipt.get("model_identity_match") is not True:
        problems.append("model identity did not match when cached")
    if receipt.get("cacheable") is not True:
        problems.append("object was not marked cacheable")

    return {
        "cache_key": cache_key,
        "path": str(path),
        "bytes": path.stat().st_size if path.is_file() else 0,
        "request_digest": str(receipt.get("request_digest") or ""),
        "response_sha256": str(receipt.get("response_sha256") or ""),
        "model_actual": str(receipt.get("model_actual") or ""),
        "route_identity": str(receipt.get("route_identity") or ""),
        "problems": problems,
    }


def inspect_cache_scope(namespace: str, scope: str, *, root: Path | None = None) -> Mapping[str, Any]:
    """Report a scope without returning any model body."""
    from axm_forge.model_runner import _cache_root  # local import avoids a cycle

    namespace, scope = normalise(namespace, scope)
    if not namespace:
        raise CacheScopeError("inspect_cache_scope requires a namespace and scope")
    base = root if root is not None else _cache_root()
    if base is None:
        raise CacheScopeError("model cache is disabled (AXM_MODEL_CACHE)")

    state = read_state(base, namespace, scope)
    epoch = int(state["current_epoch"])
    entries = [_verify_object(path, state, epoch)
               for path in _iter_objects(epoch_dir(base, namespace, scope, epoch))]
    return {
        "schema": INSPECTION_SCHEMA,
        "cache_store_sha256": cache_store_sha256(base),
        "cache_namespace": namespace,
        "cache_scope": scope,
        "current_epoch": epoch,
        "entry_count": len(entries),
        "stored_bytes": sum(int(row.get("bytes") or 0) for row in entries),
        "verified_count": sum(1 for row in entries if not row["problems"]),
        "refused_count": sum(1 for row in entries if row["problems"]),
        "entries": [
            {
                "cache_key": row["cache_key"],
                "request_digest": row.get("request_digest", ""),
                "response_sha256": row.get("response_sha256", ""),
                "model_actual": row.get("model_actual", ""),
                "route_identity": row.get("route_identity", ""),
                "problems": row["problems"],
            }
            for row in entries
        ],
        "last_invalidation_receipt_sha256": state.get("last_invalidation_receipt_sha256", ""),
        "state_sha256": state.get("state_sha256", ""),
        "state_persisted": _state_path(base, namespace, scope).is_file(),
    }


def _seal_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    """Return a receipt whose digest covers every persisted claim."""
    body = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    receipt["receipt_sha256"] = _sha256(_canonical(body))
    return receipt


def _receipt_path(
    root: Path,
    namespace: str,
    scope: str,
    invalidation_id: str,
    kind: str,
) -> Path:
    return (
        scope_dir(root, namespace, scope)
        / "receipts"
        / f"{invalidation_id}.{kind}.json"
    )


def _directory_bytes(path: Path) -> int:
    total = 0
    if not path.is_dir():
        return total
    for item in path.rglob("*"):
        if item.is_file():
            try:
                total += item.stat().st_size
            except OSError:
                pass
    return total


def invalidate_cache_scope(
    namespace: str,
    scope: str,
    *,
    reason: str,
    dry_run: bool = False,
    root: Path | None = None,
) -> Mapping[str, Any]:
    """Retire one exact scope generation and receipt cleanup separately.

    Logical invalidation is complete when the active state advances to the next
    epoch. Physical deletion of the retired generation is a later storage
    outcome and receives its own chained receipt. A cleanup failure therefore
    cannot make the retired generation readable again or rewrite the logical
    invalidation receipt after its digest has been published.
    """
    from axm_forge.model_runner import _cache_root

    namespace, scope = normalise(namespace, scope)
    if not namespace:
        raise CacheScopeError("invalidate_cache_scope requires a namespace and scope")
    reason = str(reason or "").strip()
    if not reason:
        raise CacheScopeError("invalidate_cache_scope requires a reason")
    base = root if root is not None else _cache_root()
    if base is None:
        raise CacheScopeError("model cache is disabled (AXM_MODEL_CACHE)")
    base = Path(base)
    store_sha256 = cache_store_sha256(base)

    retired: Path | None = None
    retired_relative = ""
    total_bytes = 0
    logical: dict[str, Any]
    logical_path: Path | None = None

    with _ScopeLock(base, namespace, scope):
        state = read_state(base, namespace, scope)
        epoch = int(state["current_epoch"])
        current = epoch_dir(base, namespace, scope, epoch)
        entries = [_verify_object(path, state, epoch) for path in _iter_objects(current)]
        refused = [row for row in entries if row["problems"]]
        if refused:
            raise CacheScopeError(
                "cache scope contains contradictory or corrupt entries; "
                f"refusing to invalidate: {json.dumps(refused[:3])[:400]}"
            )

        total_bytes = sum(int(row.get("bytes") or 0) for row in entries)
        request_digests = sorted(
            {row["request_digest"] for row in entries if row["request_digest"]}
        )
        observed_at = _utc_now()
        preview = {
            "schema": INVALIDATION_SCHEMA,
            "cache_store_sha256": store_sha256,
            "dry_run": bool(dry_run),
            "cache_namespace": namespace,
            "cache_scope": scope,
            "epoch_before": epoch,
            "epoch_after": epoch if dry_run else epoch + 1,
            "entry_count": len(entries),
            "verified_count": len(entries),
            "refused_count": 0,
            "retired_bytes": 0 if dry_run else total_bytes,
            # Kept for callers of the @1 preview shape. Logical invalidation
            # never claims physical deletion.
            "deleted_bytes": 0,
            "request_digests": request_digests,
            "prior_scope_receipt_sha256": state.get(
                "last_invalidation_receipt_sha256", ""
            ),
            "reason": reason,
            "observed_at": observed_at,
        }
        if dry_run:
            return preview

        invalidation_id = _sha256(
            _canonical(
                {
                    "scope": scope,
                    "namespace": namespace,
                    "epoch": epoch,
                    "at": observed_at,
                    "reason": reason,
                }
            )
        )[:32]
        retired = scope_dir(base, namespace, scope) / "retired" / invalidation_id
        retired_relative = f"retired/{invalidation_id}"
        moved = False
        if current.is_dir():
            retired.parent.mkdir(parents=True, exist_ok=True)
            os.replace(current, retired)
            moved = True

        logical = _seal_receipt(
            {
                **preview,
                "invalidation_id": invalidation_id,
                "retired_generation": retired_relative if moved else "",
            }
        )
        logical_path = _receipt_path(
            base, namespace, scope, invalidation_id, "invalidation"
        )
        try:
            # Receipt first is fail-safe: a crash may leave an unreferenced
            # receipt, but never a state transition whose authority receipt is
            # missing. The state points to it only after both bytes exist.
            _atomic_write(logical_path, _canonical(logical) + b"\n")
            new_state = _seal_state(
                {
                    "schema": STATE_SCHEMA,
                    "cache_namespace": namespace,
                    "cache_scope": scope,
                    "namespace_sha256": _sha256(namespace),
                    "scope_sha256": _sha256(scope),
                    "current_epoch": epoch + 1,
                    "previous_state_sha256": state.get("state_sha256", ""),
                    "last_invalidation_receipt_sha256": logical[
                        "receipt_sha256"
                    ],
                }
            )
            _atomic_write(
                _state_path(base, namespace, scope),
                _canonical(new_state) + b"\n",
            )
        except Exception:
            if logical_path is not None:
                logical_path.unlink(missing_ok=True)
            if moved and retired is not None:
                os.replace(retired, current)
            raise

    # The scope lock is deliberately released before physical cleanup. The
    # active state already points at the next generation, so a slow filesystem
    # deletion cannot block readers or writers of the new epoch.
    physically_deleted = True
    deleted_bytes = 0
    inaccessible_residue = ""
    cleanup_error_type = ""
    if retired is not None and retired.is_dir():
        try:
            shutil.rmtree(retired)
            deleted_bytes = total_bytes
        except OSError as exc:
            physically_deleted = False
            remaining = _directory_bytes(retired)
            deleted_bytes = max(0, total_bytes - remaining)
            inaccessible_residue = retired_relative
            cleanup_error_type = type(exc).__name__

    cleanup = _seal_receipt(
        {
            "schema": CLEANUP_SCHEMA,
            "invalidation_receipt_sha256": logical["receipt_sha256"],
            "cache_store_sha256": store_sha256,
            "cache_namespace": namespace,
            "cache_scope": scope,
            "invalidation_id": logical["invalidation_id"],
            "physically_deleted": physically_deleted,
            "deleted_bytes": deleted_bytes,
            "inaccessible_residue": inaccessible_residue,
            "cleanup_error_type": cleanup_error_type,
            "observed_at": _utc_now(),
        }
    )
    cleanup_path = _receipt_path(
        base,
        namespace,
        scope,
        logical["invalidation_id"],
        "cleanup",
    )
    cleanup_persisted = True
    cleanup_write_error_type = ""
    try:
        _atomic_write(cleanup_path, _canonical(cleanup) + b"\n")
    except OSError as exc:
        # The logical invalidation is already authoritative and cannot be
        # rolled back. Surface the missing storage witness explicitly so a
        # caller cannot mistake the return for full receipt closure.
        cleanup_persisted = False
        cleanup_write_error_type = type(exc).__name__

    return {
        **logical,
        "cleanup_receipt": cleanup if cleanup_persisted else {},
        "cleanup_receipt_sha256": (
            cleanup["receipt_sha256"] if cleanup_persisted else ""
        ),
        "cleanup_receipt_persisted": cleanup_persisted,
        "cleanup_write_error_type": cleanup_write_error_type,
        # Compatibility conveniences are copied from the separately sealed
        # cleanup receipt; they are not part of the logical receipt digest.
        "physically_deleted": physically_deleted,
        "deleted_bytes": deleted_bytes,
        "inaccessible_residue": inaccessible_residue,
    }


__all__ = [
    "CLEANUP_SCHEMA",
    "CacheScopeError",
    "cache_store_sha256",
    "derive_cache_key",
    "epoch_dir",
    "inspect_cache_scope",
    "invalidate_cache_scope",
    "normalise",
    "object_path",
    "read_state",
    "scope_dir",
]
