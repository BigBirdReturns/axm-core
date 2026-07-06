"""Foundry S3 export surface — proven against a high-fidelity simulation.

No credentials, no moto, no network: a pure-Python ``FoundryS3Sim`` faithfully
models the real surface (pagination, versioning, security markings, permission
denial), and the REAL ``S3ExportSource`` code path runs against it unchanged.
This turns the earlier probe's *recorded* gaps (paging / versioning / markings /
permissions) into *proven* behavior — and pins the silent-truncation fix.

Evidence tier: ``sim-foundry-s3`` — high-fidelity simulation, explicitly NOT an
authorized live Foundry run. Seal/verify tests skip cleanly without the kernel.
"""
from __future__ import annotations

import hashlib

import pytest

from foundry_exit.adapters import ExportPermissionError, S3Config, S3ExportSource
from foundry_exit.live_probe import run_live_probe
from foundry_exit.seal import kernel_available
from foundry_exit.sim_surface import FoundryS3Sim, SimClientError

requires_kernel = pytest.mark.skipif(
    not kernel_available(), reason="axm-genesis kernel (axm-build / axm-verify) not on PATH"
)

BUCKET = "foundry-export-sim"
SCOPE = "datasets/orders/"


def _source(sim: FoundryS3Sim) -> S3ExportSource:
    # Config prefix stays empty; scope via the list prefix so listing keys
    # round-trip through read_bytes (the recorded object-layout constraint).
    return S3ExportSource(S3Config(endpoint_url="sim://", bucket=BUCKET, prefix=""), client=sim)


# === pagination: the >1000-key truncation bug, closed =======================


def test_listing_pages_past_the_page_cap():
    # 250 objects, page size 100 -> 3 pages. The fixed adapter returns all 250.
    sim = FoundryS3Sim(bucket=BUCKET, page_size=100)
    for i in range(250):
        sim.put(f"{SCOPE}part-{i:04d}.csv", f"row-{i}".encode())
    keys = _source(sim).list_objects(SCOPE)
    assert len(keys) == 250
    assert keys[0].endswith("part-0000.csv") and keys[-1].endswith("part-0249.csv")


def test_single_call_would_have_silently_truncated():
    # Demonstrate the defect the paging fix closes: one list_objects_v2 call
    # sees only the first page and reports no error — silent evidence loss.
    sim = FoundryS3Sim(bucket=BUCKET, page_size=100)
    for i in range(250):
        sim.put(f"{SCOPE}part-{i:04d}.csv", b"x")
    first_page = sim.list_objects_v2(Bucket=BUCKET, Prefix=SCOPE)
    assert len(first_page["Contents"]) == 100          # capped
    assert first_page["IsTruncated"] is True           # ...and it SAYS so
    assert first_page.get("NextContinuationToken")     # the token the bug ignored
    # the fixed adapter honors that token; a naive `Contents`-only read would not
    assert len(_source(sim).list_objects(SCOPE)) == 250


def test_max_keys_never_exceeds_page_cap():
    sim = FoundryS3Sim(bucket=BUCKET, page_size=1000)
    for i in range(5):
        sim.put(f"{SCOPE}o{i}", b"x")
    resp = sim.list_objects_v2(Bucket=BUCKET, Prefix=SCOPE, MaxKeys=10_000)
    assert resp["MaxKeys"] == 1000                     # real S3 caps regardless


# === fetch + checksums over a paged dataset =================================


def test_read_bytes_round_trips_every_paged_object():
    sim = FoundryS3Sim(bucket=BUCKET, page_size=50)
    bodies = {f"{SCOPE}part-{i:04d}.csv": f"payload-{i}".encode() for i in range(120)}
    for k, v in bodies.items():
        sim.put(k, v)
    src = _source(sim)
    for key in src.list_objects(SCOPE):
        assert src.read_bytes(key) == bodies[key]      # keys round-trip through get


# === versioning =============================================================


def test_versioning_latest_and_pinned():
    sim = FoundryS3Sim(bucket=BUCKET)
    key = f"{SCOPE}orders.csv"
    v1 = sim.put(key, b"v1 bytes")
    v2 = sim.put(key, b"v2 bytes")
    src = _source(sim)
    assert src.read_bytes(key) == b"v2 bytes"          # latest by default
    meta = src.object_metadata(key)
    assert meta["version_id"] == v2 and v2 != v1
    # a specific prior version is still reachable via the client surface
    assert sim.get_object(Bucket=BUCKET, Key=key, VersionId=v1)["Body"].read() == b"v1 bytes"


# === security markings: recorded, never made portable =======================


def test_markings_are_recorded_as_metadata_only():
    sim = FoundryS3Sim(bucket=BUCKET)
    key = f"{SCOPE}classified.csv"
    sim.put(key, b"sensitive", marking="EXPORT-CONTROLLED")
    meta = _source(sim).object_metadata(key)
    assert meta["markings"] == ["EXPORT-CONTROLLED"]
    # markings are metadata; reading bytes does not gate on or re-create them
    assert _source(sim).read_bytes(key) == b"sensitive"


# === permission denial: surfaced, not silently widened ======================


def test_denied_prefix_raises_export_permission_error():
    sim = FoundryS3Sim(bucket=BUCKET).deny("datasets/restricted/")
    sim.put("datasets/restricted/secret.csv", b"x")
    sim.put(f"{SCOPE}ok.csv", b"y")
    src = _source(sim)
    assert src.list_objects(SCOPE) == [f"{SCOPE}ok.csv"]     # allowed prefix fine
    with pytest.raises(ExportPermissionError):
        src.list_objects("datasets/restricted/")
    with pytest.raises(ExportPermissionError):
        src.read_bytes("datasets/restricted/secret.csv")


def test_sim_error_shape_matches_botocore():
    err = SimClientError("AccessDenied", "nope")
    assert err.response["Error"]["Code"] == "AccessDenied"   # what the adapter reads


# === end-to-end probe over a multi-page dataset =============================


@requires_kernel
def test_probe_over_paged_dataset_seals_all_objects(tmp_path):
    sim = FoundryS3Sim(bucket=BUCKET, page_size=64)
    bodies = {f"{SCOPE}part-{i:04d}.csv": f"order,{i}\n".encode() for i in range(200)}
    for k, v in bodies.items():
        sim.put(k, v)
    ledger = run_live_probe(
        _source(sim),
        dataset_rid="ri.foundry.main.dataset.orders",
        prefix=SCOPE,
        endpoint_class="sim-foundry-s3 (high-fidelity simulation, NOT authorized live Foundry)",
        ontology={"object_types": [{"object_type_id": "Order"}]},
        lineage={"edges": []},
        out_dir=tmp_path / "out",
        stage_dir=tmp_path / "staged",
    )
    assert ledger["object_count"] == 200                    # ALL pages, not just the first
    assert ledger["total_bytes"] == sum(len(v) for v in bodies.values())
    # every checksum matches the real (sim) bytes
    by_path = {r["object_path"]: r for r in ledger["checksum_manifest"]}
    for key, body in bodies.items():
        assert by_path[key]["checksum"] == hashlib.sha256(body).hexdigest()
    assert ledger["sealed"] is True
    assert ledger["verification"] == "pass"
    assert ledger["detached"]["status"] == "PASS"
    assert ledger["detached"]["palantir_involved"] is False
