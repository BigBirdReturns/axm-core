"""Read-only export sources. S3 is the dataset-BYTE interface only.

Neither adapter exposes any write/put/delete/upload method: there is no code
path that writes back to Palantir or any source endpoint. Credentials for the
S3-compatible adapter come from the environment, never from committed code.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Protocol, runtime_checkable


@runtime_checkable
class ExportSource(Protocol):
    """A read-only byte source for dataset objects."""

    def read_bytes(self, object_path: str) -> bytes: ...

    def list_objects(self, prefix: str = "") -> List[str]: ...


class FilesystemExportSource:
    """Fixture / local adapter: dataset bytes from a local directory tree."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def read_bytes(self, object_path: str) -> bytes:
        return (self._root / object_path).read_bytes()

    def list_objects(self, prefix: str = "") -> List[str]:
        base = self._root / prefix
        return sorted(
            str(p.relative_to(self._root)) for p in base.rglob("*") if p.is_file()
        )


class ExportPermissionError(RuntimeError):
    """The export surface denied read access to a prefix or object (S3
    ``AccessDenied`` and friends). Surfaced so the probe records a permission
    gap instead of silently widening or silently dropping evidence."""


@dataclass(frozen=True)
class S3Config:
    endpoint_url: str
    bucket: str
    prefix: str = ""
    region: Optional[str] = None
    # Credentials are read from the environment at call time, never stored in
    # code and never committed.
    access_key_env: str = "AXM_S3_ACCESS_KEY"
    secret_key_env: str = "AXM_S3_SECRET_KEY"


# S3 error codes that mean "you are not allowed to read this" (vs. "not found").
_PERMISSION_CODES = frozenset({"AccessDenied", "AllAccessDisabled", "Forbidden", "403"})


def _error_code(exc: Exception) -> Optional[str]:
    """Pull the S3/botocore error code out of a ClientError-shaped exception,
    without importing botocore (the sim raises the same shape)."""
    resp = getattr(exc, "response", None)
    if isinstance(resp, dict):
        return (resp.get("Error") or {}).get("Code")
    return None


class S3ExportSource:
    """S3-compatible adapter (boto3, imported lazily). Read-only: it uses only
    ``list_objects_v2`` / ``get_object`` / ``head_object`` — no write path.

    ``list_objects`` follows the continuation token to completion, so a dataset
    larger than one page (S3 caps a page at 1000 keys) is listed in full rather
    than silently truncated. A pre-built ``client`` may be injected (for a
    faithful in-process simulation, or a caller-configured session) instead of
    the default lazy boto3 client; the read-only code path is identical either
    way.
    """

    def __init__(self, config: S3Config, *, client=None) -> None:
        self._cfg = config
        self._client = client

    def _client_lazy(self):
        if self._client is None:
            import boto3  # lazy: only needed for a live/boto3-backed endpoint

            self._client = boto3.client(
                "s3",
                endpoint_url=self._cfg.endpoint_url,
                region_name=self._cfg.region,
                aws_access_key_id=os.environ.get(self._cfg.access_key_env),
                aws_secret_access_key=os.environ.get(self._cfg.secret_key_env),
            )
        return self._client

    def _key(self, object_path: str) -> str:
        return f"{self._cfg.prefix}{object_path}" if self._cfg.prefix else object_path

    def read_bytes(self, object_path: str) -> bytes:
        try:
            resp = self._client_lazy().get_object(Bucket=self._cfg.bucket, Key=self._key(object_path))
        except Exception as exc:  # translate a permission denial into our type
            code = _error_code(exc)
            if code in _PERMISSION_CODES:
                raise ExportPermissionError(f"{object_path}: {code}") from exc
            raise
        return resp["Body"].read()

    def object_metadata(self, object_path: str) -> dict:
        """Read-only object metadata (version id, security markings, size) via
        ``head_object``. Markings are recorded for provenance only — they are
        never made portable (importing an export re-creates no access control)."""
        try:
            resp = self._client_lazy().head_object(Bucket=self._cfg.bucket, Key=self._key(object_path))
        except Exception as exc:
            code = _error_code(exc)
            if code in _PERMISSION_CODES:
                raise ExportPermissionError(f"{object_path}: {code}") from exc
            raise
        meta = resp.get("Metadata") or {}
        return {
            "version_id": resp.get("VersionId"),
            "size_bytes": resp.get("ContentLength"),
            "markings": [meta[k] for k in sorted(meta) if "marking" in k.lower()],
        }

    def list_objects(self, prefix: str = "") -> List[str]:
        """List every object under the prefix, following continuation tokens to
        completion. A single ``list_objects_v2`` call caps at 1000 keys and sets
        ``IsTruncated``; ignoring the token silently drops evidence, so we page."""
        client = self._client_lazy()
        keys: List[str] = []
        token: Optional[str] = None
        try:
            while True:
                kwargs = {"Bucket": self._cfg.bucket, "Prefix": self._key(prefix)}
                if token is not None:
                    kwargs["ContinuationToken"] = token
                resp = client.list_objects_v2(**kwargs)
                keys.extend(o["Key"] for o in resp.get("Contents", []))
                if not resp.get("IsTruncated"):
                    break
                token = resp.get("NextContinuationToken")
                if not token:  # truncated but no token: refuse to guess, stop honestly
                    break
        except Exception as exc:
            code = _error_code(exc)
            if code in _PERMISSION_CODES:
                raise ExportPermissionError(f"{prefix!r}: {code}") from exc
            raise
        return sorted(keys)
