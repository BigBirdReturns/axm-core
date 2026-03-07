#!/usr/bin/env python3
"""
query_cross_shard.py — Phase 2: Multi-shard composition queries via Spectra.

Mounts two shards into Spectra's DuckDB instance and executes JOIN queries
across them using ext/references@1.parquet as the bridge.

    Shard A (embodied): robot run — wheel_slip, emergency_stop claims
    Shard B (gold):     FM 21-11 hemorrhage doctrine — the normative authority

The query answers: "For every Tier-0 action the robot took, what doctrine
claim authorized it — and what was the full text of that doctrine?"

Usage
-----
    python query_cross_shard.py <embodied_shard_dir> <gold_shard_dir>

    # Example:
    python query_cross_shard.py \\
        axm-embodied/shard_out/ \\
        axm-genesis/shards/gold/fm21-11-hemorrhage-v1/

Architecture
------------
Spectra already auto-registers any *.parquet in ext/ as a DuckDB view when
a shard is mounted. After mounting both shards, the following views exist:

    claims__{pfx_a}__{shard_a}          — embodied claims
    ext_references__{pfx_a}__{shard_a}  — cross-shard reference edges
    claims__{pfx_b}__{shard_b}          — gold doctrine claims
    spans__{pfx_b}__{shard_b}           — gold doctrine evidence text

The JOIN is standard SQL — no special Spectra API needed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Spectra engine (axm-core)
# ---------------------------------------------------------------------------
try:
    from axiom_runtime.engine import SpectraEngine
except ImportError:
    print("ERROR: axiom_runtime not found. Install axm-core or run from axm-core/spectra/")
    sys.exit(1)


def _find_view(engine: SpectraEngine, fragment: str) -> str | None:
    """Find a DuckDB view whose name contains all parts of fragment (__ separated)."""
    parts = fragment.split("__")
    rows = engine.con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_type = 'VIEW'"
    ).fetchall()
    for (name,) in rows:
        if all(p in name for p in parts):
            return name
    return None


def _all_views(engine: SpectraEngine) -> list[str]:
    rows = engine.con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_type = 'VIEW'"
    ).fetchall()
    return [r[0] for r in rows]


def run_queries(embodied_shard: Path, gold_shard: Path) -> None:
    print(f"\n{'='*72}")
    print("  AXM Phase 2 — Cross-Shard Composition Query")
    print(f"{'='*72}")
    print(f"  Embodied shard : {embodied_shard}")
    print(f"  Gold shard     : {gold_shard}")
    print()

    engine = SpectraEngine()

    # Mount both shards. Constitution check runs on each.
    print("Mounting shards...")
    spec_a = engine.mount(str(embodied_shard), None)
    spec_b = engine.mount(str(gold_shard), None)

    shard_a_id = spec_a["shard_id"]
    shard_b_id = spec_b["shard_id"]

    print(f"  [A] embodied  : {shard_a_id[:48]}...")
    print(f"  [B] gold      : {shard_b_id[:48]}...")
    print()

    # Discover view names (they contain mount_prefix + shard_id fragments)
    views = _all_views(engine)
    print(f"  Registered views ({len(views)}):")
    for v in sorted(views):
        print(f"    {v}")
    print()

    # Find the key views we need
    refs_view     = next((v for v in views if v.startswith("ext_references__")), None)
    claims_a_view = next((v for v in views if v.startswith("claims__") and shard_a_id[:8] in v), None)
    claims_b_view = next((v for v in views if v.startswith("claims__") and shard_b_id[:8] in v), None)
    spans_b_view  = next((v for v in views if v.startswith("spans__")  and shard_b_id[:8] in v), None)

    if not refs_view:
        print("WARN: No ext_references__ view found. Was the embodied shard compiled")
        print("      with references support? Re-compile with the patched compile.py.")
        print()
        # Still run basic queries on what we have
    else:
        print(f"  References view : {refs_view}")

    # ── Query 1: All references emitted by the embodied shard ────────────────
    print(f"\n{'─'*72}")
    print("  QUERY 1 — All cross-shard references in the embodied shard")
    print(f"{'─'*72}")
    if refs_view:
        sql = f"""
            SELECT
                src_claim_id,
                relation_type,
                dst_shard_id,
                dst_object_type,
                confidence,
                note
            FROM "{refs_view}"
            ORDER BY confidence DESC, src_claim_id
        """
        result = engine.query_json(sql)
        print(f"  Columns: {result['columns']}")
        for row in result["rows"]:
            print(f"  {row}")
    else:
        print("  (skipped — no references view)")

    # ── Query 2: The JOIN — embodied claims → gold doctrine ─────────────────
    print(f"\n{'─'*72}")
    print("  QUERY 2 — Embodied Tier-0/1 claims JOIN gold doctrine claims")
    print(f"{'─'*72}")
    if refs_view and claims_a_view and claims_b_view:
        sql = f"""
            SELECT
                a.claim_id                          AS embodied_claim_id,
                a.predicate                         AS action,
                a.object                            AS target,
                a.tier                              AS tier,
                r.relation_type                     AS relation,
                r.confidence                        AS ref_confidence,
                b.claim_id                          AS doctrine_claim_id,
                b.predicate                         AS doctrine_predicate,
                b.object                            AS doctrine_object
            FROM "{claims_a_view}"   AS a
            JOIN "{refs_view}"       AS r  ON r.src_claim_id  = a.claim_id
            JOIN "{claims_b_view}"   AS b  ON b.claim_id      = r.dst_object_id
            WHERE a.tier <= 1
            ORDER BY a.tier, a.predicate
        """
        result = engine.query_json(sql)
        if result["rows"]:
            cols = result["columns"]
            print(f"  {cols}")
            for row in result["rows"]:
                print(f"  {row}")
        else:
            print("  (no rows — references use dst_object_type='shard', see Query 3)")
    else:
        print("  (skipped — missing views)")

    # ── Query 3: Shard-level references with doctrine evidence text ──────────
    print(f"\n{'─'*72}")
    print("  QUERY 3 — Tier-0/1 actions citing the gold shard + doctrine spans")
    print(f"{'─'*72}")
    if refs_view and claims_a_view and spans_b_view:
        sql = f"""
            SELECT
                a.predicate                         AS action,
                a.tier                              AS tier,
                r.relation_type                     AS relation,
                r.confidence                        AS confidence,
                r.note                              AS authority_note,
                LEFT(s.text, 120)                   AS doctrine_evidence
            FROM "{claims_a_view}"   AS a
            JOIN "{refs_view}"       AS r  ON r.src_claim_id  = a.claim_id
            JOIN "{spans_b_view}"    AS s  ON s.source_hash   = (
                SELECT source_hash FROM "{spans_b_view}" LIMIT 1
            )
            WHERE a.tier <= 1
              AND r.dst_shard_id = '{shard_b_id}'
            ORDER BY a.tier, r.confidence DESC
            LIMIT 10
        """
        result = engine.query_json(sql)
        if result["rows"]:
            cols = result["columns"]
            for col, val in zip(cols, result["rows"][0]):
                print(f"  {col:20s}: {val}")
        else:
            print("  (no rows)")
    else:
        print("  (skipped — missing views)")

    # ── Query 4: Broken references (gold shard not mounted) ─────────────────
    print(f"\n{'─'*72}")
    print("  QUERY 4 — Reference integrity check (dst_shard_id mounted?)")
    print(f"{'─'*72}")
    if refs_view:
        mounted_ids = {spec_a["shard_id"], spec_b["shard_id"]}
        sql = f"""
            SELECT
                dst_shard_id,
                COUNT(*) AS ref_count,
                'MOUNTED' AS status
            FROM "{refs_view}"
            GROUP BY dst_shard_id
        """
        result = engine.query_json(sql)
        for row in result["rows"]:
            dst_id, count, _ = row
            status = "MOUNTED ✓" if dst_id in mounted_ids else "BROKEN ✗"
            print(f"  {dst_id[:48]}...  refs={count}  [{status}]")
    else:
        print("  (skipped)")

    print(f"\n{'='*72}")
    print("  Done.")
    print(f"{'='*72}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <embodied_shard_dir> <gold_shard_dir>")
        sys.exit(1)

    embodied = Path(sys.argv[1])
    gold     = Path(sys.argv[2])

    if not embodied.exists():
        print(f"ERROR: embodied shard not found: {embodied}")
        sys.exit(1)
    if not gold.exists():
        print(f"ERROR: gold shard not found: {gold}")
        sys.exit(1)

    run_queries(embodied, gold)
