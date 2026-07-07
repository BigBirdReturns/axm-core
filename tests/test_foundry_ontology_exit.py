"""Ontology Exit v0 — loader/translator shape, sealed shard, and the PRODUCT
property: the sealed ontology is queryable through the repo's own Spectra engine.

Kernel-free tests (parse/translate/merge/error) always run. Kernel-gated tests
(seal/verify through real genesis) skip cleanly without `axm-build`/`axm-verify`,
mirroring tests/test_foundry_exit_v0.py. The Spectra product test additionally
skips if duckdb is absent.

Evidence tier: reconciled against Palantir's PUBLISHED Ontology API v2 wire
shapes; the fixture is an invented sample in the documented shape, NOT a live
tenant capture.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from foundry_exit.ontology_api import (
    OntologyCaptureError,
    load_ontology_capture,
    to_exit_ontology,
)
from foundry_exit.ontology_seal import (
    VerifyStatus,
    build_candidates_and_source,
    kernel_available,
    seal_ontology_capture,
)
from foundry_exit.seal import verify_exit_bundle

FIXTURE = Path(__file__).resolve().parent.parent / "samples" / "foundry_ontology_fixture"

requires_kernel = pytest.mark.skipif(
    not kernel_available(), reason="axm-genesis kernel (axm-build / axm-verify) not on PATH"
)

try:  # the product test needs duckdb (the Spectra engine dependency)
    import duckdb  # noqa: F401

    _HAVE_DUCKDB = True
except Exception:  # pragma: no cover
    _HAVE_DUCKDB = False

requires_duckdb = pytest.mark.skipif(not _HAVE_DUCKDB, reason="duckdb not installed")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _write_capture(tmp_path: Path, object_types_doc, links=None, objects=None) -> Path:
    """Materialize a capture dir from Python objects (for negative/edge tests)."""
    cap = tmp_path / "capture"
    cap.mkdir(parents=True, exist_ok=True)
    (cap / "objectTypes.json").write_text(json.dumps(object_types_doc), encoding="utf-8")
    if links:
        (cap / "linkTypes").mkdir(exist_ok=True)
        for name, doc in links.items():
            (cap / "linkTypes" / f"{name}.json").write_text(json.dumps(doc), encoding="utf-8")
    if objects:
        (cap / "objects").mkdir(exist_ok=True)
        for name, doc in objects.items():
            (cap / "objects" / f"{name}.json").write_text(json.dumps(doc), encoding="utf-8")
    return cap


# ---------------------------------------------------------------------------
# shape: loader parses the fixture
# ---------------------------------------------------------------------------


def test_loader_parses_fixture():
    """Control question: does the loader read the documented wire shapes into
    the model (types, properties, links, instance pages)?"""
    cap = load_ontology_capture(FIXTURE)
    names = [ot.api_name for ot in cap.object_types]
    assert names == ["Aircraft", "Flight", "Airport"]

    aircraft = cap.object_types[0]
    assert aircraft.primary_key == "tailNumber"
    assert aircraft.display_name == "Aircraft"
    assert aircraft.rid.startswith("ri.ontology.main.object-type.")
    prop_types = {p.api_name: p.data_type for p in aircraft.properties}
    assert prop_types["tailNumber"] == "string"
    assert prop_types["seatCount"] == "integer"
    assert prop_types["rangeKm"] == "double"
    assert prop_types["inService"] == "boolean"
    assert prop_types["firstFlightDate"] == "date"

    # Outgoing links keyed by SOURCE object type; field names the TARGET.
    flight_links = cap.links_by_source["Flight"]
    assert flight_links[0].api_name == "operatedByAircraft"
    assert flight_links[0].object_type_api_name == "Aircraft"   # target
    assert flight_links[0].cardinality == "ONE"
    assert flight_links[0].foreign_key_property_api_name == "aircraftTail"
    assert cap.links_by_source["Airport"][0].cardinality == "MANY"

    page = cap.instances_by_type["Flight"]
    assert len(page.rows) == 8
    assert page.total_count == "42"          # declared larger than rows present
    assert page.rows[0]["__apiName"] == "Flight"


def test_loader_tolerates_unknown_fields(tmp_path):
    """Control question: an unknown extra field (Palantir may add one) must be
    ignored, never crash — and the verbatim dict keeps it."""
    doc = {
        "nextPageToken": None,
        "somethingNewPalantirAdded": {"a": 1},   # unknown top-level page field
        "data": [
            {
                "apiName": "Widget",
                "displayName": "Widget",
                "primaryKey": "id",
                "rid": "ri.ontology.main.object-type.abc",
                "futureField": "ignore me",       # unknown object-type field
                "properties": {
                    "id": {
                        "dataType": {"type": "string", "extraTypeField": True},
                        "rid": "ri.ontology.main.property.zzz",
                        "unknownProp": 42,        # unknown property field
                    }
                },
            }
        ],
    }
    cap = load_ontology_capture(_write_capture(tmp_path, doc))
    ot = cap.object_types[0]
    assert ot.api_name == "Widget" and ot.properties[0].data_type == "string"
    assert ot.raw["futureField"] == "ignore me"          # preserved verbatim
    assert ot.properties[0].raw["unknownProp"] == 42


def test_loader_errors_name_file_and_missing_key(tmp_path):
    """Control question: a missing REQUIRED field fails with a clear error that
    names the file and the key — not a bare KeyError/crash."""
    # missing objectTypes.json entirely
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(OntologyCaptureError, match="objectTypes.json"):
        load_ontology_capture(empty)

    # missing apiName on an object type
    doc = {"data": [{"displayName": "X", "properties": {}}]}
    with pytest.raises(OntologyCaptureError, match="apiName"):
        load_ontology_capture(_write_capture(tmp_path / "a", doc))

    # missing dataType.type on a property
    doc2 = {"data": [{"apiName": "X", "properties": {"p": {"dataType": {}}}}]}
    with pytest.raises(OntologyCaptureError, match="type"):
        load_ontology_capture(_write_capture(tmp_path / "b", doc2))

    # missing 'data' array
    with pytest.raises(OntologyCaptureError, match="data"):
        load_ontology_capture(_write_capture(tmp_path / "c", {"nextPageToken": "x"}))


def test_duplicate_api_name_is_a_clear_refusal(tmp_path):
    """Control question: the same object type captured twice (page saved twice,
    overlapping pages merged) must refuse with a clear OntologyCaptureError
    naming the apiName — never surface as the genesis compiler's opaque
    duplicate-claim-id subprocess abort."""
    ot = {"apiName": "Widget", "primaryKey": "id", "rid": "ri.ontology.main.object-type.abc",
          "properties": {"id": {"dataType": {"type": "string"}, "rid": "ri.x"}}}
    doc = {"data": [ot, dict(ot)]}
    with pytest.raises(OntologyCaptureError, match="duplicate object type apiName='Widget'"):
        load_ontology_capture(_write_capture(tmp_path, doc))


def test_properties_key_may_be_absent(tmp_path):
    """Control question: a type with no properties may omit the key entirely
    (tolerant parsing) — treated as an empty map, not an error."""
    doc = {"data": [{"apiName": "Bare", "rid": "ri.ontology.main.object-type.bare"}]}
    cap = load_ontology_capture(_write_capture(tmp_path, doc))
    assert cap.object_types[0].api_name == "Bare"
    assert cap.object_types[0].properties == ()


def test_translation_correctness():
    """Control question: does translation produce the importer's ontology.json
    superset (ids, typed properties, links with target/cardinality/foreign_key,
    empty backing/action/security in v0, rid under external_ids)?"""
    cap = load_ontology_capture(FIXTURE)
    onto = to_exit_ontology(cap)
    by_id = {o["object_type_id"]: o for o in onto["object_types"]}

    flight = by_id["Flight"]
    assert {p["id"]: p["type"] for p in flight["properties"]}["passengers"] == "integer"
    link = flight["links"][0]
    assert link == {
        "id": "operatedByAircraft",
        "target_object_type_id": "Aircraft",
        "cardinality": "ONE",
        "foreign_key": "aircraftTail",
    }
    # v0 honestly empty (not carried by the v2 list endpoints):
    assert flight["backing_dataset_rids"] == []
    assert flight["action_refs"] == []
    assert flight["security_markings"] == []
    # rid preserved verbatim under external_ids, never as the object_type_id:
    assert flight["external_ids"]["rid"].startswith("ri.ontology.main.object-type.")
    assert flight["object_type_id"] == "Flight"

    # The translated shape is consumable by the existing importer's _object_type.
    from foundry_exit.importer import FoundryExitImporter
    ot = FoundryExitImporter._object_type(flight)
    assert ot.object_type_id == "Flight"
    assert ot.links[0]["target_object_type_id"] == "Aircraft"


def test_multipage_merge(tmp_path):
    """Control question: a JSON ARRAY of response pages is treated as
    concatenated pages (data arrays merged); the last totalCount wins."""
    # objectTypes as two pages
    pages = [
        {"nextPageToken": "p2", "data": [{"apiName": "A", "properties": {}}]},
        {"nextPageToken": None, "data": [{"apiName": "B", "properties": {}}]},
    ]
    # objects as two pages, second page declares the final totalCount
    obj_pages = [
        {"nextPageToken": "n", "totalCount": "5",
         "data": [{"__rid": "r1", "__primaryKey": "1", "__apiName": "A"}]},
        {"nextPageToken": None, "totalCount": "5",
         "data": [{"__rid": "r2", "__primaryKey": "2", "__apiName": "A"}]},
    ]
    cap = load_ontology_capture(_write_capture(tmp_path, pages, objects={"A": obj_pages}))
    assert [ot.api_name for ot in cap.object_types] == ["A", "B"]
    page = cap.instances_by_type["A"]
    assert len(page.rows) == 2 and page.total_count == "5"


def test_candidates_declare_partial_capture():
    """Control question (kernel-free): when totalCount != rows present, the
    candidate set declares BOTH declared and captured counts, hiding nothing."""
    cap = load_ontology_capture(FIXTURE)
    candidates, source, counts = build_candidates_and_source(cap)
    preds = {c["predicate"] for c in candidates if c.get("type") == "claim"}
    assert "instance_count_declared" in preds and "instances_captured" in preds
    assert "instance_count" not in preds     # fixture is a partial capture
    # every claim's evidence text is present at its byte span in source.txt
    src_bytes = source.encode("utf-8")
    for c in candidates:
        if c.get("type") == "claim":
            ev = c["evidence"]
            assert src_bytes[ev["byte_start"]:ev["byte_end"]].decode("utf-8") == ev["text"]


# ---------------------------------------------------------------------------
# kernel-gated: seal / verify / claim graph / byte-identity / invariant
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sealed(tmp_path_factory):
    if not kernel_available():
        pytest.skip("kernel not available")
    work = tmp_path_factory.mktemp("ont")
    cap = load_ontology_capture(FIXTURE)
    shard = seal_ontology_capture(cap, work / "shard")
    return cap, shard, work


def _claims_with_labels(shard_dir: Path):
    """Load claims.jsonl with subject/object entity ids resolved to labels."""
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
        out.append((subj, c["predicate"], obj, c["object_type"], c["tier"]))
    return out


@requires_kernel
def test_seal_verifies_detached_with_out_of_band_key(sealed):
    """Control question: the sealed shard verifies through real genesis with the
    out-of-band public key (PASS)."""
    _cap, shard, _w = sealed
    assert shard.suite == "axm-hybrid1"
    assert (Path(shard.shard_dir) / "manifest.json").exists()
    assert verify_exit_bundle(shard.shard_dir, shard.trusted_key_path) is VerifyStatus.PASS


@requires_kernel
def test_wrong_key_fails(sealed, tmp_path):
    """Control question: a different (attacker) key must not verify the shard."""
    _cap, shard, _w = sealed
    subprocess.run(["axm-build", "keygen", str(tmp_path), "--name", "attacker"],
                   check=True, capture_output=True, text=True)
    assert verify_exit_bundle(shard.shard_dir, tmp_path / "attacker.pub") is VerifyStatus.FAIL


@requires_kernel
def test_claim_graph_carries_the_ontology_structure(sealed):
    """Control question: the sealed claim graph contains the ontology's
    structure — has_property, links_to, cardinality, and the declared/captured
    instance counts."""
    _cap, shard, _w = sealed
    claims = _claims_with_labels(Path(shard.shard_dir))
    triples = {(s, p, o) for s, p, o, _t, _tier in claims}

    assert ("objectType/Aircraft", "has_property", "property/Aircraft.tailNumber") in triples
    assert ("objectType/Aircraft", "primary_key", "tailNumber") in triples
    assert ("property/Aircraft.seatCount", "has_type", "integer") in triples
    assert ("objectType/Flight", "links_to", "objectType/Aircraft") in triples
    assert ("objectType/Airport", "links_to", "objectType/Flight") in triples
    assert ("link/operatedByAircraft", "cardinality", "ONE") in triples
    assert ("link/departingFlights", "cardinality", "MANY") in triples
    assert ("link/operatedByAircraft", "foreign_key", "aircraftTail") in triples
    # partial capture declared, not hidden:
    assert ("objectType/Flight", "instance_count_declared", "42") in triples
    assert ("objectType/Flight", "instances_captured", "8") in triples
    # instance counts are tier 0 (weakest evidence tier)
    for s, p, o, _ot, tier in claims:
        if p in ("instance_count_declared", "instances_captured", "instance_count"):
            assert tier == 0


@requires_kernel
def test_verbatim_content_is_byte_identical(sealed):
    """Control question: the sealed API responses are preserved byte-for-byte
    (identity, not a re-serialization)."""
    _cap, shard, _w = sealed
    content = Path(shard.shard_dir) / "content"
    assert (content / "objectTypes.json").read_bytes() == (FIXTURE / "objectTypes.json").read_bytes()
    # linkTypes/objects are flattened top-level (compiler seals only top level),
    # but the BYTES are identical to the capture files.
    assert (content / "linkTypes__Flight.json").read_bytes() == (FIXTURE / "linkTypes" / "Flight.json").read_bytes()
    assert (content / "objects__Flight.json").read_bytes() == (FIXTURE / "objects" / "Flight.json").read_bytes()


@requires_kernel
def test_external_id_never_becomes_custody_id(sealed):
    """Control question (mirrors the dataset-exit invariant): Palantir rid /
    apiName appear only as labels/literals/content — never as shard identity."""
    from axm_verify.crypto import derive_shard_id

    cap, shard, _w = sealed
    manifest_bytes = (Path(shard.shard_dir) / "manifest.json").read_bytes()
    assert shard.shard_id == derive_shard_id(manifest_bytes)   # custody is genesis's
    assert shard.shard_id.startswith("sh1_")

    ext = set(cap.external_ids())
    assert "Aircraft" in ext and any(x.startswith("ri.ontology.main.object-type.") for x in ext)
    assert shard.shard_id not in ext                            # never a Palantir id

    # No Palantir rid string leaks into the identity-bearing manifest.
    manifest_text = manifest_bytes.decode("utf-8")
    assert "ri.ontology.main." not in manifest_text
    for x in ext:
        if x.startswith("ri.ontology.main."):
            assert x not in manifest_text

    # Declared exception (see ontology_seal docstring): apiNames DO appear in
    # the flattened content filenames listed under manifest.sources, and are
    # therefore hashed into — but are never themselves — the custody id.
    assert "linkTypes__Flight.json" in manifest_text
    assert shard.shard_id == derive_shard_id(manifest_bytes)


# ---------------------------------------------------------------------------
# THE PRODUCT TEST: the sealed ontology is queryable through Spectra
# ---------------------------------------------------------------------------


@requires_kernel
@requires_duckdb
def test_sealed_ontology_is_queryable_through_spectra(sealed, tmp_path, monkeypatch):
    """The feature's reason to exist: mount the sealed shard into the repo's own
    axiom_runtime SpectraEngine (verify-gated) and prove the ontology answers
    queries — object types via SQL over claims, and the Flight->Aircraft
    links_to edge."""
    _cap, shard, _w = sealed

    monkeypatch.setenv("SPECTRA_DEV_MODE", "1")            # dev vault key; verify still runs
    monkeypatch.setenv("SPECTRA_TRUSTED_PUBKEY", shard.trusted_key_path)
    from axiom_runtime.engine import SpectraEngine

    eng = SpectraEngine(
        db_path=str(tmp_path / "spectra.db"),
        audit_path=str(tmp_path / "audit.jsonl"),
        cache_path=str(tmp_path / "cache.jsonl"),
    )
    spec = eng.mount_shard(shard.shard_dir)
    assert spec.shard_id == shard.shard_id
    assert sorted({t.split("__")[0] for t in spec.tables}) == ["claims", "entities", "provenance", "spans"]

    # SQL over claims returns the object types.
    res = eng.query_json(
        """
        SELECT DISTINCT e.label
        FROM claims c JOIN entities e ON e.entity_id = c.subject
        WHERE e.entity_type = 'object_type'
        ORDER BY e.label
        """
    )
    assert [r[0] for r in res["rows"]] == ["objectType/Aircraft", "objectType/Airport", "objectType/Flight"]

    # A links_to query returns the Flight->Aircraft edge.
    edge = eng.query_json(
        """
        SELECT s.label, o.label
        FROM claims c
        JOIN entities s ON s.entity_id = c.subject
        JOIN entities o ON o.entity_id = c.object
        WHERE c.predicate = 'links_to' AND s.label = 'objectType/Flight'
        """
    )
    assert edge["rows"] == [("objectType/Flight", "objectType/Aircraft")]

    # The declared-vs-captured instance counts are queryable too.
    counts = eng.query_json(
        """
        SELECT c.predicate, c.object
        FROM claims c JOIN entities e ON e.entity_id = c.subject
        WHERE e.label = 'objectType/Flight' AND c.predicate LIKE 'instance%'
        ORDER BY c.predicate
        """
    )
    assert counts["rows"] == [("instance_count_declared", "42"), ("instances_captured", "8")]
