"""Crash-safe, replayable custody for ``axm-intake/1.0`` observations.

This store is deliberately pre-shard. It preserves exact bytes and local
admission history. It never assigns Genesis identities or upgrades authority.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from . import canonical, conform, payload_bytes, sha256, validate, now
from .store_common import (
    STORE_EVENT_SPEC,
    STORE_RECEIPT_SPEC,
    STORE_SCHEMA_VERSION,
    STORE_SPEC,
    FileLock,
    StoreConfig,
    StoreError,
    atomic_write_bytes,
    atomic_write_json,
    copy_atomic,
    load_json,
    safe_component,
)


class IntakeStore:
    """SQLite-indexed local custody store with immutable file receipts."""

    def __init__(self, config: StoreConfig | None = None) -> None:
        self.config = config or StoreConfig.load()
        self.root = self.config.root
        self.db_path = self.root / "store.sqlite3"
        self.lock_path = self.root / "locks" / "store.lock"
        for relative in (
            "objects/sha256",
            "observations",
            "receipts",
            "events",
            "quarantine",
            "spool/pending",
            "spool/processing",
            "spool/accepted",
            "spool/rejected",
            "locks",
        ):
            directory = self.root / relative
            directory.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(directory, 0o700)
            except OSError:
                pass
        self._initialize()

    @contextmanager
    def _lock(self, timeout_seconds: float = 60.0) -> Iterator[None]:
        with FileLock(self.lock_path, timeout_seconds):
            yield

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    @staticmethod
    def _create_schema(db: sqlite3.Connection) -> None:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS observations (
                observation_id TEXT PRIMARY KEY,
                content_id TEXT NOT NULL,
                logical_id TEXT NOT NULL,
                version_id TEXT NOT NULL,
                subject_kind TEXT NOT NULL,
                adapter_id TEXT NOT NULL,
                adapter_version TEXT NOT NULL,
                producer TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                admitted_at TEXT NOT NULL,
                conformance_level TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                payload_bytes INTEGER NOT NULL,
                object_path TEXT NOT NULL,
                envelope_sha256 TEXT NOT NULL,
                envelope_path TEXT NOT NULL,
                receipt_path TEXT NOT NULL,
                receipt_json TEXT NOT NULL,
                stream_id TEXT NOT NULL,
                event_seq INTEGER NOT NULL,
                event_sha256 TEXT NOT NULL,
                UNIQUE(stream_id, event_seq),
                UNIQUE(event_sha256)
            );
            CREATE TABLE IF NOT EXISTS events (
                stream_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                previous_event_sha256 TEXT NOT NULL,
                event_sha256 TEXT NOT NULL UNIQUE,
                event_path TEXT NOT NULL,
                event_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                observation_id TEXT NOT NULL,
                PRIMARY KEY(stream_id, seq),
                FOREIGN KEY(observation_id) REFERENCES observations(observation_id)
            );
            CREATE TABLE IF NOT EXISTS quarantine (
                quarantine_id TEXT PRIMARY KEY,
                source_path TEXT NOT NULL,
                artifact_path TEXT NOT NULL,
                error_path TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_observations_logical
                ON observations(logical_id, observed_at);
            CREATE INDEX IF NOT EXISTS idx_observations_content
                ON observations(content_id);
            """
        )

    def _initialize(self) -> None:
        with self._lock():
            with self._connect() as db:
                version = int(db.execute("PRAGMA user_version").fetchone()[0])
                if version > STORE_SCHEMA_VERSION:
                    raise StoreError(
                        f"store schema {version} is newer than supported "
                        f"{STORE_SCHEMA_VERSION}"
                    )
                if version == 0:
                    self._create_schema(db)
                    db.execute(
                        "INSERT OR REPLACE INTO metadata VALUES('writer_id',?)",
                        (self.config.writer_id,),
                    )
                    db.execute(
                        "INSERT OR REPLACE INTO metadata VALUES('store_spec',?)",
                        (STORE_SPEC,),
                    )
                    db.execute(f"PRAGMA user_version={STORE_SCHEMA_VERSION}")
                    db.commit()
                else:
                    row = db.execute(
                        "SELECT value FROM metadata WHERE key='writer_id'"
                    ).fetchone()
                    if row and str(row[0]) != self.config.writer_id:
                        raise StoreError(
                            "configured writer_id does not match this store: "
                            f"{self.config.writer_id!r} != {row[0]!r}"
                        )
            manifest = self.root / "store.json"
            if not manifest.is_file():
                atomic_write_json(
                    manifest,
                    {
                        "specversion": STORE_SPEC,
                        "schema_version": STORE_SCHEMA_VERSION,
                        "writer_id": self.config.writer_id,
                        "created_at": now(),
                    },
                )
        try:
            os.chmod(self.db_path, 0o600)
        except OSError:
            pass

    def _object_path(self, digest: str) -> Path:
        return self.root / "objects" / "sha256" / digest[:2] / digest

    def _observation_path(self, observation_id: str) -> Path:
        return self.root / "observations" / f"{observation_id}.json"

    def _receipt_path(self, observation_id: str) -> Path:
        return self.root / "receipts" / f"{observation_id}.json"

    def _event_directory(self) -> Path:
        return self.root / "events" / safe_component(self.config.writer_id)

    def _next_event(self, db: sqlite3.Connection) -> tuple[int, str]:
        row = db.execute(
            "SELECT seq,event_sha256 FROM events WHERE stream_id=? "
            "ORDER BY seq DESC LIMIT 1",
            (self.config.writer_id,),
        ).fetchone()
        if not row:
            return 1, "0" * 64
        return int(row["seq"]) + 1, str(row["event_sha256"])

    def _materialize_payload(
        self,
        observation: Mapping[str, Any],
        base_dir: Path | None,
    ) -> tuple[bytes, Path]:
        raw = payload_bytes(
            observation["payload"],
            base_dir=base_dir,
            verify_locator=True,
        )
        if raw is None:
            raise StoreError("admission requires locally verifiable payload bytes")
        if len(raw) > self.config.max_payload_bytes:
            raise StoreError(
                f"payload exceeds configured maximum of "
                f"{self.config.max_payload_bytes} bytes"
            )
        digest = str(observation["payload"]["sha256"])
        if sha256(raw) != digest:
            raise StoreError("payload changed between validation and materialization")
        destination = self._object_path(digest)
        if destination.is_file():
            if (
                destination.stat().st_size != len(raw)
                or sha256(destination.read_bytes()) != digest
            ):
                raise StoreError(f"existing content object is corrupt: {destination}")
        else:
            atomic_write_bytes(destination, raw)
        return raw, destination

    def admit(
        self,
        observation: Mapping[str, Any],
        *,
        base_dir: Path | None = None,
    ) -> dict[str, Any]:
        checked = validate(observation, base_dir=base_dir, verify_locator=True)
        report = conform(observation, base_dir=base_dir, verify_locator=True)
        if not checked["payload_verified"] or "C1-custody" not in report["achieved"]:
            raise StoreError("store admission requires C1-custody")
        envelope_bytes = canonical(observation)
        if len(envelope_bytes) > self.config.max_envelope_bytes:
            raise StoreError(
                f"observation envelope exceeds "
                f"{self.config.max_envelope_bytes} bytes"
            )
        observation_id = str(observation["id"])
        payload_raw, object_path = self._materialize_payload(observation, base_dir)
        envelope_path = self._observation_path(observation_id)
        envelope_digest = sha256(envelope_bytes)
        if envelope_path.is_file():
            existing = envelope_path.read_bytes().rstrip(b"\n")
            if sha256(existing) != envelope_digest:
                raise StoreError(f"observation identity collision at {envelope_path}")
        else:
            atomic_write_bytes(envelope_path, envelope_bytes + b"\n")

        with self._lock():
            with self._connect() as db:
                existing = db.execute(
                    "SELECT receipt_path,receipt_json,event_seq FROM observations "
                    "WHERE observation_id=?",
                    (observation_id,),
                ).fetchone()
                if existing:
                    receipt = json.loads(str(existing["receipt_json"]))
                    event_row = db.execute(
                        "SELECT event_path,event_json FROM events "
                        "WHERE stream_id=? AND seq=?",
                        (self.config.writer_id, int(existing["event_seq"])),
                    ).fetchone()
                    if event_row and not Path(str(event_row["event_path"])).is_file():
                        atomic_write_bytes(
                            Path(str(event_row["event_path"])),
                            str(event_row["event_json"]).encode("utf-8") + b"\n",
                        )
                    receipt_path = Path(str(existing["receipt_path"]))
                    if not receipt_path.is_file():
                        atomic_write_json(receipt_path, receipt)
                    return receipt

                seq, previous = self._next_event(db)
                admitted_at = now()
                event_body = {
                    "specversion": STORE_EVENT_SPEC,
                    "stream_id": self.config.writer_id,
                    "seq": seq,
                    "previous_event_sha256": previous,
                    "created_at": admitted_at,
                    "kind": "observation_admitted",
                    "observation_id": observation_id,
                    "content_id": observation["content_id"],
                    "payload_sha256": observation["payload"]["sha256"],
                    "envelope_sha256": envelope_digest,
                }
                event_hash = sha256(canonical(event_body))
                event = {**event_body, "event_sha256": event_hash}
                event_path = (
                    self._event_directory()
                    / f"{seq:020d}_{event_hash}.json"
                )
                receipt_path = self._receipt_path(observation_id)
                receipt = {
                    "specversion": STORE_RECEIPT_SPEC,
                    "store_specversion": STORE_SPEC,
                    "observation_id": observation_id,
                    "content_id": observation["content_id"],
                    "logical_id": observation["subject"]["logical_id"],
                    "version_id": observation["subject"]["version_id"],
                    "payload_sha256": observation["payload"]["sha256"],
                    "payload_bytes": len(payload_raw),
                    "object_path": str(object_path),
                    "envelope_sha256": envelope_digest,
                    "envelope_path": str(envelope_path),
                    "receipt_path": str(receipt_path),
                    "conformance_level": report["highest_level"],
                    "authority": observation["authority"],
                    "admitted_at": admitted_at,
                    "writer_id": self.config.writer_id,
                    "event_seq": seq,
                    "event_sha256": event_hash,
                    "event_path": str(event_path),
                }
                event_json = canonical(event).decode("utf-8")
                receipt_json = canonical(receipt).decode("utf-8")
                db.execute("BEGIN IMMEDIATE")
                try:
                    db.execute(
                        """
                        INSERT INTO observations(
                            observation_id,content_id,logical_id,version_id,
                            subject_kind,adapter_id,adapter_version,producer,
                            observed_at,recorded_at,admitted_at,conformance_level,
                            payload_sha256,payload_bytes,object_path,envelope_sha256,
                            envelope_path,receipt_path,receipt_json,stream_id,
                            event_seq,event_sha256
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            observation_id,
                            observation["content_id"],
                            observation["subject"]["logical_id"],
                            observation["subject"]["version_id"],
                            observation["subject"]["kind"],
                            observation["source"]["adapter_id"],
                            observation["source"]["adapter_version"],
                            observation["source"]["producer"],
                            observation["observed_at"],
                            observation["recorded_at"],
                            admitted_at,
                            report["highest_level"],
                            observation["payload"]["sha256"],
                            len(payload_raw),
                            str(object_path),
                            envelope_digest,
                            str(envelope_path),
                            str(receipt_path),
                            receipt_json,
                            self.config.writer_id,
                            seq,
                            event_hash,
                        ),
                    )
                    db.execute(
                        "INSERT INTO events VALUES(?,?,?,?,?,?,?,?)",
                        (
                            self.config.writer_id,
                            seq,
                            previous,
                            event_hash,
                            str(event_path),
                            event_json,
                            admitted_at,
                            observation_id,
                        ),
                    )
                    db.commit()
                except Exception:
                    db.rollback()
                    raise
                atomic_write_bytes(event_path, event_json.encode() + b"\n")
                atomic_write_bytes(receipt_path, receipt_json.encode() + b"\n")
                return receipt

    def admit_file(
        self,
        path: Path,
        *,
        quarantine_on_error: bool = True,
    ) -> dict[str, Any]:
        source = path.resolve()
        try:
            return self.admit(load_json(source), base_dir=source.parent)
        except Exception as exc:
            if quarantine_on_error:
                self.quarantine(source, exc)
            raise

    def quarantine(self, source: Path, error: BaseException) -> dict[str, Any]:
        quarantine_id = "q1_" + uuid.uuid4().hex
        artifact = (
            self.root / "quarantine" / f"{quarantine_id}{source.suffix or '.bin'}"
        )
        error_path = self.root / "quarantine" / f"{quarantine_id}.error.json"
        if source.is_file():
            copy_atomic(source, artifact)
        else:
            atomic_write_bytes(artifact, b"")
        payload = {
            "quarantine_id": quarantine_id,
            "source_path": str(source),
            "artifact_path": str(artifact),
            "error_type": type(error).__name__,
            "errors": list(getattr(error, "errors", (str(error),))),
            "created_at": now(),
        }
        atomic_write_json(error_path, payload)
        with self._lock():
            with self._connect() as db:
                db.execute(
                    "INSERT INTO quarantine VALUES(?,?,?,?,?)",
                    (
                        quarantine_id,
                        str(source),
                        str(artifact),
                        str(error_path),
                        payload["created_at"],
                    ),
                )
        return payload

    def status(self) -> dict[str, Any]:
        with self._connect() as db:
            observations = int(
                db.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
            )
            contents = int(
                db.execute(
                    "SELECT COUNT(DISTINCT content_id) FROM observations"
                ).fetchone()[0]
            )
            events = int(db.execute("SELECT COUNT(*) FROM events").fetchone()[0])
            quarantined = int(
                db.execute("SELECT COUNT(*) FROM quarantine").fetchone()[0]
            )
            last = db.execute(
                "SELECT observation_id,admitted_at,conformance_level "
                "FROM observations ORDER BY admitted_at DESC LIMIT 1"
            ).fetchone()
        return {
            "specversion": STORE_SPEC,
            "schema_version": STORE_SCHEMA_VERSION,
            "root": str(self.root),
            "writer_id": self.config.writer_id,
            "observations": observations,
            "contents": contents,
            "events": events,
            "quarantined": quarantined,
            "spool": {
                state: len(list((self.root / "spool" / state).glob("*.json")))
                for state in ("pending", "processing", "accepted", "rejected")
            },
            "last": dict(last) if last else None,
        }

    def verify(self) -> dict[str, Any]:
        errors: list[str] = []
        checked_observations = 0
        checked_events = 0
        with self._connect() as db:
            integrity = str(db.execute("PRAGMA integrity_check").fetchone()[0])
            if integrity.lower() != "ok":
                errors.append(f"sqlite integrity_check: {integrity}")
            rows = db.execute(
                "SELECT * FROM observations ORDER BY admitted_at,observation_id"
            ).fetchall()
            event_rows = db.execute(
                "SELECT * FROM events ORDER BY stream_id,seq"
            ).fetchall()

        for row in rows:
            try:
                envelope_raw = Path(str(row["envelope_path"])).read_bytes().rstrip(b"\n")
                if sha256(envelope_raw) != row["envelope_sha256"]:
                    raise StoreError("envelope digest mismatch")
                observation = json.loads(envelope_raw.decode("utf-8"))
                validate(observation, verify_locator=False)
                if observation["id"] != row["observation_id"]:
                    raise StoreError("observation id mismatch")
                object_path = Path(str(row["object_path"]))
                if not object_path.is_file():
                    raise StoreError("payload object missing")
                if object_path.stat().st_size != int(row["payload_bytes"]):
                    raise StoreError("payload byte count mismatch")
                if sha256(object_path.read_bytes()) != row["payload_sha256"]:
                    raise StoreError("payload digest mismatch")
                receipt = load_json(Path(str(row["receipt_path"])))
                if receipt.get("observation_id") != row["observation_id"]:
                    raise StoreError("receipt observation id mismatch")
                if receipt.get("event_sha256") != row["event_sha256"]:
                    raise StoreError("receipt event hash mismatch")
                checked_observations += 1
            except Exception as exc:
                errors.append(f"observation {row['observation_id']}: {exc}")

        previous_by_stream: dict[str, str] = {}
        expected_seq_by_stream: dict[str, int] = {}
        for row in event_rows:
            stream = str(row["stream_id"])
            seq = int(row["seq"])
            expected = expected_seq_by_stream.get(stream, 1)
            previous = previous_by_stream.get(stream, "0" * 64)
            try:
                if seq != expected:
                    raise StoreError(f"event sequence expected {expected}, got {seq}")
                if str(row["previous_event_sha256"]) != previous:
                    raise StoreError("previous event hash mismatch")
                event = load_json(Path(str(row["event_path"])))
                event_hash = str(event.pop("event_sha256", ""))
                if (
                    sha256(canonical(event)) != event_hash
                    or event_hash != row["event_sha256"]
                ):
                    raise StoreError("event hash mismatch")
                checked_events += 1
                previous_by_stream[stream] = event_hash
                expected_seq_by_stream[stream] = seq + 1
            except Exception as exc:
                errors.append(f"event {stream}/{seq}: {exc}")

        return {
            "status": "PASS" if not errors else "FAIL",
            "specversion": STORE_SPEC,
            "root": str(self.root),
            "observations_checked": checked_observations,
            "events_checked": checked_events,
            "errors": errors,
        }

    def rebuild_index(self) -> dict[str, Any]:
        from .store_recovery import rebuild_index

        return rebuild_index(self)

    def backup(self, destination: Path) -> Path:
        from .store_recovery import backup_store

        return backup_store(self, destination)

    @classmethod
    def restore_backup(
        cls,
        archive_path: Path,
        destination: Path,
        *,
        writer_id: str | None = None,
    ) -> "IntakeStore":
        from .store_recovery import restore_backup

        return restore_backup(archive_path, destination, writer_id=writer_id)

    def spool_submit(self, observation_path: Path) -> Path:
        from .store_recovery import spool_submit

        return spool_submit(self, observation_path)

    def spool_recover(self, max_age_seconds: int = 15 * 60) -> int:
        from .store_recovery import spool_recover

        return spool_recover(self, max_age_seconds=max_age_seconds)

    def spool_pump(self, limit: int = 100) -> dict[str, int]:
        from .store_recovery import spool_pump

        return spool_pump(self, limit=limit)
