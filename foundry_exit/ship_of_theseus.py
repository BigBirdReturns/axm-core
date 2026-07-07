"""The Palantir Ship of Theseus — every plank sealed, each at its honest tier.

You don't leave Foundry in one move. You replace it one plank at a time, and this
module is the ship: it runs every plank-exit and seals ONE **ship manifest** — a
genesis shard recording all 9 planks of a Foundry deployment, the honest TIER at
which each is carried, and the child exit shard that sealed it.

9/9 planks now produce a sealed, detached-verifiable artifact — but "sealed" is
not "fully sovereign," and the manifest never pretends otherwise. Each plank
carries a tier:

  * FULL      — the whole surface travels (ontology structure/data, pipeline
                schemas/DAG). 4 planks.
  * CONTRACT  — definitions + source travel; the engine/runtime does NOT
                (actions, functions). 2 planks.
  * SOURCE    — the source travels verbatim; the runtime is rebuilt on your infra
                (transform runtime). 1 plank.
  * ATTESTED  — what's exportable is sealed and the rest is honestly attested
                (apps: Slate yes / Workshop-AIP no-export; permissions:
                deliberate non-port). 2 planks.

So the honest headline is "9/9 sealed · 4 full-surface, the rest carrying exactly
what can be carried and attesting the rest" — never "9/9 sovereign." The ship
still floats before every plank is FULL: the load-bearing hull (meaning + data +
data-flow) is full-surface today.

No Palantir code, no credentials, no network. The manifest is itself a sealed,
detached-verifiable genesis shard; custody is the genesis ``sh1_``.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import _seal_common as SC
from . import logic_exit, residual_exit, run_ontology_exit, run_pipeline_exit
from .exit_test import verify_detached

NAMESPACE = "foundry/ship-of-theseus"

# Honest tiers — what actually travels for a plank.
FULL = "FULL"          # whole surface
CONTRACT = "CONTRACT"  # definitions + source; not the engine/runtime
SOURCE = "SOURCE"      # source verbatim; not the runtime
ATTESTED = "ATTESTED"  # exportable sealed + the rest honestly attested

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


@dataclass(frozen=True)
class Plank:
    id: str
    surface: str
    tier: str
    replacement: str
    exit: str   # exit key that seals this plank (see EXITS)


# The honest ledger. Every plank has an exit; the TIER states what that exit
# actually carries. Must not drift from WORKFLOW_EXIT_MAP.md.
SHIP_PLANKS: Tuple[Plank, ...] = (
    Plank("ontology-structure", "Ontology structure (object types, properties, keys, links)",
          FULL, "genesis claims via axm-exit; Spectra-queryable", "ontology"),
    Plank("ontology-data", "Ontology instance data + verbatim API responses",
          FULL, "sealed content byte-for-byte + declared/captured counts", "ontology"),
    Plank("pipeline-schemas", "Dataset schemas (typed columns)",
          FULL, "genesis claims via axm-pipeline-exit", "pipeline"),
    Plank("pipeline-dag", "Dependency DAG + build/schedule provenance",
          FULL, "genesis claims (dataset feeds dataset), Spectra-queryable", "pipeline"),
    Plank("actions", "Actions (writeback ops, typed parameters)",
          CONTRACT, "definitions sealed via published Actions API; engine rebuilt", "logic"),
    Plank("functions", "Functions / Queries (server-side logic)",
          CONTRACT, "query defs + function source sealed; runtime rebuilt", "logic"),
    Plank("pipeline-runtime", "Transform source & runtime",
          SOURCE, "transform source sealed verbatim; runtime rebuilt on your infra", "residual:source"),
    Plank("apps", "Applications — Workshop / Slate / AIP",
          ATTESTED, "Slate JSON sealed; Workshop/AIP no-export attested, rebuild-forward on OSDK", "residual:apps"),
    Plank("permissions", "Permission / security model",
          ATTESTED, "deliberate non-port attestation; authorization under your own policy", "residual:policy"),
)


# ---------------------------------------------------------------------------
# Run every plank-exit that has a capture
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChildExit:
    exit: str
    shard_id: str
    verify: str


def _ont(cap, out):  return run_ontology_exit.run(cap, out)
def _pipe(cap, out): return run_pipeline_exit.run(cap, out)
def _logic(cap, out): return logic_exit.run(cap, out)


# exit key -> (runner(capture,out)->packet, default synthetic sample, real-mode subdir)
EXITS: Dict[str, Tuple[Callable, Path, str]] = {
    "ontology":        (_ont,  SAMPLES / "foundry_ontology_fixture", "ontology"),
    "pipeline":        (_pipe, SAMPLES / "pipeline_exit_synthetic",  "pipeline"),
    "logic":           (_logic, SAMPLES / "logic_exit_synthetic",    "logic"),
    "residual:source": (lambda cap, out: residual_exit.run("source", cap, out), SAMPLES / "residual_source_synthetic", "source"),
    "residual:apps":   (lambda cap, out: residual_exit.run("apps",   cap, out), SAMPLES / "residual_apps_synthetic",   "apps"),
    "residual:policy": (lambda cap, out: residual_exit.run("policy", cap, out), SAMPLES / "residual_policy_synthetic", "policy"),
}


def _collect(capture_root: Optional[Path], work: Path) -> Dict[str, ChildExit]:
    """Run the plank-exits whose captures are available.

    demo (capture_root is None): run every exit against its synthetic sample.
    real (capture_root given): run only exits whose subdir is present — never a
    silent synthetic fallback under a real root.
    """
    children: Dict[str, ChildExit] = {}
    for i, (key, (runner, sample, subdir)) in enumerate(EXITS.items()):
        if capture_root is None:
            cap = sample
        else:
            cap = capture_root / subdir
            if not cap.exists():
                continue
        packet = runner(cap, work / f"child_{i}")
        children[key] = ChildExit(key, packet["shard_id"], packet["verification"]["status"])
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
    claim_count: int
    entity_count: int


def _seal(children: Dict[str, ChildExit], out_shard_dir: Path) -> Tuple[SealedShip, Dict[str, int]]:
    c = SC.Claims(NAMESPACE)
    ship = c.entity("ship/foundry-exit", "ship")
    for child in {ch.shard_id: ch for ch in children.values()}.values():
        sh = c.entity(f"shard/{child.shard_id}", "sealed_shard")
        c.claim(sh, "verify", child.verify, "literal:string", 1)
    tiers: Dict[str, int] = {FULL: 0, CONTRACT: 0, SOURCE: 0, ATTESTED: 0}
    sealed_count = 0
    for pk in SHIP_PLANKS:
        pl = c.entity(f"plank/{pk.id}", "plank")
        c.claim(ship, "has_plank", pl, "entity", 1)
        c.claim(pl, "surface", pk.surface, "literal:string", 1)
        c.claim(pl, "tier", pk.tier, "literal:string", 1)
        c.claim(pl, "replacement", pk.replacement, "literal:string", 1)
        tiers[pk.tier] += 1
        child = children.get(pk.exit)
        if child is not None:
            c.claim(pl, "sealed_as", f"shard/{child.shard_id}", "entity", 1)
            sealed_count += 1
    c.claim(ship, "planks_sealed", str(sealed_count), "literal:integer", 0)
    c.claim(ship, "total_planks", str(len(SHIP_PLANKS)), "literal:integer", 0)
    c.claim(ship, "full_surface_planks", str(tiers[FULL]), "literal:integer", 0)
    c.claim(ship, "tier_breakdown",
            f"FULL:{tiers[FULL]} CONTRACT:{tiers[CONTRACT]} SOURCE:{tiers[SOURCE]} ATTESTED:{tiers[ATTESTED]}",
            "literal:string", 0)

    candidates, source_text, _ = c.build()
    manifest = {"ship": "foundry-exit",
                "children": [{"exit": ch.exit, "shard_id": ch.shard_id, "verify": ch.verify} for ch in children.values()]}
    content = {"ship_children.json": (json.dumps(manifest, indent=2) + "\n").encode("utf-8")}
    r = SC.seal(candidates, source_text, content, out_shard_dir,
                namespace=NAMESPACE, title="Palantir Ship of Theseus — exit ship manifest")
    return SealedShip(r.shard_id, r.shard_dir, r.trusted_key_path, r.suite, r.claim_count, r.entity_count), {
        **tiers, "planks_sealed": sealed_count}


def run(capture_root: Optional[Path], out_dir: Path) -> dict:
    if not SC.kernel_available():
        raise SystemExit("axm-genesis kernel not on PATH (need axm-build / axm-verify).")
    work = Path(tempfile.mkdtemp(prefix="ship_of_theseus_"))
    children = _collect(capture_root, work)
    ship, tiers = _seal(children, work / "shard")
    verify = verify_detached(ship.shard_dir, ship.trusted_key_path)

    packet = {
        "artifact": "Palantir Ship of Theseus — exit ship manifest v0",
        "claim": ("One sealed genesis shard recording every plank of a Foundry deployment, the honest "
                  "TIER at which each is carried, and the child exit shard that sealed it. 9/9 planks "
                  "sealed — 4 full-surface, the rest carrying exactly what can be carried and attesting "
                  "the rest. Never '9/9 sovereign.'"),
        "mode": "demo (synthetic samples)" if capture_root is None else f"capture_root={capture_root}",
        "shard_id": ship.shard_id,
        "signature_context": {"suite": ship.suite},
        "trusted_key_source": {"source": ship.trusted_key_path, "kind": "out-of-band"},
        "coverage": {
            "planks_sealed": tiers["planks_sealed"],
            "total_planks": len(SHIP_PLANKS),
            "full_surface_planks": tiers[FULL],
            "tier_breakdown": {FULL: tiers[FULL], CONTRACT: tiers[CONTRACT], SOURCE: tiers[SOURCE], ATTESTED: tiers[ATTESTED]},
            "child_exits": [{"exit": ch.exit, "shard_id": ch.shard_id, "verify": ch.verify} for ch in children.values()],
        },
        "planks": [{"id": p.id, "surface": p.surface, "tier": p.tier, "replacement": p.replacement} for p in SHIP_PLANKS],
        "verification": {"status": verify.get("status"), "exit_code": verify.get("exit_code"),
                         "verifier": "axm-verify shard <dir> --trusted-key <oob_pub> (real genesis kernel)"},
        "evidence_tier": ("The ship manifest is a sealed, detached-verifiable genesis shard. Plank tiers are "
                          "the honest ledger from WORKFLOW_EXIT_MAP.md; 'sealed' means an artifact exists at "
                          "the stated tier, NOT that the whole surface is sovereign. In demo mode the child "
                          "shards are synthetic samples, not a live tenant."),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "ship_packet.json").write_text(json.dumps(packet, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out_dir / "ship_packet.md").write_text(_render_md(packet), encoding="utf-8")
    return packet


_TIER_MARK = {FULL: "✅ full surface", CONTRACT: "🟩 contract (defs+source)",
              SOURCE: "🔧 source (runtime rebuilt)", ATTESTED: "📝 attested"}


def _render_md(p: dict) -> str:
    cov = p["coverage"]
    v = p["verification"]
    tb = cov["tier_breakdown"]
    lines = [
        f"# {p['artifact']}", "",
        f"**Claim:** {p['claim']}", "",
        f"- mode: {p['mode']}",
        f"- ship shard id: `{p['shard_id']}`",
        f"- suite: `{p['signature_context']['suite']}` · verification: **{v['status']}** (exit {v['exit_code']})",
        f"- **planks sealed: {cov['planks_sealed']} / {cov['total_planks']}** · "
        f"tiers — FULL:{tb[FULL]} · CONTRACT:{tb[CONTRACT]} · SOURCE:{tb[SOURCE]} · ATTESTED:{tb[ATTESTED]}",
        "", "## The planks", "",
        "| Plank | Tier | What travels |", "|---|---|---|",
    ]
    for pk in p["planks"]:
        lines.append(f"| {pk['surface']} | {_TIER_MARK.get(pk['tier'], pk['tier'])} | {pk['replacement']} |")
    lines += ["", "## Child exits sealed this run"]
    for ch in cov["child_exits"]:
        lines.append(f"- **{ch['exit']}** → shard `{ch['shard_id']}` (verify {ch['verify']})")
    lines += ["", "## Evidence tier (honest)", f"- {p['evidence_tier']}", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Assemble the Palantir Ship of Theseus exit manifest.")
    ap.add_argument("capture_root", nargs="?", default=None,
                    help="Dir with ontology/ pipeline/ logic/ source/ apps/ policy/ subdirs. Omit for a synthetic demo ship.")
    ap.add_argument("--out", default="ship_out")
    args = ap.parse_args(argv)
    root = Path(args.capture_root) if args.capture_root else None
    packet = run(root, Path(args.out))
    print(_render_md(packet))
    ok = packet["verification"]["status"] == "PASS"
    cov = packet["coverage"]
    tb = cov["tier_breakdown"]
    print(f"[ship of theseus: {'OK' if ok else 'FAIL'} — shard={packet['shard_id']}, "
          f"verify={packet['verification']['status']}, "
          f"sealed={cov['planks_sealed']}/{cov['total_planks']}, "
          f"tiers FULL:{tb[FULL]} CONTRACT:{tb[CONTRACT]} SOURCE:{tb[SOURCE]} ATTESTED:{tb[ATTESTED]}]")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
