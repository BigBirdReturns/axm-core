"""Hardened production facade over the reference intake custody store.

The base store remains a compact reference implementation. This facade adds
restart repair, strict store-root confinement, symlink refusal, bounded file
admission, and byte-for-byte verification of committed receipt and event files.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import canonical, sha256, validate
from .store import IntakeStore as ReferenceIntakeStore
from .store_common import StoreError, atomic_write_bytes, load_json


class IntakeStore(ReferenceIntakeStore):
    """Production custody store with restart and path-integrity defenses."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._repair_committed_artifacts()

    def _assert_store_path(self, path: Path, label: str) -> Path:
        if path.is_symlink():
            raise StoreError(f"{label} must not be a symlink: {path}")
        resolved = path.resolve()
        try:
            resolved.relative_to(self.root.resolve())
        except ValueError as exc:
            raise StoreError(f"{label} escapes the store root: {path}") from exc
        return resolved

    def _repair_committed_artifacts(self) -> None:
        """Materialize DB-committed receipt/event files after interruption."""
        with self._lock():
            with self._connect() as db:
                receipts = db.execute(
                    "SELECT receipt_path,receipt_json FROM observations"
                ).fetchall()
                events = db.execute(
                    "SELECT event_path,event_json FROM events"
                ).fetchall()
            for row in receipts:
                path = self._assert_store_path(
                    Path(str(row["receipt_path"])), "receipt_path"
                )
                expected = str(row["receipt_json"]).encode("utf-8") + b"\n"
                if not path.is_file():
                    atomic_write_bytes(path, expected)
            for row in events:
                path = self._assert_store_path(
                    Path(str(row["event_path"])), "event_path"
                )
                expected = str(row["event_json"]).encode("utf-8") + b"\n"
                if not path.is_file():
                    atomic_write_bytes(path, expected)

    def admit_file(
        self,
        path: Path,
        *,
        quarantine_on_error: bool = True,
    ) -> dict[str, Any]:
        source = path.resolve()
        if source.stat().st_size > self.config.max_envelope_bytes:
            error = StoreError(
                f"observation envelope exceeds {self.config.max_envelope_bytes} bytes"
            )
            if quarantine_on_error:
                self.quarantine(source, error)
            raise error
        return super().admit_file(
            source,
            quarantine_on_error=quarantine_on_error,
        )

    def status(self) -> dict[str, Any]:
        result = super().status()
        result["spool"] = {
            state: len(
                [
                    path
                    for path in (self.root / "spool" / state).glob("*.json")
                    if not path.name.endswith((".receipt.json", ".error.json"))
                ]
            )
            for state in ("pending", "processing", "accepted", "rejected")
        }
        return result

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
                envelope_path = self._assert_store_path(
                    Path(str(row["envelope_path"])), "envelope_path"
                )
                envelope_raw = envelope_path.read_bytes().rstrip(b"\n")
                if sha256(envelope_raw) != row["envelope_sha256"]:
                    raise StoreError("envelope digest mismatch")
                observation = json.loads(envelope_raw.decode("utf-8"))
                validate(observation, verify_locator=False)
                if observation["id"] != row["observation_id"]:
                    raise StoreError("observation id mismatch")
                if observation["content_id"] != row["content_id"]:
                    raise StoreError("content id mismatch")
                if observation["payload"]["sha256"] != row["payload_sha256"]:
                    raise StoreError("payload digest differs from envelope")

                object_path = self._assert_store_path(
                    Path(str(row["object_path"])), "object_path"
                )
                if not object_path.is_file():
                    raise StoreError("payload object missing")
                if object_path.stat().st_size != int(row["payload_bytes"]):
                    raise StoreError("payload byte count mismatch")
                if sha256(object_path.read_bytes()) != row["payload_sha256"]:
                    raise StoreError("payload digest mismatch")

                receipt_path = self._assert_store_path(
                    Path(str(row["receipt_path"])), "receipt_path"
                )
                receipt = load_json(receipt_path)
                if receipt.get("observation_id") != row["observation_id"]:
                    raise StoreError("receipt observation id mismatch")
                if receipt.get("event_sha256") != row["event_sha256"]:
                    raise StoreError("receipt event hash mismatch")
                if canonical(receipt).decode("utf-8") != row["receipt_json"]:
                    raise StoreError("receipt file differs from committed receipt JSON")
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
                event_path = self._assert_store_path(
                    Path(str(row["event_path"])), "event_path"
                )
                event = load_json(event_path)
                event_hash = str(event.pop("event_sha256", ""))
                if (
                    sha256(canonical(event)) != event_hash
                    or event_hash != row["event_sha256"]
                ):
                    raise StoreError("event hash mismatch")
                event["event_sha256"] = event_hash
                if canonical(event).decode("utf-8") != row["event_json"]:
                    raise StoreError("event file differs from committed event JSON")
                checked_events += 1
                previous_by_stream[stream] = event_hash
                expected_seq_by_stream[stream] = seq + 1
            except Exception as exc:
                errors.append(f"event {stream}/{seq}: {exc}")

        return {
            "status": "PASS" if not errors else "FAIL",
            "specversion": "axm-intake-store/1",
            "root": str(self.root),
            "observations_checked": checked_observations,
            "events_checked": checked_events,
            "errors": errors,
        }
