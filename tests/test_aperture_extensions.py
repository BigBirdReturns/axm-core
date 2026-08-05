from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SPECTRA = REPO_ROOT / "spectra"
if str(SPECTRA) not in sys.path:
    sys.path.insert(0, str(SPECTRA))

from axiom_runtime import aperture_extensions as aperture
from axiom_runtime.aperture_extensions import runtime as aperture_runtime

PACKAGE = "storypkg1_" + "1" * 64
MAP = "timemap1_" + "2" * 64
DIGEST = "3" * 64
SOURCE_DIGEST = "4" * 64


def _bundle():
    revision = {"package_id": PACKAGE, "revision": "1"}
    return {
        "aperture-package-revisions@1": [{
            **revision,
            "work_id": "work:fixture",
            "canonical_story_digest": DIGEST,
            "canonical_edition_id": "edition:canonical",
            "review_state": "reviewed",
            "supersedes": "",
            "edition_time_map_refs_json": json.dumps([MAP], separators=(",", ":")),
        }],
        "aperture-positions@1": [
            {**revision, "position_id": "position:one", "canonical_start_us": "0", "canonical_end_us": "5000000", "kind": "scene", "parent_id": "", "label": "Entry"},
            {**revision, "position_id": "position:two", "canonical_start_us": "5000000", "canonical_end_us": "9000000", "kind": "scene", "parent_id": "", "label": "Consequence"},
        ],
        "aperture-facts@1": [
            {**revision, "fact_id": "fact:one", "proposition": "The courier enters.", "first_reveal_position_id": "position:one", "subject_ids_json": "[]", "provenance_refs_json": '["source:one"]'},
            {**revision, "fact_id": "fact:two", "proposition": "The map changes hands.", "first_reveal_position_id": "position:two", "subject_ids_json": '["character:courier"]', "provenance_refs_json": '["source:one"]'},
        ],
        "aperture-causal-edges@1": [{**revision, "edge_id": "edge:one", "cause_fact_ids_json": '["fact:one"]', "effect_fact_id": "fact:two", "strength": "necessary", "provenance_refs_json": '["source:one"]'}],
        "aperture-reveals@1": [
            {**revision, "reveal_id": "reveal:one", "fact_id": "fact:one", "position_id": "position:one", "mode": "seen", "provenance_refs_json": '["source:one"]'},
            {**revision, "reveal_id": "reveal:two", "fact_id": "fact:two", "position_id": "position:two", "mode": "seen", "provenance_refs_json": '["source:one"]'},
        ],
        "aperture-edition-maps@1": [{
            "map_id": MAP, "work_id": "work:fixture", "provider_edition_id": "edition:provider", "canonical_edition_id": "edition:canonical",
            "segment_id": "segment:one", "kind": "mapped", "provider_start_us": "0", "provider_end_us": "9000000",
            "canonical_start_us": "0", "canonical_end_us": "9000000", "rate_numerator": "1", "rate_denominator": "1",
            "evidence_refs_json": '["evidence:one"]', "source_digests_json": json.dumps([SOURCE_DIGEST], separators=(",", ":")),
            "confidence": "1", "review_state": "reviewed",
        }],
        "aperture-sources@1": [{**revision, "source_id": "source:one", "sha256": SOURCE_DIGEST, "custody": "holder_controlled", "contains_redistributable_text": "false"}],
    }


def test_validates_complete_bundle():
    result = aperture.validate_aperture_extension_bundle(_bundle())
    assert set(result) == set(aperture.APERTURE_EXTENSION_SPECS)
    assert result["aperture-facts@1"][1]["fact_id"] == "fact:two"


@pytest.mark.parametrize("mutation,match", [
    (lambda b: b["aperture-facts@1"][0].__setitem__("subject_ids_json", "[ \"x\" ]"), "canonical compact JSON"),
    (lambda b: b["aperture-facts@1"][0].__setitem__("first_reveal_position_id", "position:missing"), "unknown reveal position"),
    (lambda b: b["aperture-causal-edges@1"][0].__setitem__("effect_fact_id", "fact:one"), "self-causal"),
    (lambda b: (b["aperture-edition-maps@1"][0].__setitem__("canonical_start_us", ""), b["aperture-edition-maps@1"][0].__setitem__("canonical_end_us", "")), "contradicts segment kind"),
    (lambda b: b["aperture-sources@1"][0].__setitem__("contains_redistributable_text", False), "JSON strings only"),
])
def test_refuses_adversarial_bundle(mutation, match):
    value = _bundle()
    mutation(value)
    with pytest.raises(aperture.ApertureExtensionError, match=match):
        aperture.validate_aperture_extension_bundle(value)


class _FakeConnection:
    def __init__(self):
        self.statements = []
        self.inserted = []

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        return self

    def executemany(self, sql, rows):
        self.statements.append((sql, None))
        self.inserted.extend(rows)
        return self


def _write_shard(root: Path, bundle):
    shard = root / "shard"
    ext = shard / "ext"
    ext.mkdir(parents=True)
    manifest = {
        "spec_version": "1.0.0",
        "extensions": sorted(bundle),
        "integrity": {"merkle_root": "fixture"},
    }
    (shard / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    for extension_id, rows in bundle.items():
        payload = "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows)
        (ext / f"{extension_id}.jsonl").write_text(payload, encoding="utf-8")
    return shard


def test_mounts_atomically_with_out_of_band_key(tmp_path, monkeypatch):
    shard = _write_shard(tmp_path, _bundle())
    trusted = tmp_path / "trusted.pub"
    trusted.write_bytes(b"trusted")
    monkeypatch.setattr(aperture_runtime, "_verify_genesis", lambda _shard, _key: {"status": "PASS"})
    connection = _FakeConnection()
    runtime = aperture.ApertureExtensionRuntime(SimpleNamespace(con=connection))

    mount = runtime.mount_verified_shard(shard, trusted)
    assert mount.authority == "rebuildable_query_cache_only"
    assert len(mount.tables) == 7
    assert runtime.catalog()["mounts"][0]["manifest_sha256"] == mount.manifest_sha256
    assert any('CREATE VIEW "aperture_facts"' in sql for sql, _ in connection.statements)

    runtime.unmount(mount.mount_id)
    assert runtime.catalog()["mounts"] == []
    assert any("DROP TABLE IF EXISTS" in sql for sql, _ in connection.statements)


def test_refuses_embedded_trust_anchor(tmp_path, monkeypatch):
    shard = _write_shard(tmp_path, _bundle())
    embedded = shard / "sig" / "publisher.pub"
    embedded.parent.mkdir()
    embedded.write_bytes(b"embedded")
    monkeypatch.setattr(aperture_runtime, "_verify_genesis", lambda _shard, _key: {"status": "PASS"})
    runtime = aperture.ApertureExtensionRuntime(SimpleNamespace(con=_FakeConnection()))
    with pytest.raises(aperture.ApertureExtensionError, match="outside the shard"):
        runtime.mount_verified_shard(shard, embedded)
