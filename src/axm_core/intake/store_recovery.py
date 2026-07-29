"""Recovery, backup, restore, and atomic spool operations for intake custody."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import canonical, conform, now, sha256, validate
from .store_common import (
    STORE_SCHEMA_VERSION,
    STORE_SPEC,
    StoreConfig,
    StoreError,
    atomic_write_json,
    copy_atomic,
    fsync_directory,
    load_json,
)

if TYPE_CHECKING:
    from .store import IntakeStore


def rebuild_index(store: "IntakeStore") -> dict[str, Any]:
    """Rebuild SQLite from immutable receipts, events, envelopes, and objects."""
    backup = store.db_path.with_suffix(
        f".sqlite3.before-rebuild-{int(time.time())}"
    )
    receipts: list[tuple[int, Path, dict[str, Any]]] = []
    for path in sorted((store.root / "receipts").glob("obs1_*.json")):
        try:
            receipt = load_json(path)
            receipts.append((int(receipt["event_seq"]), path, receipt))
        except Exception:
            continue
    receipts.sort(key=lambda row: (row[0], row[2].get("observation_id", "")))

    with store._lock():
        if store.db_path.exists():
            with store._connect() as db:
                db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            shutil.copy2(store.db_path, backup)
            store.db_path.unlink()
        for suffix in ("-wal", "-shm"):
            try:
                Path(str(store.db_path) + suffix).unlink()
            except FileNotFoundError:
                pass
        with store._connect() as db:
            store._create_schema(db)
            db.execute(
                "INSERT INTO metadata VALUES('writer_id',?)",
                (store.config.writer_id,),
            )
            db.execute("INSERT INTO metadata VALUES('store_spec',?)", (STORE_SPEC,))
            db.execute(f"PRAGMA user_version={STORE_SCHEMA_VERSION}")
            db.commit()

    recovered = 0
    failed = 0
    previous = "0" * 64
    expected_seq = 1
    with store._lock():
        with store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                for seq, receipt_path, receipt in receipts:
                    try:
                        if seq != expected_seq:
                            raise StoreError(
                                f"expected event sequence {expected_seq}, got {seq}"
                            )
                        envelope_path = Path(str(receipt["envelope_path"]))
                        envelope_raw = envelope_path.read_bytes().rstrip(b"\n")
                        if sha256(envelope_raw) != receipt["envelope_sha256"]:
                            raise StoreError("envelope digest mismatch")
                        observation = json.loads(envelope_raw.decode("utf-8"))
                        validate(observation, verify_locator=False)
                        object_path = Path(str(receipt["object_path"]))
                        if (
                            not object_path.is_file()
                            or sha256(object_path.read_bytes())
                            != receipt["payload_sha256"]
                        ):
                            raise StoreError("payload object mismatch")
                        event_path = Path(str(receipt["event_path"]))
                        event = load_json(event_path)
                        event_hash = str(event.pop("event_sha256", ""))
                        if event.get("previous_event_sha256") != previous:
                            raise StoreError("event predecessor mismatch")
                        if (
                            sha256(canonical(event)) != event_hash
                            or event_hash != receipt["event_sha256"]
                        ):
                            raise StoreError("event digest mismatch")
                        event["event_sha256"] = event_hash
                        report = conform(observation, verify_locator=False)
                        db.execute(
                            "INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (
                                observation["id"],
                                observation["content_id"],
                                observation["subject"]["logical_id"],
                                observation["subject"]["version_id"],
                                observation["subject"]["kind"],
                                observation["source"]["adapter_id"],
                                observation["source"]["adapter_version"],
                                observation["source"]["producer"],
                                observation["observed_at"],
                                observation["recorded_at"],
                                receipt["admitted_at"],
                                receipt.get("conformance_level")
                                or report["highest_level"],
                                receipt["payload_sha256"],
                                int(receipt["payload_bytes"]),
                                str(object_path),
                                receipt["envelope_sha256"],
                                str(envelope_path),
                                str(receipt_path),
                                canonical(receipt).decode("utf-8"),
                                store.config.writer_id,
                                seq,
                                event_hash,
                            ),
                        )
                        db.execute(
                            "INSERT INTO events VALUES(?,?,?,?,?,?,?,?)",
                            (
                                store.config.writer_id,
                                seq,
                                previous,
                                event_hash,
                                str(event_path),
                                canonical(event).decode("utf-8"),
                                event["created_at"],
                                observation["id"],
                            ),
                        )
                        previous = event_hash
                        expected_seq += 1
                        recovered += 1
                    except Exception:
                        failed += 1
                db.commit()
            except Exception:
                db.rollback()
                raise
    return {
        "recovered": recovered,
        "failed": failed,
        "backup": str(backup) if backup.exists() else "",
    }


def backup_store(store: "IntakeStore", destination: Path) -> Path:
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with store._lock():
        with store._connect() as db:
            db.execute("PRAGMA wal_checkpoint(FULL)")
        verification = store.verify()
        if verification["status"] != "PASS":
            raise StoreError("refusing to back up a store that fails verification")
        with tempfile.TemporaryDirectory(prefix="axm-intake-backup-") as temporary:
            stage = Path(temporary) / "intake"
            shutil.copytree(
                store.root,
                stage,
                ignore=shutil.ignore_patterns("locks", "*.tmp", "*.part"),
            )
            atomic_write_json(
                stage / "backup-manifest.json",
                {
                    "specversion": "axm-intake-backup/1",
                    "created_at": now(),
                    "writer_id": store.config.writer_id,
                    "store_verification": verification,
                },
            )
            temporary_zip = destination.with_name(
                f".{destination.name}.{uuid.uuid4().hex}.tmp"
            )
            with zipfile.ZipFile(
                temporary_zip,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                allowZip64=True,
            ) as archive:
                for path in sorted(stage.rglob("*")):
                    if path.is_file():
                        archive.write(path, path.relative_to(stage.parent).as_posix())
            os.replace(temporary_zip, destination)
            fsync_directory(destination.parent)
    return destination


def restore_backup(
    archive_path: Path,
    destination: Path,
    *,
    writer_id: str | None = None,
) -> "IntakeStore":
    from .store import IntakeStore

    archive_path = archive_path.expanduser().resolve()
    destination = destination.expanduser().resolve()
    if not archive_path.is_file():
        raise StoreError(f"backup archive not found: {archive_path}")
    if destination.exists() and any(destination.iterdir()):
        raise StoreError(f"restore destination must be empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="axm-intake-restore-") as temporary:
        stage = Path(temporary)
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                path = Path(member.filename)
                if path.is_absolute() or ".." in path.parts:
                    raise StoreError(f"unsafe backup member: {member.filename}")
            archive.extractall(stage)
        source = stage / "intake"
        if not (source / "backup-manifest.json").is_file():
            raise StoreError("backup manifest is missing")
        store_manifest = load_json(source / "store.json")
        restored_writer = writer_id or str(store_manifest.get("writer_id") or "")
        if not restored_writer:
            raise StoreError("backup does not declare writer_id")
        for item in source.iterdir():
            if item.name == "backup-manifest.json":
                continue
            target = destination / item.name
            if item.is_dir():
                shutil.copytree(item, target)
            else:
                shutil.copy2(item, target)
    restored = IntakeStore(StoreConfig(destination, restored_writer))
    result = restored.verify()
    if result["status"] != "PASS":
        raise StoreError(
            "restored store failed verification: " + "; ".join(result["errors"])
        )
    return restored


def spool_submit(store: "IntakeStore", observation_path: Path) -> Path:
    source = observation_path.expanduser().resolve()
    if not source.is_file():
        raise StoreError(f"spool source is not a file: {source}")
    identifier = f"{int(time.time() * 1_000_000):020d}_{uuid.uuid4().hex}.json"
    destination = store.root / "spool" / "pending" / identifier
    copy_atomic(source, destination)
    return destination


def spool_recover(store: "IntakeStore", max_age_seconds: int = 15 * 60) -> int:
    recovered = 0
    cutoff = time.time() - max_age_seconds
    for path in (store.root / "spool" / "processing").glob("*.json"):
        try:
            if path.stat().st_mtime >= cutoff:
                continue
            os.replace(path, store.root / "spool" / "pending" / path.name)
            recovered += 1
        except OSError:
            continue
    return recovered


def spool_pump(store: "IntakeStore", limit: int = 100) -> dict[str, int]:
    recovered = spool_recover(store)
    attempted = 0
    accepted = 0
    rejected = 0
    pending_paths = sorted((store.root / "spool" / "pending").glob("*.json"))
    for pending in pending_paths[: max(1, min(limit, 1000))]:
        processing = store.root / "spool" / "processing" / pending.name
        try:
            os.replace(pending, processing)
        except OSError:
            continue
        attempted += 1
        try:
            receipt = store.admit_file(processing, quarantine_on_error=False)
            destination = store.root / "spool" / "accepted" / processing.name
            os.replace(processing, destination)
            atomic_write_json(destination.with_suffix(".receipt.json"), receipt)
            accepted += 1
        except Exception as exc:
            destination = store.root / "spool" / "rejected" / processing.name
            os.replace(processing, destination)
            atomic_write_json(
                destination.with_suffix(".error.json"),
                {
                    "error_type": type(exc).__name__,
                    "errors": list(getattr(exc, "errors", (str(exc),))),
                    "rejected_at": now(),
                },
            )
            rejected += 1
    return {
        "attempted": attempted,
        "accepted": accepted,
        "rejected": rejected,
        "recovered": recovered,
    }
