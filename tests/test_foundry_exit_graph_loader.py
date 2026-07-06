"""Foundry Exit graph loader — downstream of the sealed bundle.

Verification-gated: the loader reads ontology/lineage ONLY after genesis verifies
the sealed shard with an out-of-band key. Pure tests (projection, determinism,
external-id preservation, boundaries) always run; seal/verify-gated tests skip
cleanly without the genesis kernel.

Evidence tier: recreate a queryable ontology+lineage graph from an already-sealed,
genesis-verified bundle — no Palantir, no S3, no GhostBox.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from foundry_exit import graph_loader as gl
from foundry_exit.graph_loader import (
    LoadRefused,
    LoadedGraph,
    build_graph_projection,
    load_verified_graph,
    to_cypher,
    write_graph_export,
)
from foundry_exit.seal import VerifyStatus, kernel_available

requires_kernel = pytest.mark.skipif(
    not kernel_available(), reason="axm-genesis kernel (axm-build / axm-verify) not on PATH"
)

CSV_SHA = "7fdff1bdcc231787991339f939beebed8f56140217e1e2f9afe00b610386fa2c"

# --- shared synthetic inputs (with properties + links) -----------------------

ONTOLOGY = {
    "object_types": [
        {
            "object_type_id": "Order",
            "properties": [
                {"id": "Order.total", "data_type": "double"},
                {"id": "Order.status", "data_type": "string"},
            ],
            "links": [
                {"id": "Order.customer", "cardinality": "MANY_TO_ONE", "target_object_type_id": "Customer"},
            ],
            "backing_dataset_rids": ["ri.foundry.main.dataset.orders"],
            "security_markings": ["SECRET", "NOFORN"],
        }
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
DATASETS = [
    {
        "dataset_rid": "ri.foundry.main.dataset.orders",
        "branch": "master",
        "version": "v1",
        "object_path": "orders.csv",
        "file_format": "csv",
        "size_bytes": 79,
        "checksum": CSV_SHA,
    }
]


def _projection():
    return build_graph_projection(
        ontology=ONTOLOGY, lineage=LINEAGE, datasets=DATASETS, source_system="palantir-foundry"
    )


def _node(graph, node_id):
    return next(n for n in graph["nodes"] if n["id"] == node_id)


def _has_edge(graph, src, rel, dst):
    return any(e["src"] == src and e["rel"] == rel and e["dst"] == dst for e in graph["edges"])


# --- kernel: seal a real bundle so the verify gate can be exercised ----------


@pytest.fixture(scope="module")
def sealed(tmp_path_factory):
    if not kernel_available():
        pytest.skip("kernel not available")
    from foundry_exit.planes import (
        DatasetExport,
        DatasetObject,
        FoundryExitManifest,
        LineageEdge,
        OntologyObjectType,
    )
    from foundry_exit.seal import seal_from_manifest

    manifest = FoundryExitManifest(
        source_system="palantir-foundry",
        datasets=(
            DatasetExport(
                dataset_rid="ri.foundry.main.dataset.orders",
                objects=(DatasetObject(object_path="orders.csv", file_format="csv", size_bytes=79, checksum=CSV_SHA),),
                branch="master",
                version="v1",
            ),
        ),
        object_types=(
            OntologyObjectType(
                object_type_id="Order",
                properties=({"id": "Order.total", "data_type": "double"}, {"id": "Order.status", "data_type": "string"}),
                links=({"id": "Order.customer", "cardinality": "MANY_TO_ONE", "target_object_type_id": "Customer"},),
                backing_dataset_rids=("ri.foundry.main.dataset.orders",),
                security_markings=("SECRET", "NOFORN"),
            ),
        ),
        lineage=(
            LineageEdge(
                upstream_dataset_rid="ri.foundry.main.dataset.raw_orders",
                downstream_dataset_rid="ri.foundry.main.dataset.orders",
                transform_ref="ri.foundry.main.transform.clean_orders",
                produces_object_type_id="Order",
            ),
        ),
    )
    work = tmp_path_factory.mktemp("graph")
    _bundle, shard = seal_from_manifest(manifest, work)
    return shard


# === requirement: verified bundle loads =====================================


@requires_kernel
def test_verified_bundle_loads(sealed):
    loaded = load_verified_graph(sealed.shard_dir, sealed.trusted_key_path)
    assert loaded.provenance["verified"] is True
    assert loaded.provenance["verify_status"] == "pass"
    assert loaded.provenance["detached_status"] == "PASS"
    assert loaded.provenance["ghostbox_involved"] is False
    assert loaded.provenance["palantir_involved"] is False
    assert loaded.provenance["s3_involved"] is False
    # graph recreated from verified bytes
    g = loaded.graph
    assert _node(g, "Order")["kind"] == "object_type"
    assert _has_edge(g, "Order", "backed_by", "ri.foundry.main.dataset.orders")
    assert _has_edge(g, "ri.foundry.main.dataset.orders", "derives_from", "ri.foundry.main.dataset.raw_orders")


# === requirement: wrong key blocks load =====================================


@requires_kernel
def test_wrong_key_blocks_load(sealed, tmp_path):
    import subprocess

    subprocess.run(["axm-build", "keygen", str(tmp_path), "--name", "attacker"], check=True, capture_output=True, text=True)
    with pytest.raises(LoadRefused) as ei:
        load_verified_graph(sealed.shard_dir, tmp_path / "attacker.pub")
    assert ei.value.status is VerifyStatus.FAIL


# === requirement: missing key blocks load (before any read) =================


def test_missing_key_blocks_load(tmp_path):
    # No CLI is invoked and no content is read: None short-circuits to refusal.
    with pytest.raises(LoadRefused) as ei:
        load_verified_graph(tmp_path, None)
    assert ei.value.status is VerifyStatus.NO_TRUSTED_KEY


# === requirement: malformed bundle blocks load ==============================


@requires_kernel
def test_tampered_bundle_blocks_load(sealed, tmp_path):
    import shutil

    tampered = tmp_path / "shard"
    shutil.copytree(sealed.shard_dir, tampered)
    # Tamper a sealed content file -> signature no longer matches -> refuse.
    ont = tampered / "content" / "ontology.json"
    ont.write_text(ont.read_text() + "\n// tampered\n", encoding="utf-8")
    with pytest.raises(LoadRefused) as ei:
        load_verified_graph(tampered, sealed.trusted_key_path)
    assert ei.value.status in (VerifyStatus.FAIL, VerifyStatus.MALFORMED)


@requires_kernel
def test_non_shard_dir_blocks_load(sealed, tmp_path):
    (tmp_path / "content").mkdir()
    with pytest.raises(LoadRefused) as ei:
        load_verified_graph(tmp_path, sealed.trusted_key_path)
    assert ei.value.status in (VerifyStatus.FAIL, VerifyStatus.MALFORMED)


# === requirement: never reads content before verifying ======================


def test_no_content_read_when_verification_is_refused(tmp_path, monkeypatch):
    # If _load_json/_load_jsonl were ever reached on a refused bundle, this blows
    # up. A refusal must happen strictly before any content read.
    def boom(*a, **k):
        raise AssertionError("content was read before verification passed")

    monkeypatch.setattr(gl, "_load_json", boom)
    monkeypatch.setattr(gl, "_load_jsonl", boom)
    with pytest.raises(LoadRefused):
        load_verified_graph(tmp_path, None)  # missing key -> refuse before any read


# === requirement: ontology nodes preserve external IDs verbatim =============


def test_ontology_nodes_preserve_external_ids_verbatim():
    g = _projection()
    assert _node(g, "Order")["external_id"] == "Order"
    # property + relationship nodes keep the Palantir ids verbatim
    assert _node(g, "Order::property::Order.total")["external_id"] == "Order.total"
    assert _node(g, "Order::relationship::Order.customer")["external_id"] == "Order.customer"
    assert _node(g, "ri.foundry.main.transform.clean_orders")["external_id"] == "ri.foundry.main.transform.clean_orders"
    # object-property + object-relationship edges
    assert _has_edge(g, "Order", "has_property", "Order::property::Order.total")
    assert _has_edge(g, "Order", "has_relationship", "Order::relationship::Order.customer")
    # exported file node from the sealed dataset manifest
    fnode = _node(g, "ri.foundry.main.dataset.orders::file::orders.csv")
    assert fnode["kind"] == "file" and fnode["external_id"] == "orders.csv"
    assert fnode["attributes"]["checksum"] == CSV_SHA


def test_no_custody_id_is_generated_from_palantir_ids():
    g = _projection()
    # No node id or external id is an AXM custody id (sh1_...).
    for n in g["nodes"]:
        assert not str(n["id"]).startswith("sh1_")
        assert not str(n.get("external_id") or "").startswith("sh1_")


def test_security_markings_are_metadata_only():
    g = _projection()
    order = _node(g, "Order")
    assert list(order["attributes"]["security_markings"]) == ["SECRET", "NOFORN"]
    # markings never become edges/permissions
    assert not any("SECRET" in (e["src"], e["dst"]) for e in g["edges"])


# === requirement: lineage edges preserve source and target IDs verbatim =====


def test_lineage_edges_preserve_source_and_target_verbatim():
    g = _projection()
    assert _has_edge(g, "ri.foundry.main.dataset.orders", "derives_from", "ri.foundry.main.dataset.raw_orders")
    # transform input + output edges carry the ids verbatim
    assert _has_edge(g, "ri.foundry.main.dataset.raw_orders", "input_to", "ri.foundry.main.transform.clean_orders")
    assert _has_edge(g, "ri.foundry.main.transform.clean_orders", "produces", "ri.foundry.main.dataset.orders")
    assert _has_edge(g, "ri.foundry.main.transform.clean_orders", "produces_object_type", "Order")


# === requirement: graph export is deterministic =============================


def test_projection_is_deterministic():
    a = json.dumps(_projection(), sort_keys=True)
    b = json.dumps(_projection(), sort_keys=True)
    assert a == b


def test_export_bytes_are_deterministic(tmp_path):
    loaded = LoadedGraph(
        graph=_projection(),
        provenance={"verified": True, "verify_status": "pass", "detached_status": "PASS"},
    )
    p1 = write_graph_export(loaded, tmp_path / "a")["graph"].read_bytes()
    p2 = write_graph_export(loaded, tmp_path / "b")["graph"].read_bytes()
    assert p1 == p2
    # cypher is deterministic too
    c1 = write_graph_export(loaded, tmp_path / "c")["cypher"].read_bytes()
    c2 = write_graph_export(loaded, tmp_path / "d")["cypher"].read_bytes()
    assert c1 == c2


def test_cypher_is_wellformed_and_covers_nodes_and_edges():
    g = _projection()
    stmts = to_cypher(g)
    assert all(s.endswith(";") for s in stmts)
    assert sum(s.startswith("MERGE (x:") for s in stmts) == g["stats"]["node_count"]
    assert sum(s.startswith("MATCH (a") for s in stmts) == g["stats"]["edge_count"]
    assert any('MERGE (x:ObjectType {id: "Order"})' in s for s in stmts)


# === requirement: boundaries — no ghostbox / no Palantir / no S3 ============


def test_loader_does_not_import_ghostbox():
    import sys

    assert not any(n == "ghostbox" or n.startswith("ghostbox.") for n in sys.modules)
    src = inspect.getsource(gl)
    assert "import ghostbox" not in src and "from ghostbox" not in src


def test_loader_does_not_call_palantir_or_s3():
    src = inspect.getsource(gl)
    for forbidden in ("import boto3", "from .adapters", "S3ExportSource", "from .importer",
                      "from .live_probe", "import requests", "urllib.request", "http.client"):
        assert forbidden not in src, f"graph loader must not reference {forbidden!r}"


def test_loader_reads_only_from_the_sealed_bundle():
    # The only file reads are content/* under the shard dir; the module pulls in
    # the genesis verifier + the VerifyStatus taxonomy, nothing that imports or
    # fetches. Assert the read helpers only ever touch a shard-relative content dir.
    src = inspect.getsource(gl)
    assert '/ "content"' in src or '"content"' in src
    assert "from .exit_test import verify_detached" in src
    # no source-fetch surface reachable from the loader
    assert ".read_bytes(" not in src and ".list_objects(" not in src


# === requirement: optional FalkorDB path skips cleanly if unavailable =======


def test_falkordb_path_skips_cleanly_when_unavailable():
    if gl.falkordb_available():
        pytest.skip("falkordb installed; the clean-skip contract is not exercised")
    assert gl.falkordb_available() is False
    with pytest.raises(RuntimeError) as ei:
        gl.load_into_falkordb(_projection())
    assert "falkordb" in str(ei.value).lower()
