"""Cache placement identity for the AXM Core model runner.

A caller may supply an opaque ``(cache_namespace, cache_scope)`` pair. Core
governs it mechanically and never interprets it.

Two identities are kept distinct:

``request_digest``
    The semantic request identity: model, prompts, schema, purpose, route, and
    decoding controls.

``cache_key``
    The physical placement identity. For unscoped callers the two are equal, so
    every existing cache coordinate keeps working unchanged.

Keeping them separate is what allows a later invalidation generation to retire
objects without making the same semantic request look like a different model
request.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


class CacheScopeError(RuntimeError):
    """A scope operation was refused. Nothing was mutated."""


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



def derive_cache_key(request_digest: str, namespace: str, scope: str, epoch: int = 0) -> str:
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


__all__ = ["CacheScopeError", "derive_cache_key", "normalise"]
