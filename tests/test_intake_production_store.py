from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from axm_core.intake import AUTHORITY, finalize, inline_payload
from axm_core.intake.production_store import IntakeStore
from axm_core.intake.store_common import StoreConfig, StoreError

T = "2026-07-29T04:00:00Z"


def observation(text: str = "production") -> dict:
    payload = inline_payload(text, "text/plain")
    return finalize(
        {
            "specversion": "axm-intake/1.0",
            "type": "observation",
            "id": "obs1_" + "0" * 64,
            "content_id": "cnt1_" + "0" * 64,
            "source": {
                "adapter_id": "org.example.production-store",
                "adapter_version": "1.0.0",
                "producer": "pytest",
                "source_uri": "urn:test:production-store",
                "source_revision": "fixture-v1",
                "source_license": "Apache-2.0",
            },
            "subject": {
                "kind": "event",
                "logical_id": "production-store",
                "version_id": payload["sha256"],
                "parent_version_ids": [],
            },
            "observed_at": T,
            "recorded_at": T,
            "payload": payload,
            "authority": AUTHORITY,
            "relations": [],
            "coverage": {
                "scope": "one fixture",
                "status": "complete",
                "method": "unit test",
                "denominator": {
                    "kind": "record",
                    "expected": 1,
                    "observed": 1,
                    "excluded": 0,
                },
                "exceptions": [],
            },
            "security": {
                "sensitivity": "private",
                "personal_data": "no",
                "credentials": "no",
                "redactions": [],
            },
            "extensions": {"bridge": {"format": "production-store-test"}},
        },
        T,
    )


def config(tmp_path: Path, max_envelope_bytes: int = 16 * 1024 * 1024) -> StoreConfig:
    return StoreConfig(
        root=tmp_path / "store",
        writer_id="writer:production",
        max_envelope_bytes=max_envelope_bytes,
    )


def test_restart_repairs_db_committed_receipt_and_event_files(tmp_path: Path) -> None:
    store = IntakeStore(config(tmp_path))
    receipt = store.admit(observation())
    Path(receipt["receipt_path"]).unlink()
    Path(receipt["event_path"]).unlink()
    reopened = IntakeStore(config(tmp_path))
    assert Path(receipt["receipt_path"]).is_file()
    assert Path(receipt["event_path"]).is_file()
    assert reopened.verify()["status"] == "PASS"


def test_receipt_tamper_is_detected_against_committed_json(tmp_path: Path) -> None:
    store = IntakeStore(config(tmp_path))
    receipt = store.admit(observation())
    path = Path(receipt["receipt_path"])
    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["authority"] = "approved"
    path.write_text(json.dumps(tampered), encoding="utf-8")
    result = store.verify()
    assert result["status"] == "FAIL"
    assert any("committed receipt JSON" in error for error in result["errors"])


def test_store_path_escape_is_rejected(tmp_path: Path) -> None:
    store = IntakeStore(config(tmp_path))
    receipt = store.admit(observation())
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    with store._connect() as db:
        db.execute(
            "UPDATE observations SET receipt_path=? WHERE observation_id=?",
            (str(outside), receipt["observation_id"]),
        )
    result = store.verify()
    assert result["status"] == "FAIL"
    assert any("escapes the store root" in error for error in result["errors"])


@pytest.mark.skipif(os.name == "nt", reason="symlink creation may require Windows privileges")
def test_symlinked_payload_object_is_rejected(tmp_path: Path) -> None:
    store = IntakeStore(config(tmp_path))
    receipt = store.admit(observation())
    object_path = Path(receipt["object_path"])
    original = object_path.read_bytes()
    target = tmp_path / "external-payload"
    target.write_bytes(original)
    object_path.unlink()
    object_path.symlink_to(target)
    result = store.verify()
    assert result["status"] == "FAIL"
    assert any("must not be a symlink" in error for error in result["errors"])


def test_file_admission_is_bounded_before_json_decode(tmp_path: Path) -> None:
    store = IntakeStore(config(tmp_path, max_envelope_bytes=32))
    source = tmp_path / "oversized.json"
    source.write_bytes(b"{" + b"x" * 128 + b"}")
    with pytest.raises(StoreError, match="exceeds"):
        store.admit_file(source, quarantine_on_error=False)
