"""Foundry Exit Intake v0 — importer shape, sealed bundle format, and exit property.

Kernel-dependent tests (seal/verify through real genesis) skip cleanly without
`axm-build`/`axm-verify`. Pure tests (import, adapters, bundle, boundaries) always
run. Evidence tier: authorized-export readiness against fixtures, not live Palantir.
"""
from __future__ import annotations

import inspect
import json
import subprocess
from pathlib import Path

import pytest

from foundry_exit import adapters as adapters_mod
from foundry_exit.adapters import FilesystemExportSource, S3ExportSource
from foundry_exit.bundle import build_bundle
from foundry_exit.exit_test import verify_detached
from foundry_exit.importer import ChecksumMismatch, FoundryExitImporter, load_json
from foundry_exit.seal import (
    VerifyStatus,
    kernel_available,
    seal_exit_bundle,
    verify_exit_bundle,
)

FIXTURE = Path(__file__).resolve().parent.parent / "samples" / "foundry_exit_fixture"
CSV_SHA256 = "7fdff1bdcc231787991339f939beebed8f56140217e1e2f9afe00b610386fa2c"

requires_kernel = pytest.mark.skipif(
    not kernel_available(), reason="axm-genesis kernel (axm-build / axm-verify) not on PATH"
)


def _import_manifest(stage_dir=None):
    source = FilesystemExportSource(FIXTURE)
    importer = FoundryExitImporter(source, stage_dir=stage_dir)
    return importer.import_export(
        inventory=load_json(FIXTURE / "inventory.json"),
        ontology=load_json(FIXTURE / "ontology.json"),
        lineage=load_json(FIXTURE / "lineage.json"),
    )


@pytest.fixture(scope="module")
def sealed_bundle(tmp_path_factory):
    if not kernel_available():
        pytest.skip("kernel not available")
    work = tmp_path_factory.mktemp("fx")
    manifest = _import_manifest(stage_dir=work / "staged")
    bundle_dir = build_bundle(manifest, work / "bundle")
    sealed = seal_exit_bundle(manifest, bundle_dir, work / "shard")
    return manifest, sealed, work


# --- pure: import / metadata preservation -----------------------------------


def test_import_preserves_palantir_ids_verbatim_as_external_ids():
    m = _import_manifest()
    assert m.datasets[0].dataset_rid == "ri.foundry.main.dataset.orders"
    assert m.object_types[0].object_type_id == "Order"
    assert m.lineage[0].transform_ref == "ri.foundry.main.transform.clean_orders"
    ext = m.external_ids()
    assert "ri.foundry.main.dataset.orders" in ext and "Order" in ext
    assert "ri.foundry.main.dataset.raw_orders" in ext


def test_s3_object_checksums_recorded_and_stable():
    m1 = _import_manifest()
    m2 = _import_manifest()
    obj = m1.datasets[0].objects[0]
    assert obj.checksum == CSV_SHA256          # matches the real dataset bytes
    assert obj.size_bytes == 79
    assert obj.checksum == m2.datasets[0].objects[0].checksum  # stable across runs


def test_checksum_mismatch_is_rejected(tmp_path):
    inv = load_json(FIXTURE / "inventory.json")
    inv["datasets"][0]["objects"][0]["checksum"] = "deadbeef"  # wrong
    imp = FoundryExitImporter(FilesystemExportSource(FIXTURE))
    with pytest.raises(ChecksumMismatch):
        imp.import_export(inventory=inv, ontology={"object_types": []}, lineage={"edges": []})


# --- pure: read-only adapters, no source write path -------------------------


def test_adapters_have_no_source_write_path():
    forbidden = {"put", "write", "upload", "delete", "remove", "create", "set", "save", "put_object", "upload_file"}
    for cls in (FilesystemExportSource, S3ExportSource):
        methods = {n for n in dir(cls) if not n.startswith("_")}
        assert methods & forbidden == set(), f"{cls.__name__} exposes a write method: {methods & forbidden}"
        assert "read_bytes" in methods and "list_objects" in methods


# --- pure: GhostBox is not in the import path -------------------------------


def test_no_ghostbox_in_the_import_path():
    import sys
    import foundry_exit
    import foundry_exit.adapters, foundry_exit.importer, foundry_exit.seal, foundry_exit.bundle, foundry_exit.packet, foundry_exit.exit_test
    # Importing the whole import path never pulled ghostbox into the process...
    assert not any(name == "ghostbox" or name.startswith("ghostbox.") for name in sys.modules)
    # ...and no module actually imports it (check import statements, not the
    # docstring prose, which legitimately names GhostBox to say it's excluded).
    for mod in (foundry_exit, adapters_mod, foundry_exit.importer, foundry_exit.seal,
                foundry_exit.bundle, foundry_exit.packet, foundry_exit.exit_test):
        src = inspect.getsource(mod)
        assert "import ghostbox" not in src and "from ghostbox" not in src


# --- pure: bundle includes ontology + lineage -------------------------------


def test_bundle_includes_ontology_and_lineage(tmp_path):
    m = _import_manifest()
    out = build_bundle(m, tmp_path / "bundle")
    ontology = json.loads((out / "ontology.json").read_text())
    lineage = json.loads((out / "lineage.json").read_text())
    assert ontology["object_types"][0]["object_type_id"] == "Order"
    assert lineage["edges"][0]["downstream_dataset_rid"] == "ri.foundry.main.dataset.orders"


# --- kernel: sealed bundle format + verification ----------------------------


@requires_kernel
def test_fixture_import_produces_sealed_hybrid1_shard(sealed_bundle):
    _m, sealed, _w = sealed_bundle
    shard = Path(sealed.shard_dir)
    assert (shard / "manifest.json").exists() and (shard / "sig" / "manifest.sig").exists()
    assert sealed.suite == "axm-hybrid1"


@requires_kernel
def test_sealed_bundle_verifies_with_correct_out_of_band_key(sealed_bundle):
    _m, sealed, _w = sealed_bundle
    assert verify_exit_bundle(sealed.shard_dir, sealed.trusted_key_path) is VerifyStatus.PASS


@requires_kernel
def test_wrong_key_fails(sealed_bundle, tmp_path):
    _m, sealed, _w = sealed_bundle
    subprocess.run(["axm-build", "keygen", str(tmp_path), "--name", "attacker"], check=True, capture_output=True, text=True)
    assert verify_exit_bundle(sealed.shard_dir, tmp_path / "attacker.pub") is VerifyStatus.FAIL


@requires_kernel
def test_missing_key_is_no_anchor(sealed_bundle):
    _m, sealed, _w = sealed_bundle
    assert verify_exit_bundle(sealed.shard_dir, None) is VerifyStatus.NO_TRUSTED_KEY


@requires_kernel
def test_shard_id_is_genesis_derived_only(sealed_bundle):
    from axm_verify.crypto import derive_shard_id

    m, sealed, _w = sealed_bundle
    manifest_bytes = (Path(sealed.shard_dir) / "manifest.json").read_bytes()
    assert sealed.shard_id == derive_shard_id(manifest_bytes)
    assert sealed.shard_id.startswith("sh1_")
    assert sealed.shard_id not in m.external_ids()  # never a Palantir id


@requires_kernel
def test_ontology_and_lineage_are_in_the_sealed_shard(sealed_bundle):
    _m, sealed, _w = sealed_bundle
    content = Path(sealed.shard_dir) / "content"
    ontology = json.loads((content / "ontology.json").read_text())
    lineage = json.loads((content / "lineage.json").read_text())
    assert ontology["object_types"][0]["object_type_id"] == "Order"
    assert lineage["edges"][0]["upstream_dataset_rid"] == "ri.foundry.main.dataset.raw_orders"


@requires_kernel
def test_datasets_manifest_carries_checksums_in_the_sealed_shard(sealed_bundle):
    _m, sealed, _w = sealed_bundle
    rows = [
        json.loads(line)
        for line in (Path(sealed.shard_dir) / "content" / "datasets.manifest.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert any(r["checksum"] == CSV_SHA256 and r["dataset_rid"] == "ri.foundry.main.dataset.orders" for r in rows)


@requires_kernel
def test_exit_property_survives_importer_palantir_and_ghostbox(sealed_bundle):
    _m, sealed, _w = sealed_bundle
    res = verify_detached(sealed.shard_dir, sealed.trusted_key_path)
    assert res["status"] == "PASS" and res["exit_code"] == 0
    assert res["importer_involved"] is False
    assert res["ghostbox_involved"] is False
    assert res["palantir_involved"] is False
