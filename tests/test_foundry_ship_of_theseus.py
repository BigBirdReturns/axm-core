"""The Palantir Ship of Theseus — the exit ship manifest.

Kernel-free tests check the honest plank ledger. Kernel-gated tests run the demo
ship (both synthetic child exits), prove the ship manifest seals + verifies
detached, references the real child shard ids, and reports honest coverage
(4/9 planks sovereign). The Spectra product test mounts the ship shard and
queries the planks.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from foundry_exit import ship_of_theseus as S

requires_kernel = pytest.mark.skipif(
    not S.kernel_available(), reason="axm-genesis kernel (axm-build / axm-verify) not on PATH"
)

try:
    import duckdb  # noqa: F401

    _HAVE_DUCKDB = True
except Exception:  # pragma: no cover
    _HAVE_DUCKDB = False

requires_duckdb = pytest.mark.skipif(not _HAVE_DUCKDB, reason="duckdb not installed")


# ---------------------------------------------------------------------------
# the ledger is honest (kernel-free)
# ---------------------------------------------------------------------------


def test_ledger_is_honest():
    by_id = {p.id: p for p in S.SHIP_PLANKS}
    # Exactly the four load-bearing planks are PROVEN.
    proven = {p.id for p in S.SHIP_PLANKS if p.capability == S.PROVEN}
    assert proven == {"ontology-structure", "ontology-data", "pipeline-schemas", "pipeline-dag"}
    # The runtime cannot be exported; permissions is a deliberate anti-goal.
    assert by_id["pipeline-runtime"].capability == S.CUSTOMER_REBUILD
    assert by_id["permissions"].capability == S.ANTI_GOAL
    assert by_id["apps"].capability == S.NO_EXPORT
    assert by_id["actions"].capability == S.MAPPED
    # Plank ids are unique.
    ids = [p.id for p in S.SHIP_PLANKS]
    assert len(ids) == len(set(ids))
    # Only PROVEN planks name a working exit.
    for p in S.SHIP_PLANKS:
        if p.exit is not None:
            assert p.capability == S.PROVEN


# ---------------------------------------------------------------------------
# kernel-gated: the ship seals, verifies, and is honest about coverage
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def demo_ship(tmp_path_factory):
    if not S.kernel_available():
        pytest.skip("kernel not available")
    out = tmp_path_factory.mktemp("ship")
    packet = S.run(None, out)   # demo mode: both synthetic samples
    return packet


@requires_kernel
def test_demo_ship_seals_and_verifies(demo_ship):
    assert demo_ship["verification"]["status"] == "PASS"
    assert demo_ship["shard_id"].startswith("sh1_")
    cov = demo_ship["coverage"]
    assert (cov["sovereign_planks"], cov["total_planks"]) == (4, 9)
    swapped = {s["exit"]: s for s in cov["planks_swapped_this_run"]}
    assert set(swapped) == {"ontology", "pipeline"}
    assert all(s["verify"] == "PASS" for s in swapped.values())


@requires_kernel
def test_ship_manifest_carries_the_plank_ledger(demo_ship, tmp_path):
    # Re-run to get a shard dir we can read (run() seals into a temp dir; re-seal
    # here into a known place via the same path the packet reports is not exposed,
    # so we rebuild once into tmp_path).
    work = tmp_path / "w"
    work.mkdir()
    children = S._collect_children(None, work)
    ship = S._seal_ship(children, work / "shard")

    ents = {}
    for line in (Path(ship.shard_dir) / "graph" / "entities.jsonl").read_text().splitlines():
        if line.strip():
            e = json.loads(line)
            ents[e["entity_id"]] = e["label"]
    triples = []
    for line in (Path(ship.shard_dir) / "graph" / "claims.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        c = json.loads(line)
        subj = ents.get(c["subject"], c["subject"])
        obj = ents.get(c["object"], c["object"]) if c["object_type"] == "entity" else c["object"]
        triples.append((subj, c["predicate"], obj))
    tset = set(triples)

    # every plank is present with a status
    assert ("ship/foundry-exit", "has_plank", "plank/ontology-structure") in tset
    assert ("plank/permissions", "status", S.ANTI_GOAL) in tset
    assert ("plank/pipeline-dag", "status", S.PROVEN) in tset
    # swapped planks bind their child shard
    ont_shard = children["ontology"].shard_id
    assert ("plank/ontology-structure", "sealed_as", f"shard/{ont_shard}") in tset
    # the honest count
    assert ("ship/foundry-exit", "sovereign_planks", "4") in tset


@requires_kernel
def test_real_mode_runs_only_present_captures(tmp_path):
    """A real capture_root with only pipeline/ present runs ONLY the pipeline
    exit — never a silent synthetic fallback."""
    root = tmp_path / "root"
    root.mkdir()
    shutil.copytree(S.PIPELINE_SAMPLE, root / "pipeline")
    packet = S.run(root, tmp_path / "out")
    swapped = {s["exit"] for s in packet["coverage"]["planks_swapped_this_run"]}
    assert swapped == {"pipeline"}
    assert "ontology" not in swapped
    assert packet["verification"]["status"] == "PASS"
    # coverage (the intrinsic ledger) is unchanged; only what SWAPPED differs.
    assert packet["coverage"]["sovereign_planks"] == 4


@requires_kernel
@requires_duckdb
def test_ship_is_queryable_through_spectra(tmp_path, monkeypatch):
    work = tmp_path / "w"
    work.mkdir()
    children = S._collect_children(None, work)
    ship = S._seal_ship(children, work / "shard")

    monkeypatch.setenv("SPECTRA_DEV_MODE", "1")
    monkeypatch.setenv("SPECTRA_TRUSTED_PUBKEY", ship.trusted_key_path)
    from axiom_runtime.engine import SpectraEngine

    eng = SpectraEngine(
        db_path=str(tmp_path / "spectra.db"),
        audit_path=str(tmp_path / "audit.jsonl"),
        cache_path=str(tmp_path / "cache.jsonl"),
    )
    eng.mount_shard(ship.shard_dir)

    # "Which planks are sovereign?" — the ship answers.
    res = eng.query_json(
        """
        SELECT e.label
        FROM claims c JOIN entities e ON e.entity_id = c.subject
        WHERE c.predicate = 'status' AND c.object = 'PROVEN'
        ORDER BY e.label
        """
    )
    labels = [r[0] for r in res["rows"]]
    assert labels == [
        "plank/ontology-data", "plank/ontology-structure",
        "plank/pipeline-dag", "plank/pipeline-schemas",
    ]
