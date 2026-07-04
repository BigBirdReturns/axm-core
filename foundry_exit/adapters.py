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


class S3ExportSource:
    """S3-compatible adapter (boto3, imported lazily). Read-only: it uses only
    ``get_object`` / ``list_objects_v2``. No real Palantir call is required for
    tests; this is exercised only with real credentials against a real endpoint.
    """

    def __init__(self, config: S3Config) -> None:
        self._cfg = config
        self._client = None

    def _client_lazy(self):
        if self._client is None:
            import boto3  # lazy: only needed for live extraction

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
        resp = self._client_lazy().get_object(Bucket=self._cfg.bucket, Key=self._key(object_path))
        return resp["Body"].read()

    def list_objects(self, prefix: str = "") -> List[str]:
        resp = self._client_lazy().list_objects_v2(
            Bucket=self._cfg.bucket, Prefix=self._key(prefix)
        )
        return sorted(o["Key"] for o in resp.get("Contents", []))
