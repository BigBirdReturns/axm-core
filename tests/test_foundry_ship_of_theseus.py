"""The Palantir Ship of Theseus — 9/9 planks sealed, each at its honest tier.

Kernel-free tests check the tier ledger. Kernel-gated tests run the demo ship
(all six child exits), prove the manifest seals + verifies detached, records the
9 planks with correct tiers, and reports honest coverage (9/9 sealed, 4 FULL).
The Spectra product test mounts the ship shard and queries the planks by tier.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from foundry_exit import ship_of_theseus as S

requires_kernel = pytest.mark.skipif(
    not S.SC.kernel_available(), reason="axm-genesis kernel not on PATH"
)
try:
    import duckdb  # noqa: F401
    _HAVE_DUCKDB = True
except Exception:  # pragma: no cover
    _HAVE_DUCKDB = False
requires_duckdb = pytest.mark.skipif(not _HAVE_DUCKDB, reason="duckdb not installed")


def test_tier_ledger_is_honest():
    by_id = {p.id: p for p in S.SHIP_PLANKS}
    tiers = {}
    for p in S.SHIP_PLANKS:
        tiers[p.tier] = tiers.get(p.tier, 0) + 1
    assert tiers == {S.FULL: 4, S.CONTRACT: 2, S.SOURCE: 1, S.ATTESTED: 2}
    # the load-bearing four are FULL; permissions is ATTESTED (never "carried")
    assert by_id["pipeline-dag"].tier == S.FULL
    assert by_id["permissions"].tier == S.ATTESTED
    assert by_id["pipeline-runtime"].tier == S.SOURCE
    assert by_id["actions"].tier == S.CONTRACT
    # every plank names an exit that exists
    for p in S.SHIP_PLANKS:
        assert p.exit in S.EXITS
    ids = [p.id for p in S.SHIP_PLANKS]
    assert len(ids) == len(set(ids)) == 9


@pytest.fixture(scope="module")
def demo_ship(tmp_path_factory):
    if not S.SC.kernel_available():
        pytest.skip("kernel not available")
    return S.run(None, tmp_path_factory.mktemp("ship"))


@requires_kernel
def test_demo_ship_seals_9_of_9_and_verifies(demo_ship):
    assert demo_ship["verification"]["status"] == "PASS"
    cov = demo_ship["coverage"]
    assert (cov["planks_sealed"], cov["total_planks"]) == (9, 9)
    assert cov["full_surface_planks"] == 4
    assert cov["tier_breakdown"] == {S.FULL: 4, S.CONTRACT: 2, S.SOURCE: 1, S.ATTESTED: 2}
    # six distinct child exits, all PASS
    exits = {c["exit"]: c for c in cov["child_exits"]}
    assert set(exits) == {"ontology", "pipeline", "logic", "residual:source", "residual:apps", "residual:policy"}
    assert all(c["verify"] == "PASS" for c in exits.values())


@requires_kernel
def test_manifest_records_tiers_and_bindings(tmp_path):
    work = tmp_path / "w"; work.mkdir()
    children = S._collect(None, work)
    ship, tiers = S._seal(children, work / "shard")

    ents = {}
    for line in (Path(ship.shard_dir) / "graph" / "entities.jsonl").read_text().splitlines():
        if line.strip():
            e = json.loads(line); ents[e["entity_id"]] = e["label"]
    triples = set()
    for line in (Path(ship.shard_dir) / "graph" / "claims.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        c = json.loads(line)
        subj = ents.get(c["subject"], c["subject"])
        obj = ents.get(c["object"], c["object"]) if c["object_type"] == "entity" else c["object"]
        triples.add((subj, c["predicate"], obj))

    assert ("ship/foundry-exit", "has_plank", "plank/permissions") in triples
    assert ("plank/permissions", "tier", S.ATTESTED) in triples
    assert ("plank/ontology-structure", "tier", S.FULL) in triples
    assert ("plank/actions", "tier", S.CONTRACT) in triples
    assert ("ship/foundry-exit", "planks_sealed", "9") in triples
    # actions + functions both bind to the ONE logic child shard
    logic_shard = children["logic"].shard_id
    assert ("plank/actions", "sealed_as", f"shard/{logic_shard}") in triples
    assert ("plank/functions", "sealed_as", f"shard/{logic_shard}") in triples


@requires_kernel
def test_real_mode_runs_only_present_captures(tmp_path):
    root = tmp_path / "root"; root.mkdir()
    shutil.copytree(S.SAMPLES / "pipeline_exit_synthetic", root / "pipeline")
    packet = S.run(root, tmp_path / "out")
    exits = {c["exit"] for c in packet["coverage"]["child_exits"]}
    assert exits == {"pipeline"}
    # only the 2 pipeline planks got sealed; the tier ledger is intrinsic (still 4 FULL)
    assert packet["coverage"]["planks_sealed"] == 2
    assert packet["coverage"]["full_surface_planks"] == 4
    assert packet["verification"]["status"] == "PASS"


@requires_kernel
@requires_duckdb
def test_ship_queryable_by_tier_through_spectra(tmp_path, monkeypatch):
    work = tmp_path / "w"; work.mkdir()
    children = S._collect(None, work)
    ship, _ = S._seal(children, work / "shard")

    monkeypatch.setenv("SPECTRA_DEV_MODE", "1")
    monkeypatch.setenv("SPECTRA_TRUSTED_PUBKEY", ship.trusted_key_path)
    from axiom_runtime.engine import SpectraEngine
    eng = SpectraEngine(db_path=str(tmp_path / "s.db"), audit_path=str(tmp_path / "a.jsonl"),
                        cache_path=str(tmp_path / "c.jsonl"))
    eng.mount_shard(ship.shard_dir)
    res = eng.query_json(
        """
        SELECT e.label FROM claims c JOIN entities e ON e.entity_id = c.subject
        WHERE c.predicate = 'tier' AND c.object = 'FULL' ORDER BY e.label
        """
    )
    assert [r[0] for r in res["rows"]] == [
        "plank/ontology-data", "plank/ontology-structure", "plank/pipeline-dag", "plank/pipeline-schemas",
    ]
