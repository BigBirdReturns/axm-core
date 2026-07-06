"""High-fidelity in-process simulation of a Foundry S3-compatible export surface.

Doctrine: authorized live extraction is NOT gated on credentials. What the real
surface *does* is known; the honest move is to simulate it at high fidelity,
label the result a simulation, and prove the adapter against it. The evidence
tier stays truthful (``sim-foundry-s3``, never "authorized live Foundry"); the
credential nag is gone.

This models the behaviors a real S3-compatible Foundry export surface exposes and
that a naive adapter gets wrong — the exact gaps the earlier probe report could
only *record*:

  - **Pagination.** ``list_objects_v2`` returns at most ``page_size`` keys per
    call (S3 caps a page at 1000) and signals more via ``IsTruncated`` +
    ``NextContinuationToken``. A single call silently truncates a large dataset.
  - **Versioning.** An object can carry multiple versions; ``get_object`` /
    ``head_object`` return the latest unless a ``VersionId`` is given.
  - **Security markings.** Objects carry ``Metadata`` markings (e.g. classified /
    export-controlled). They are recorded for provenance, never made portable.
  - **Permission denial.** Unauthorized prefixes/objects raise a ClientError-
    shaped ``AccessDenied`` (same shape botocore raises), so the adapter can
    translate it and the probe can record a permission gap.

It is a faithful *stand-in for the client*, so the REAL ``S3ExportSource`` code
path (list/get/head) runs against it unchanged — no boto3, no moto, no network.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

SURFACE_CLASS = "sim-foundry-s3 (high-fidelity simulation, NOT authorized live Foundry)"


class SimClientError(Exception):
    """A ClientError-shaped exception. ``response['Error']['Code']`` mirrors
    botocore so :class:`~foundry_exit.adapters.S3ExportSource` translates it
    exactly as it would a real one."""

    def __init__(self, code: str, message: str = "") -> None:
        self.response = {"Error": {"Code": code, "Message": message}}
        super().__init__(f"{code}: {message}")


class _Body:
    """Minimal stand-in for a boto3 StreamingBody."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


@dataclass
class _Version:
    version_id: str
    body: bytes
    marking: Optional[str] = None


@dataclass
class FoundryS3Sim:
    """A high-fidelity fake S3 client for one bucket. Populate with :meth:`put`,
    then hand it to ``S3ExportSource(cfg, client=sim)``."""

    bucket: str
    page_size: int = 1000
    denied_prefixes: Tuple[str, ...] = ()
    _objects: Dict[str, List[_Version]] = field(default_factory=dict)
    _tokens: Dict[str, int] = field(default_factory=dict)

    # -- population (test/sim side, not part of the client API) --------------

    def put(self, key: str, body: bytes, *, marking: Optional[str] = None) -> str:
        """Add (or add a new version of) an object. Returns the version id."""
        versions = self._objects.setdefault(key, [])
        version_id = f"v{len(versions) + 1}"
        versions.append(_Version(version_id, body, marking))
        return version_id

    def deny(self, *prefixes: str) -> "FoundryS3Sim":
        self.denied_prefixes = self.denied_prefixes + tuple(prefixes)
        return self

    # -- the S3 client surface the adapter actually calls --------------------

    def _guard(self, key_or_prefix: str) -> None:
        for denied in self.denied_prefixes:
            if key_or_prefix.startswith(denied):
                raise SimClientError("AccessDenied", f"no read grant for {denied!r}")

    def list_objects_v2(
        self,
        Bucket: str,
        Prefix: str = "",
        ContinuationToken: Optional[str] = None,
        MaxKeys: Optional[int] = None,
        **_ignored,
    ) -> dict:
        if Bucket != self.bucket:
            raise SimClientError("NoSuchBucket", Bucket)
        self._guard(Prefix)
        keys = sorted(k for k in self._objects if k.startswith(Prefix))
        # Real S3 never returns more than page_size (<=1000) per call, whatever
        # MaxKeys asks for.
        limit = min(MaxKeys, self.page_size) if MaxKeys else self.page_size
        start = self._tokens[ContinuationToken] if ContinuationToken is not None else 0
        page = keys[start:start + limit]
        end = start + len(page)
        resp: dict = {
            "Contents": [{"Key": k, "Size": len(self._objects[k][-1].body)} for k in page],
            "KeyCount": len(page),
            "MaxKeys": limit,
        }
        if end < len(keys):
            token = f"tok-{end}"
            self._tokens[token] = end
            resp["IsTruncated"] = True
            resp["NextContinuationToken"] = token
        else:
            resp["IsTruncated"] = False
        return resp

    def _resolve(self, Key: str, VersionId: Optional[str]) -> _Version:
        self._guard(Key)
        versions = self._objects.get(Key)
        if not versions:
            raise SimClientError("NoSuchKey", Key)
        if VersionId is None:
            return versions[-1]  # latest
        for v in versions:
            if v.version_id == VersionId:
                return v
        raise SimClientError("NoSuchVersion", VersionId)

    def get_object(self, Bucket: str, Key: str, VersionId: Optional[str] = None, **_ignored) -> dict:
        if Bucket != self.bucket:
            raise SimClientError("NoSuchBucket", Bucket)
        v = self._resolve(Key, VersionId)
        return {
            "Body": _Body(v.body),
            "VersionId": v.version_id,
            "ContentLength": len(v.body),
            "Metadata": {"marking": v.marking} if v.marking else {},
        }

    def head_object(self, Bucket: str, Key: str, VersionId: Optional[str] = None, **_ignored) -> dict:
        if Bucket != self.bucket:
            raise SimClientError("NoSuchBucket", Bucket)
        v = self._resolve(Key, VersionId)
        return {
            "VersionId": v.version_id,
            "ContentLength": len(v.body),
            "Metadata": {"marking": v.marking} if v.marking else {},
        }
