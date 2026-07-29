from __future__ import annotations

import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from click.testing import CliRunner

from axm_core.intake import AUTHORITY, finalize, inline_payload
from axm_core.intake.cli import intake_group
from axm_core.intake.store import IntakeStore, StoreConfig

T = "2026-07-28T12:00:00Z"


def observation(text: str = "hello", observed_at: str = T):
    payload = inline_payload(text, "text/plain")
    return finalize(
        {
            "specversion": "axm-intake/1.0",
            "type": "observation",
            "id": "obs1_" + "0" * 64,
            "content_id": "cnt1_" + "0" * 64,
            "source": {
                "adapter_id": "org.example.production-test",
                "adapter_version": "1.0.0",
                "producer": "test-suite",
                "source_uri": "urn:test:observation",
                "source_revision": "test-fixture-v1",
                "source_license": "Apache-2.0",
            },
            "subject": {
                "kind": "event",
                "logical_id": "thing",
                "version_id": payload["sha256"],
                "parent_version_ids": [],
            },
            "observed_at": observed_at,
            "recorded_at": observed_at,
            "payload": payload,
            "authority": AUTHORITY,
            "relations": [],
            "coverage": {
                "scope": "one test record",
                "status": "complete",
                "method": "single fixture",
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
            "extensions": {"bridge": {"format": "production-test"}},
        },
        observed_at,
    )


def store(tmp_path: Path) -> IntakeStore:
    return IntakeStore(StoreConfig(tmp_path / "store", "writer:test"))


def test_admission_is_idempotent_and_hash_chained(tmp_path: Path):
    custody = store(tmp_path)
    first = custody.admit(observation())
    second = custody.admit(observation())
    assert first == second
    assert custody.status()["observations"] == 1
    assert custody.status()["events"] == 1
    assert custody.verify()["status"] == "PASS"


def test_concurrent_duplicate_delivery_has_one_semantic_effect(tmp_path: Path):
    custody = store(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(lambda _: custody.admit(observation()), range(16)))
    assert len({receipt["event_sha256"] for receipt in receipts}) == 1
    assert custody.status()["events"] == 1
    assert custody.verify()["status"] == "PASS"


def test_committed_database_repairs_missing_receipt_and_event_files(tmp_path: Path):
    custody = store(tmp_path)
    receipt = custody.admit(observation())
    Path(receipt["receipt_path"]).unlink()
    Path(receipt["event_path"]).unlink()
    repaired = custody.admit(observation())
    assert repaired == receipt
    assert Path(receipt["receipt_path"]).is_file()
    assert Path(receipt["event_path"]).is_file()
    assert custody.verify()["status"] == "PASS"


def test_tamper_is_detected(tmp_path: Path):
    custody = store(tmp_path)
    receipt = custody.admit(observation())
    Path(receipt["object_path"]).write_text("tampered", encoding="utf-8")
    result = custody.verify()
    assert result["status"] == "FAIL"
    assert any("payload" in error for error in result["errors"])


def test_rebuild_backup_and_clean_restore(tmp_path: Path):
    custody = store(tmp_path)
    custody.admit(observation("one", "2026-07-28T12:00:00Z"))
    custody.admit(observation("two", "2026-07-28T12:01:00Z"))
    rebuilt = custody.rebuild_index()
    assert rebuilt["recovered"] == 2
    assert rebuilt["failed"] == 0
    assert custody.verify()["status"] == "PASS"
    archive = custody.backup(tmp_path / "intake-backup.zip")
    original_root = custody.root
    shutil.rmtree(original_root)
    restored = IntakeStore.restore_backup(archive, tmp_path / "restored")
    assert not original_root.exists()
    assert restored.status()["observations"] == 2
    assert restored.verify()["status"] == "PASS"
    for receipt in (restored.root / "receipts").glob("*.json"):
        payload = json.loads(receipt.read_text(encoding="utf-8"))
        assert str(restored.root) in payload["object_path"]
        assert str(restored.root) in payload["event_path"]


def test_atomic_spool_preserves_success_and_rejection(tmp_path: Path):
    custody = store(tmp_path)
    good = tmp_path / "good.json"
    good.write_text(json.dumps(observation()), encoding="utf-8")
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    custody.spool_submit(good)
    custody.spool_submit(bad)
    result = custody.spool_pump()
    assert result["accepted"] == 1
    assert result["rejected"] == 1
    assert list((custody.root / "spool" / "accepted").glob("*.receipt.json"))
    assert list((custody.root / "spool" / "rejected").glob("*.error.json"))


def test_store_cli_roundtrip(tmp_path: Path):
    source = tmp_path / "observation.json"
    source.write_text(json.dumps(observation()), encoding="utf-8")
    root = tmp_path / "cli-store"
    runner = CliRunner()
    result = runner.invoke(
        intake_group,
        [
            "store",
            "admit",
            str(source),
            "--root",
            str(root),
            "--writer-id",
            "writer:cli",
        ],
    )
    assert result.exit_code == 0, result.output
    result = runner.invoke(
        intake_group,
        ["store", "verify", "--root", str(root), "--writer-id", "writer:cli"],
    )
    assert result.exit_code == 0, result.output
