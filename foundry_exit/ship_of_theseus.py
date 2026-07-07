"""The Palantir Ship of Theseus — assemble every plank into one sovereign hull.

You don't leave Foundry in a single move. You replace it one plank at a time —
the ontology, then its data, then the pipeline schemas, then the dependency DAG —
each swapped for a sovereign, sealed, detached-verifiable equivalent. This module
is the ship: it runs whichever plank-exits have captures, and seals ONE **ship
manifest** — a genesis shard that records, for every plank of a Foundry
deployment, its sovereign-replacement status and (where a plank has actually been
swapped this run) the child exit shard that replaced it.

The whole point is honesty about which planks are yours yet. Some planks are
PROVEN (a tested exit seals them). Some are MAPPED (a design exists, not built).
One is CUSTOMER_REBUILD (no export exists — you rebuild the runtime on your own
infrastructure). One is ANTI_GOAL (the permission model — reconstructed under
your OWN policy, never ported). The ship manifest states each, and the ship still
floats before every plank is swapped: the load-bearing hull (meaning + data +
data-flow) is sovereign today.

No Palantir code, no credentials, no network. The ship manifest is itself a
sealed, detached-verifiable genesis shard; custody is the genesis ``sh1_``.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import run_ontology_exit, run_pipeline_exit
from .exit_test import verify_detached
from .seal import AXM_BUILD, kernel_available

NAMESPACE = "foundry/ship-of-theseus"
SOURCE_FILE = "source.txt"

# Capability tiers (honest, matches WORKFLOW_EXIT_MAP.md):
PROVEN = "PROVEN"                    # a tested exit seals this plank
MAPPED = "MAPPED"                    # design exists (published wire shape), not built
NO_EXPORT = "NO_EXPORT"             # no self-contained export; rebuild forward (OSDK)
CUSTOMER_REBUILD = "CUSTOMER_REBUILD"  # runtime; no export can exist; rebuild on own infra
ANTI_GOAL = "ANTI_GOAL"             # deliberately not carried (reconstruct under own policy)

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
ONTOLOGY_SAMPLE = SAMPLES / "foundry_ontology_fixture"
PIPELINE_SAMPLE = SAMPLES / "pipeline_exit_synthetic"


@dataclass(frozen=True)
class Plank:
    id: str                 # short stable id, e.g. "ontology-structure"
    surface: str            # the Foundry surface this plank is
    capability: str         # PROVEN | MAPPED | NO_EXPORT | CUSTOMER_REBUILD | ANTI_GOAL
    replacement: str        # the sovereign replacement (or why it isn't carried)
    exit: Optional[str]     # which exit swaps it, if any ("ontology" | "pipeline" | None)


# The honest ledger of a Foundry deployment's planks. This is the source of
# truth; it must not drift from WORKFLOW_EXIT_MAP.md.
SHIP_PLANKS: Tuple[Plank, ...] = (
    Plank("ontology-structure", "Ontology structure (object types, properties, keys, links)",
          PROVEN, "genesis claims via axm-exit; Spectra-queryable, detached-verifiable", "ontology"),
    Plank("ontology-data", "Ontology instance data + verbatim API responses",
          PROVEN, "sealed content byte-for-byte + tier-0 declared/captured counts", "ontology"),
    Plank("pipeline-schemas", "Dataset schemas (typed columns)",
          PROVEN, "genesis claims via axm-pipeline-exit", "pipeline"),
    Plank("pipeline-dag", "Dependency DAG + build/schedule provenance",
          PROVEN, "genesis claims (dataset feeds dataset), Spectra-queryable", "pipeline"),
    Plank("pipeline-runtime", "Transform runtime (transforms framework, Spark, incremental)",
          CUSTOMER_REBUILD, "no export exists; rebuilt on your own infra (Spark/dbt/Airflow)", None),
    Plank("actions", "Actions engine (writeback, submission criteria, validation)",
          MAPPED, "definitions capturable via published Actions API; engine rebuilt", None),
    Plank("functions", "Functions / Queries (server-side logic)",
          MAPPED, "source git-cloneable; execution runtime rebuilt off-platform", None),
    Plank("apps", "Applications — Workshop / AIP (UI, agents)",
          NO_EXPORT, "no published export; rebuild forward on OSDK (own your code)", None),
    Plank("permissions", "Permission / security model (markings, PBAC, ACLs)",
          ANTI_GOAL, "reconstructed under your OWN policy at the destination, never ported", None),
)


# ---------------------------------------------------------------------------
# Run the plank-exits that have captures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChildExit:
    exit: str               # "ontology" | "pipeline"
    shard_id: str
    verify: str             # PASS | FAIL | ...
    counts: Dict[str, int]


def _run_ontology(capture: Path, work: Path) -> ChildExit:
    packet = run_ontology_exit.run(capture, work / "ontology_out")
    return ChildExit("ontology", packet["shard_id"], packet["verification"]["status"], packet["counts"])


def _run_pipeline(capture: Path, work: Path) -> ChildExit:
    packet = run_pipeline_exit.run(capture, work / "pipeline_out")
    return ChildExit("pipeline", packet["shard_id"], packet["verification"]["status"], packet["counts"])


def _collect_children(capture_root: Optional[Path], work: Path) -> Dict[str, ChildExit]:
    """Run the plank-exits whose captures are available.

    demo mode (capture_root is None): run BOTH synthetic samples — a complete
    demonstration ship. Real mode (capture_root given): run only the exits whose
    subdir (``ontology/`` and/or ``pipeline/``) is present. We never silently
    fall back to a synthetic sample under a real capture root — mixing real and
    synthetic would be exactly the dishonesty this project refuses.
    """
    children: Dict[str, ChildExit] = {}
    if capture_root is None:
        children["ontology"] = _run_ontology(ONTOLOGY_SAMPLE, work)
        children["pipeline"] = _run_pipeline(PIPELINE_SAMPLE, work)
        return children
    ont = capture_root / "ontology"
    pipe = capture_root / "pipeline"
    if ont.exists():
        children["ontology"] = _run_ontology(ont, work)
    if pipe.exists():
        children["pipeline"] = _run_pipeline(pipe, work)
    return children


# ---------------------------------------------------------------------------
# Seal the ship manifest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SealedShip:
    shard_id: str
    shard_dir: str
    trusted_key_path: str
    suite: str
    merkle_root: Optional[str]
    claim_count: int
    entity_count: int


@dataclass
class _Stmt:
    text: str
    subject_label: str
    predicate: str
    object_label: str
    object_type: str
    tier: int


def _build_candidates_and_source(children: Dict[str, ChildExit]) -> Tuple[List[dict], str, Dict[str, int]]:
    entities: Dict[str, dict] = {}

    def ent(label: str, entity_type: str) -> None:
        entities.setdefault(label, {
            "type": "entity", "namespace": NAMESPACE, "label": label, "entity_type": entity_type,
        })

    ship = "ship/foundry-exit"
    ent(ship, "ship")
    stmts: List[_Stmt] = []

    # Each unique child shard gets ONE verify claim, regardless of how many planks
    # it seals (two ontology planks share one ontology shard, etc.). Emitting it
    # per-plank would produce identical claims -> duplicate claim ids.
    for child in {c.shard_id: c for c in children.values()}.values():
        sh = f"shard/{child.shard_id}"
        ent(sh, "sealed_shard")
        stmts.append(_Stmt(f'{sh} verify "{child.verify}"', sh, "verify", child.verify, "literal:string", 1))

    proven = 0
    for pk in SHIP_PLANKS:
        pl = f"plank/{pk.id}"
        ent(pl, "plank")
        stmts.append(_Stmt(f"{ship} has_plank {pl}", ship, "has_plank", pl, "entity", 1))
        stmts.append(_Stmt(f'{pl} surface "{pk.surface}"', pl, "surface", pk.surface, "literal:string", 1))
        stmts.append(_Stmt(f'{pl} status "{pk.capability}"', pl, "status", pk.capability, "literal:string", 1))
        stmts.append(_Stmt(f'{pl} replacement "{pk.replacement}"', pl, "replacement", pk.replacement, "literal:string", 1))
        if pk.capability == PROVEN:
            proven += 1
        # If a plank's exit actually ran this invocation, bind the child shard.
        child = children.get(pk.exit) if pk.exit else None
        if child is not None:
            sh = f"shard/{child.shard_id}"
            stmts.append(_Stmt(f"{pl} sealed_as {sh}", pl, "sealed_as", sh, "entity", 1))

    stmts.append(_Stmt(f'{ship} sovereign_planks "{proven}"', ship, "sovereign_planks", str(proven), "literal:integer", 0))
    stmts.append(_Stmt(f'{ship} total_planks "{len(SHIP_PLANKS)}"', ship, "total_planks", str(len(SHIP_PLANKS)), "literal:integer", 0))

    claims: List[dict] = []
    source = ""
    for s in stmts:
        start = len(source.encode("utf-8"))
        source += s.text
        end = len(source.encode("utf-8"))
        source += "\n"
        claims.append({
            "type": "claim",
            "subject_label": s.subject_label,
            "predicate": s.predicate,
            "object_label": s.object_label,
            "object_type": s.object_type,
            "tier": s.tier,
            "evidence": {"source_file": SOURCE_FILE, "byte_start": start, "byte_end": end, "text": s.text},
        })

    candidates = list(entities.values()) + claims
    return candidates, source, {"entities": len(entities), "claims": len(claims)}


def _seal_ship(children: Dict[str, ChildExit], out_shard_dir: Path) -> SealedShip:
    work = out_shard_dir.parent
    content_dir = work / "_ship_content"
    key_dir = work / "keys"
    if content_dir.exists():
        shutil.rmtree(content_dir)
    content_dir.mkdir(parents=True, exist_ok=True)
    key_dir.mkdir(parents=True, exist_ok=True)

    candidates, source_text, counts = _build_candidates_and_source(children)
    (content_dir / SOURCE_FILE).write_text(source_text, encoding="utf-8")
    # A human-readable manifest of the referenced child shards, sealed as content.
    manifest = {
        "ship": "foundry-exit",
        "children": [
            {"plank_exit": c.exit, "shard_id": c.shard_id, "verify": c.verify, "counts": c.counts}
            for c in children.values()
        ],
    }
    (content_dir / "ship_children.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    candidates_path = work / "ship_candidates.jsonl"
    candidates_path.write_text("\n".join(json.dumps(c) for c in candidates) + "\n", encoding="utf-8")

    key_path = key_dir / "publisher.key"
    pub_path = key_dir / "publisher.pub"
    if not (key_path.exists() and pub_path.exists()):
        subprocess.run([AXM_BUILD, "keygen", str(key_dir), "--name", "publisher"],
                       check=True, capture_output=True, text=True)
    subprocess.run(
        [AXM_BUILD, "compile", str(candidates_path), str(content_dir), str(out_shard_dir),
         "--private-key", str(key_path), "--namespace", NAMESPACE,
         "--title", "Palantir Ship of Theseus — exit ship manifest",
         "--created-at", "2026-07-07T00:00:00Z"],
        check=True, capture_output=True, text=True,
    )
    manifest_bytes = (out_shard_dir / "manifest.json").read_bytes()
    m = json.loads(manifest_bytes)
    from axm_verify.crypto import derive_shard_id
    return SealedShip(
        shard_id=derive_shard_id(manifest_bytes),
        shard_dir=str(out_shard_dir),
        trusted_key_path=str(pub_path),
        suite=m.get("suite", "axm-hybrid1"),
        merkle_root=(m.get("integrity") or {}).get("merkle_root"),
        claim_count=counts["claims"],
        entity_count=counts["entities"],
    )


# ---------------------------------------------------------------------------
# Orchestrate
# ---------------------------------------------------------------------------


def run(capture_root: Optional[Path], out_dir: Path) -> dict:
    if not kernel_available():
        raise SystemExit(
            "axm-genesis kernel not on PATH (need `axm-build` and `axm-verify`).\n"
            "Install it, e.g.:  pip install -e '/path/to/axm-genesis[mldsa-compat]'"
        )
    work = Path(tempfile.mkdtemp(prefix="ship_of_theseus_"))
    children = _collect_children(capture_root, work)
    ship = _seal_ship(children, work / "shard")
    verify = verify_detached(ship.shard_dir, ship.trusted_key_path)

    proven = [p for p in SHIP_PLANKS if p.capability == PROVEN]
    packet = {
        "artifact": "Palantir Ship of Theseus — exit ship manifest v0",
        "claim": (
            "One sealed genesis shard recording every plank of a Foundry deployment, "
            "its sovereign-replacement status, and the child exit shards that have "
            "actually swapped a plank this run. The ship floats before every plank is "
            "replaced; the load-bearing hull (meaning + data + data-flow) is sovereign today."
        ),
        "mode": "demo (synthetic samples)" if capture_root is None else f"capture_root={capture_root}",
        "shard_id": ship.shard_id,
        "signature_context": {"suite": ship.suite, "merkle_root": ship.merkle_root},
        "trusted_key_source": {"source": ship.trusted_key_path, "kind": "out-of-band"},
        "coverage": {
            "sovereign_planks": len(proven),
            "total_planks": len(SHIP_PLANKS),
            "planks_swapped_this_run": [
                {"exit": c.exit, "shard_id": c.shard_id, "verify": c.verify}
                for c in children.values()
            ],
        },
        "planks": [
            {"id": p.id, "surface": p.surface, "status": p.capability, "replacement": p.replacement}
            for p in SHIP_PLANKS
        ],
        "verification": {
            "status": verify.get("status"),
            "exit_code": verify.get("exit_code"),
            "verifier": "axm-verify shard <dir> --trusted-key <oob_pub> (real genesis kernel)",
        },
        "evidence_tier": (
            "The ship manifest is a sealed, detached-verifiable genesis shard. Plank "
            "statuses are the honest ledger from WORKFLOW_EXIT_MAP.md: PROVEN planks "
            "have tested exits; MAPPED/NO_EXPORT/CUSTOMER_REBUILD/ANTI_GOAL planks are "
            "NOT sealed and say so. In demo mode the child shards are synthetic samples, "
            "not a live tenant."
        ),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "ship_packet.json").write_text(json.dumps(packet, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out_dir / "ship_packet.md").write_text(_render_md(packet), encoding="utf-8")
    return packet


_STATUS_MARK = {PROVEN: "✅ sovereign", MAPPED: "🟡 mapped", NO_EXPORT: "🔴 rebuild-forward",
                CUSTOMER_REBUILD: "🔧 you rebuild", ANTI_GOAL: "⚫ not carried (by design)"}


def _render_md(p: dict) -> str:
    cov = p["coverage"]
    v = p["verification"]
    lines = [
        f"# {p['artifact']}",
        "",
        f"**Claim:** {p['claim']}",
        "",
        f"- mode: {p['mode']}",
        f"- ship shard id: `{p['shard_id']}`",
        f"- suite: `{p['signature_context']['suite']}` · verification: **{v['status']}** (exit {v['exit_code']})",
        f"- **sovereign planks: {cov['sovereign_planks']} / {cov['total_planks']}**",
        "",
        "## The planks",
        "",
        "| Plank | Status | Sovereign replacement |",
        "|---|---|---|",
    ]
    for pk in p["planks"]:
        lines.append(f"| {pk['surface']} | {_STATUS_MARK.get(pk['status'], pk['status'])} | {pk['replacement']} |")
    lines += ["", "## Planks swapped this run"]
    if cov["planks_swapped_this_run"]:
        for s in cov["planks_swapped_this_run"]:
            lines.append(f"- **{s['exit']}** → shard `{s['shard_id']}` (verify {s['verify']})")
    else:
        lines.append("- (no plank captures provided)")
    lines += ["", "## Evidence tier (honest)", f"- {p['evidence_tier']}", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Assemble the Palantir Ship of Theseus exit manifest.")
    ap.add_argument("capture_root", nargs="?", default=None,
                    help="Dir with ontology/ and/or pipeline/ capture subdirs. Omit for a synthetic demo ship.")
    ap.add_argument("--out", default="ship_out")
    args = ap.parse_args(argv)
    root = Path(args.capture_root) if args.capture_root else None
    packet = run(root, Path(args.out))
    print(_render_md(packet))
    ok = packet["verification"]["status"] == "PASS"
    cov = packet["coverage"]
    print(f"[ship of theseus: {'OK' if ok else 'FAIL'} — shard={packet['shard_id']}, "
          f"verify={packet['verification']['status']}, "
          f"sovereign={cov['sovereign_planks']}/{cov['total_planks']}, "
          f"swapped_this_run={len(cov['planks_swapped_this_run'])}]")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
