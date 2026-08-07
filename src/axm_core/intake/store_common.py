"""Shared durability primitives for the AXM intake custody store."""
from __future__ import annotations

import json
import os
import shutil
import socket
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import canonical

STORE_SPEC = "axm-intake-store/1"
STORE_SCHEMA_VERSION = 1
STORE_RECEIPT_SPEC = "axm-intake-store-receipt/1"
STORE_EVENT_SPEC = "axm-intake-store-event/1"
DEFAULT_MAX_ENVELOPE_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_PAYLOAD_BYTES = 1024 * 1024 * 1024


class StoreError(RuntimeError):
    """The local custody store could not complete or prove an operation."""


@dataclass(frozen=True)
class StoreConfig:
    root: Path
    writer_id: str
    max_envelope_bytes: int = DEFAULT_MAX_ENVELOPE_BYTES
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES

    @classmethod
    def load(
        cls,
        root: str | Path | None = None,
        writer_id: str | None = None,
    ) -> "StoreConfig":
        configured_root = root or os.environ.get("AXM_INTAKE_STORE")
        path = (
            Path(configured_root).expanduser()
            if configured_root
            else Path.home() / ".axm" / "intake"
        )
        configured_writer = (
            writer_id
            or os.environ.get("AXM_INTAKE_WRITER")
            or f"host:{socket.gethostname()}"
        )
        if not configured_writer.strip():
            raise StoreError("writer_id must be a non-empty string")
        return cls(path.resolve(), configured_writer.strip())


class FileLock:
    """Cross-platform advisory lock without stale-file deletion heuristics."""

    def __init__(self, path: Path, timeout_seconds: float = 60.0) -> None:
        self.path = path
        self.timeout_seconds = timeout_seconds
        self._handle: Any = None

    def __enter__(self) -> "FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a+b")
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    self._handle.seek(0)
                    if self._handle.read(1) == b"":
                        self._handle.seek(0)
                        self._handle.write(b"0")
                        self._handle.flush()
                    self._handle.seek(0)
                    msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(
                        self._handle.fileno(),
                        fcntl.LOCK_EX | fcntl.LOCK_NB,
                    )
                return self
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    self._handle.close()
                    self._handle = None
                    raise TimeoutError(f"timed out waiting for store lock: {self.path}")
                time.sleep(0.05)

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self._handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None


def safe_component(value: str, limit: int = 160) -> str:
    safe = "".join(
        character if character.isalnum() or character in "._-" else "_"
        for character in value
    )
    return (safe.strip("._") or "unknown")[:limit]


def fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except (OSError, AttributeError):
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    fsync_directory(path.parent)


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_bytes(path, canonical(value) + b"\n")


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StoreError(f"cannot read JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise StoreError(f"expected a JSON object in {path}")
    return value


def copy_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    with source.open("rb") as reader, temporary.open("wb") as writer:
        shutil.copyfileobj(reader, writer, length=1024 * 1024)
        writer.flush()
        os.fsync(writer.fileno())
    os.replace(temporary, destination)
    fsync_directory(destination.parent)
