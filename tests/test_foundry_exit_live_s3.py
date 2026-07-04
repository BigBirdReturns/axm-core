"""Live S3-compatible extraction probe — adapter conformance.

These tests drive the REAL v0 ``S3ExportSource`` (boto3 ``list_objects_v2`` /
``get_object``) end to end through the live probe, against a local ``moto``
S3-compatible mock.

Evidence tier — READ THIS BEFORE TRUSTING THE RESULT:
    This is ADAPTER CONFORMANCE against a local ``moto`` stand-in. It is NOT an
    authorized Foundry run. It proves the code path (list -> fetch -> checksum ->
    v0 manifest -> seal -> verify -> detached) is correct against an S3-compatible
    surface; it does NOT prove anything about a real Palantir Foundry endpoint,
    its permissions, paging, versioning, or markings. A run against authorized
    Foundry credentials is a separate, still-open step.

boto3 + moto are optional; the whole module skips cleanly without them. The
seal/verify assertions additionally skip without the genesis kernel on PATH.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

boto3 = pytest.importorskip("boto3", reason="boto3 not installed")
moto = pytest.importorskip("moto", reason="moto not installed")
from moto import mock_aws  # noqa: E402

from foundry_exit.adapters import S3Config, S3ExportSource  # noqa: E402
from foundry_exit import live_probe  # noqa: E402
from foundry_exit.live_probe import build_inventory_from_listing, run_live_probe  # noqa: E402
from foundry_exit.seal import kernel_available  # noqa: E402

BUCKET = "axm-foundry-export-mock"
REGION = "us-east-1"
ENDPOINT = "https://s3.us-east-1.amazonaws.com"
SCOPE = "datasets/orders/"

# One small scoped dataset/prefix: a couple of tiny objects plus a decoy that
# must NOT be listed because it is outside the scoped prefix.
OBJECTS = {
    SCOPE + "part-0000.csv": b"order_id,total\n1,10\n2,25\n",
    SCOPE + "part-0001.csv": b"order_id,total\n3,7\n",
    SCOPE + "_SUCCESS": b"",
}
OUT_OF_SCOPE = {"datasets/customers/part-0000.csv": b"nope\n"}

ONTOLOGY = {
    "object_types": [
        {"object_type_id": "Order", "backing_dataset_rids": ["ri.foundry.main.dataset.orders"]}
    ]
}
LINEAGE = {
    "edges": [
        {
            "upstream_dataset_rid": "ri.foundry.main.dataset.raw_orders",
            "downstream_dataset_rid": "ri.foundry.main.dataset.orders",
            "transform_ref": "ri.foundry.main.transform.clean_orders",
            "produces_object_type_id": "Order",
        }
    ]
}

requires_kernel = pytest.mark.skipif(
    not kernel_available(), reason="axm-genesis kernel (axm-build / axm-verify) not on PATH"
)


@pytest.fixture
def s3_creds(monkeypatch):
    # Dummy creds for moto only. The adapter reads AXM_S3_* from the env; never
    # committed, never real. moto ignores their value.
    monkeypatch.setenv("AXM_S3_ACCESS_KEY", "testing")
    monkeypatch.setenv("AXM_S3_SECRET_KEY", "testing")


def _seed_bucket():
    client = boto3.client("s3", endpoint_url=ENDPOINT, region_name=REGION)
    client.create_bucket(Bucket=BUCKET)
    for key, body in {**OBJECTS, **OUT_OF_SCOPE}.items():
        client.put_object(Bucket=BUCKET, Key=key, Body=body)


def _source():
    # Config prefix stays empty; scoping is done via the list prefix so listing
    # keys round-trip through read_bytes (see live_probe.main).
    return S3ExportSource(S3Config(endpoint_url=ENDPOINT, bucket=BUCKET, prefix="", region=REGION))


# --- adapter conformance: the real boto3 read-only calls work ----------------


@mock_aws
def test_s3_adapter_lists_only_the_scoped_prefix(s3_creds):
    _seed_bucket()
    keys = _source().list_objects(SCOPE)
    assert set(keys) == set(OBJECTS)                      # scoped in
    assert "datasets/customers/part-0000.csv" not in keys  # scoped out


@mock_aws
def test_s3_adapter_fetches_exact_bytes(s3_creds):
    _seed_bucket()
    src = _source()
    for key, body in OBJECTS.items():
        assert src.read_bytes(key) == body               # keys round-trip


@mock_aws
def test_listing_drives_a_v0_shaped_inventory(s3_creds):
    _seed_bucket()
    inv = build_inventory_from_listing(
        _source(), dataset_rid="ri.foundry.main.dataset.orders", prefix=SCOPE
    )
    paths = {o["object_path"] for o in inv["datasets"][0]["objects"]}
    assert paths == set(OBJECTS)
    csv = next(o for o in inv["datasets"][0]["objects"] if o["object_path"].endswith(".csv"))
    assert csv["file_format"] == "csv"                   # format inferred from suffix


# --- full probe: list -> fetch -> checksum -> manifest -----------------------


@mock_aws
def test_probe_checksums_match_the_real_mock_bytes(s3_creds, tmp_path):
    import hashlib

    _seed_bucket()
    ledger = run_live_probe(
        _source(),
        dataset_rid="ri.foundry.main.dataset.orders",
        prefix=SCOPE,
        endpoint_class="local-moto-mock (NOT authorized Foundry)",
        ontology=ONTOLOGY,
        lineage=LINEAGE,
        out_dir=tmp_path / "out",
        stage_dir=tmp_path / "staged",
    )
    assert ledger["object_count"] == len(OBJECTS)
    assert ledger["total_bytes"] == sum(len(b) for b in OBJECTS.values())
    by_path = {r["object_path"]: r for r in ledger["checksum_manifest"]}
    for key, body in OBJECTS.items():
        assert by_path[key]["checksum"] == hashlib.sha256(body).hexdigest()
        assert by_path[key]["size_bytes"] == len(body)


@mock_aws
def test_probe_attaches_only_explicit_ontology_and_lineage(s3_creds, tmp_path):
    _seed_bucket()
    # With explicit metadata: recorded, no gaps for it.
    ledger = run_live_probe(
        _source(),
        dataset_rid="ri.foundry.main.dataset.orders",
        prefix=SCOPE,
        endpoint_class="local-moto-mock (NOT authorized Foundry)",
        ontology=ONTOLOGY,
        lineage=LINEAGE,
        out_dir=tmp_path / "out",
        stage_dir=tmp_path / "staged",
    )
    assert ledger["ontology_object_types"] == ["Order"]
    assert ledger["lineage_edges"] == 1
    assert not any("ontology metadata input" in g for g in ledger["gaps"])

    # Without metadata: S3 does NOT supply ontology; the probe records the gap
    # instead of inventing it.
    bare = run_live_probe(
        _source(),
        dataset_rid="ri.foundry.main.dataset.orders",
        prefix=SCOPE,
        endpoint_class="local-moto-mock (NOT authorized Foundry)",
        out_dir=tmp_path / "out2",
        stage_dir=tmp_path / "staged2",
    )
    assert bare["ontology_object_types"] == []
    assert any("ontology" in g for g in bare["gaps"])
    assert any("lineage" in g for g in bare["gaps"])


# --- full probe through genesis: seal / verify / detached --------------------


@requires_kernel
@mock_aws
def test_probe_seals_and_verifies_through_genesis(s3_creds, tmp_path):
    _seed_bucket()
    ledger = run_live_probe(
        _source(),
        dataset_rid="ri.foundry.main.dataset.orders",
        prefix=SCOPE,
        endpoint_class="local-moto-mock (NOT authorized Foundry)",
        ontology=ONTOLOGY,
        lineage=LINEAGE,
        out_dir=tmp_path / "out",
        stage_dir=tmp_path / "staged",
    )
    assert ledger["sealed"] is True
    assert isinstance(ledger["shard_id"], str) and ledger["shard_id"].startswith("sh1_")
    assert ledger["verification"] == "pass"


@requires_kernel
@mock_aws
def test_probe_exit_property_holds_after_removing_everything(s3_creds, tmp_path):
    _seed_bucket()
    ledger = run_live_probe(
        _source(),
        dataset_rid="ri.foundry.main.dataset.orders",
        prefix=SCOPE,
        endpoint_class="local-moto-mock (NOT authorized Foundry)",
        ontology=ONTOLOGY,
        lineage=LINEAGE,
        out_dir=tmp_path / "out",
        stage_dir=tmp_path / "staged",
    )
    detached = ledger["detached"]
    assert detached["status"] == "PASS"
    assert detached["importer_involved"] is False
    assert detached["ghostbox_involved"] is False
    assert detached["palantir_involved"] is False


# --- the honest refusal + no ghostbox in the probe path ----------------------


def test_main_refuses_without_an_authorized_foundry_surface(monkeypatch, capsys):
    for var in ("FOUNDRY_S3_ENDPOINT", "FOUNDRY_S3_BUCKET", "FOUNDRY_DATASET_RID"):
        monkeypatch.delenv(var, raising=False)
    assert live_probe.main([]) == 2
    out = capsys.readouterr().out
    assert "REFUSED" in out
    assert "never uses arbitrary S3 credentials" in out


def test_probe_never_imports_ghostbox():
    import sys

    assert not any(n == "ghostbox" or n.startswith("ghostbox.") for n in sys.modules)
    src = inspect.getsource(live_probe)
    # Check import statements, not docstring prose.
    assert "import ghostbox" not in src and "from ghostbox" not in src
