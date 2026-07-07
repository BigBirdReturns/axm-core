"""Pipeline Exit v0 — loader shape, sealed shard, and the PRODUCT property: the
sealed pipeline (schemas + dependency DAG + provenance) is queryable through the
repo's own Spectra engine.

Kernel-free tests (parse/edges/candidates/errors) always run. Kernel-gated tests
(seal/verify through real genesis) skip cleanly without `axm-build`/`axm-verify`,
mirroring tests/test_foundry_ontology_exit.py. The Spectra product test also
skips if duckdb is absent.

Evidence tier: reconciled against Palantir's PUBLISHED Datasets + Orchestration
API v2 wire shapes; the fixture is an invented sample in the documented shape,
NOT a live tenant capture. Structure only — no transform runtime.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from foundry_exit.pipeline_api import (
    PipelineCaptureError,
    load_pipeline_capture,
)
from foundry_exit.pipeline_seal import (
    VerifyStatus,
    build_candidates_and_source,
    kernel_available,
    seal_pipeline_capture,
)
from foundry_exit.seal import verify_exit_bundle

FIXTURE = Path(__file__).resolve().parent.parent / "samples" / "pipeline_exit_synthetic"

requires_kernel = pytest.mark.skipif(
    not kernel_available(), reason="axm-genesis kernel (axm-build / axm-verify) not on PATH"
)

try:
    import duckdb  # noqa: F401

    _HAVE_DUCKDB = True
except Exception:  # pragma: no cover
    _HAVE_DUCKDB = False

requires_duckdb = pytest.mark.skipif(not _HAVE_DUCKDB, reason="duckdb not installed")


def _write_capture(tmp_path: Path, datasets_doc, schemas=None, builds=None, jobs=None, schedules=None) -> Path:
    cap = tmp_path / "capture"
    cap.mkdir(parents=True, exist_ok=True)
    (cap / "datasets.json").write_text(json.dumps(datasets_doc), encoding="utf-8")
    if schemas:
        (cap / "schemas").mkdir(exist_ok=True)
        for name, doc in schemas.items():
            (cap / "schemas" / f"{name}.json").write_text(json.dumps(doc), encoding="utf-8")
    if builds is not None:
        (cap / "builds.json").write_text(json.dumps(builds), encoding="utf-8")
    if jobs:
        (cap / "jobs").mkdir(exist_ok=True)
        for name, doc in jobs.items():
            (cap / "jobs" / f"{name}.json").write_text(json.dumps(doc), encoding="utf-8")
    if schedules is not None:
        (cap / "schedules.json").write_text(json.dumps(schedules), encoding="utf-8")
    return cap


# ---------------------------------------------------------------------------
# shape
# ---------------------------------------------------------------------------


def test_loader_parses_fixture():
    cap = load_pipeline_capture(FIXTURE)
    assert [d.name for d in cap.datasets] == ["raw_flights", "airport_ref", "flights_clean", "flight_metrics"]
    assert cap.datasets[0].rid == "ri.foundry.main.dataset.5ynth-raw-flights"

    schema = cap.schemas_by_dataset["flights_clean"]
    types = {f.name: f.type for f in schema.fields}
    assert types["delayMinutes"] == "INTEGER"
    assert types["flightId"] == "STRING"

    # DAG edges reconstructed from build job I/O, rids resolved to names.
    edges = {(s, t) for s, t, _b in cap.edges()}
    assert ("raw_flights", "flights_clean") in edges
    assert ("airport_ref", "flights_clean") in edges
    assert ("flights_clean", "flight_metrics") in edges
    assert len(edges) == 3

    # provenance
    assert ("flights_clean", "build_flights_clean") in cap.outputs_by_build()
    assert cap.schedules_by_rid["ri.foundry.main.schedule.5ynth-nightly"].name == "nightly"


def test_loader_tolerates_unknown_fields(tmp_path):
    doc = {
        "somethingNewPalantirAdded": {"a": 1},
        "data": [
            {"rid": "ri.foundry.main.dataset.zzz", "name": "widgets",
             "futureField": "ignore me", "parentFolderRid": "ri.foundry.main.folder.q"}
        ],
    }
    cap = load_pipeline_capture(_write_capture(tmp_path, doc))
    assert cap.datasets[0].name == "widgets"
    assert cap.datasets[0].raw["futureField"] == "ignore me"


def test_loader_errors_name_file_and_missing_key(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(PipelineCaptureError, match="datasets.json"):
        load_pipeline_capture(empty)

    doc = {"data": [{"name": "no_rid"}]}
    with pytest.raises(PipelineCaptureError, match="rid"):
        load_pipeline_capture(_write_capture(tmp_path / "a", doc))

    doc2 = {"data": [{"rid": "ri.x", "name": "d"}]}
    bad_schema = {"d": {"branchName": "master"}}   # no fieldSchemaList
    with pytest.raises(PipelineCaptureError, match="fieldSchemaList"):
        load_pipeline_capture(_write_capture(tmp_path / "b", doc2, schemas=bad_schema))


def test_duplicate_dataset_name_is_a_clear_refusal(tmp_path):
    d = {"rid": "ri.foundry.main.dataset.a", "name": "dup"}
    doc = {"data": [d, {"rid": "ri.foundry.main.dataset.b", "name": "dup"}]}
    with pytest.raises(PipelineCaptureError, match="duplicate dataset name='dup'"):
        load_pipeline_capture(_write_capture(tmp_path, doc))


def test_unknown_input_rid_is_labeled_external_not_invented(tmp_path):
    """An input rid not in datasets.json is kept as an obviously-external label,
    never silently dropped and never invented into a real dataset name."""
    doc = {"data": [{"rid": "ri.foundry.main.dataset.known", "name": "out_ds"}]}
    builds = {"data": [{"rid": "ri.foundry.main.build.b", "name": "b"}]}
    jobs = {"b": {"data": [{"rid": "ri.j", "jobStatus": "SUCCEEDED",
                            "inputs": [{"datasetRid": "ri.foundry.main.dataset.mystery"}],
                            "outputs": [{"datasetRid": "ri.foundry.main.dataset.known"}]}]}}
    cap = load_pipeline_capture(_write_capture(tmp_path, doc, builds=builds, jobs=jobs))
    edges = {(s, t) for s, t, _b in cap.edges()}
    assert ("external:mystery", "out_ds") in edges


def test_candidates_bind_evidence_spans():
    cap = load_pipeline_capture(FIXTURE)
    candidates, source, counts = build_candidates_and_source(cap)
    preds = {c["predicate"] for c in candidates if c.get("type") == "claim"}
    assert {"has_field", "has_type", "feeds", "produced_by", "triggered_by"} <= preds
    src_bytes = source.encode("utf-8")
    for c in candidates:
        if c.get("type") == "claim":
            ev = c["evidence"]
            assert src_bytes[ev["byte_start"]:ev["byte_end"]].decode("utf-8") == ev["text"]


# ---------------------------------------------------------------------------
# kernel-gated
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sealed(tmp_path_factory):
    if not kernel_available():
        pytest.skip("kernel not available")
    work = tmp_path_factory.mktemp("pipe")
    cap = load_pipeline_capture(FIXTURE)
    shard = seal_pipeline_capture(cap, work / "shard")
    return cap, shard, work


def _claims_with_labels(shard_dir: Path):
    ents = {}
    for line in (shard_dir / "graph" / "entities.jsonl").read_text().splitlines():
        if line.strip():
            e = json.loads(line)
            ents[e["entity_id"]] = e["label"]
    out = []
    for line in (shard_dir / "graph" / "claims.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        c = json.loads(line)
        subj = ents.get(c["subject"], c["subject"])
        obj = ents.get(c["object"], c["object"]) if c["object_type"] == "entity" else c["object"]
        out.append((subj, c["predicate"], obj))
    return out


@requires_kernel
def test_seal_verifies_detached_with_out_of_band_key(sealed):
    _cap, shard, _w = sealed
    assert shard.suite == "axm-hybrid1"
    assert (Path(shard.shard_dir) / "manifest.json").exists()
    assert verify_exit_bundle(shard.shard_dir, shard.trusted_key_path) is VerifyStatus.PASS


@requires_kernel
def test_wrong_key_fails(sealed, tmp_path):
    _cap, shard, _w = sealed
    subprocess.run(["axm-build", "keygen", str(tmp_path), "--name", "attacker"],
                   check=True, capture_output=True, text=True)
    assert verify_exit_bundle(shard.shard_dir, tmp_path / "attacker.pub") is VerifyStatus.FAIL


@requires_kernel
def test_claim_graph_carries_the_pipeline_structure(sealed):
    _cap, shard, _w = sealed
    triples = set(_claims_with_labels(Path(shard.shard_dir)))
    assert ("dataset/flights_clean", "has_field", "field/flights_clean.delayMinutes") in triples
    assert ("field/flights_clean.delayMinutes", "has_type", "INTEGER") in triples
    assert ("dataset/raw_flights", "feeds", "dataset/flights_clean") in triples
    assert ("dataset/flights_clean", "feeds", "dataset/flight_metrics") in triples
    assert ("dataset/flights_clean", "produced_by", "build/build_flights_clean") in triples
    assert ("build/build_flights_clean", "triggered_by", "schedule/nightly") in triples


@requires_kernel
def test_verbatim_content_is_byte_identical(sealed):
    _cap, shard, _w = sealed
    content = Path(shard.shard_dir) / "content"
    assert (content / "datasets.json").read_bytes() == (FIXTURE / "datasets.json").read_bytes()
    # schemas/jobs are flattened top-level, but BYTES are identical to capture.
    assert (content / "schemas__flights_clean.json").read_bytes() == (FIXTURE / "schemas" / "flights_clean.json").read_bytes()
    assert (content / "jobs__build_flights_clean.json").read_bytes() == (FIXTURE / "jobs" / "build_flights_clean.json").read_bytes()


@requires_kernel
def test_external_id_never_becomes_custody_id(sealed):
    from axm_verify.crypto import derive_shard_id

    cap, shard, _w = sealed
    manifest_bytes = (Path(shard.shard_dir) / "manifest.json").read_bytes()
    assert shard.shard_id == derive_shard_id(manifest_bytes)
    assert shard.shard_id.startswith("sh1_")

    ext = set(cap.external_ids())
    assert any(x.startswith("ri.foundry.main.dataset.") for x in ext)
    assert shard.shard_id not in ext

    # No Palantir rid leaks into the identity-bearing manifest.
    manifest_text = manifest_bytes.decode("utf-8")
    assert "ri.foundry.main." not in manifest_text


# ---------------------------------------------------------------------------
# THE PRODUCT TEST: the sealed pipeline is queryable through Spectra
# ---------------------------------------------------------------------------


@requires_kernel
@requires_duckdb
def test_sealed_pipeline_is_queryable_through_spectra(sealed, tmp_path, monkeypatch):
    """The feature's reason to exist: mount the sealed shard into the repo's own
    axiom_runtime SpectraEngine (verify-gated) and prove the pipeline answers
    queries — the datasets, the dependency DAG, and a dataset's schema."""
    _cap, shard, _w = sealed

    monkeypatch.setenv("SPECTRA_DEV_MODE", "1")
    monkeypatch.setenv("SPECTRA_TRUSTED_PUBKEY", shard.trusted_key_path)
    from axiom_runtime.engine import SpectraEngine

    eng = SpectraEngine(
        db_path=str(tmp_path / "spectra.db"),
        audit_path=str(tmp_path / "audit.jsonl"),
        cache_path=str(tmp_path / "cache.jsonl"),
    )
    spec = eng.mount_shard(shard.shard_dir)
    assert spec.shard_id == shard.shard_id

    # The datasets are queryable.
    res = eng.query_json(
        """
        SELECT e.label
        FROM claims c JOIN entities e ON e.entity_id = c.subject
        WHERE e.entity_type = 'dataset' AND c.predicate = 'produced_by'
        ORDER BY e.label
        """
    )
    assert [r[0] for r in res["rows"]] == ["dataset/flight_metrics", "dataset/flights_clean"]

    # "What feeds flight_metrics?" — the DAG is queryable.
    feeds = eng.query_json(
        """
        SELECT s.label
        FROM claims c
        JOIN entities s ON s.entity_id = c.subject
        JOIN entities o ON o.entity_id = c.object
        WHERE c.predicate = 'feeds' AND o.label = 'dataset/flight_metrics'
        """
    )
    assert feeds["rows"] == [("dataset/flights_clean",)]

    # Transitive upstream of flight_metrics via two feeds hops.
    upstream = eng.query_json(
        """
        SELECT DISTINCT s2.label
        FROM claims c1
        JOIN entities o1 ON o1.entity_id = c1.object
        JOIN entities s1 ON s1.entity_id = c1.subject
        JOIN claims c2 ON c2.object = s1.entity_id AND c2.predicate = 'feeds'
        JOIN entities s2 ON s2.entity_id = c2.subject
        WHERE c1.predicate = 'feeds' AND o1.label = 'dataset/flight_metrics'
        ORDER BY s2.label
        """
    )
    assert sorted(r[0] for r in upstream["rows"]) == ["dataset/airport_ref", "dataset/raw_flights"]

    # A dataset's schema columns are queryable.
    cols = eng.query_json(
        """
        SELECT o.label
        FROM claims c
        JOIN entities s ON s.entity_id = c.subject
        JOIN entities o ON o.entity_id = c.object
        WHERE c.predicate = 'has_field' AND s.label = 'dataset/flights_clean'
        ORDER BY o.label
        """
    )
    assert ("field/flights_clean.delayMinutes",) in cols["rows"]
