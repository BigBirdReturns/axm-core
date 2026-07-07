"""Residual Exit v0 — the three planks with no full-surface export, sealed honestly.

Some planks of a Foundry deployment cannot be carried in full, and one must not
be. This module seals each to its honest maximum, and ATTESTS the boundary on the
shard itself:

  * ``source``  — transform SOURCE bundle (Code Repositories git clone), sealed
    VERBATIM. The runtime is not carried and cannot be exported; you rebuild it on
    Spark/dbt/Airflow. (Plank: pipeline-runtime — the recoverable half.)
  * ``apps``    — documented app exports (e.g. Slate JSON) sealed verbatim, and an
    honest NO-EXPORT attestation for Workshop/AIP (rebuild-forward on OSDK).
    (Plank: apps.)
  * ``policy``  — a sealed attestation that the Foundry permission model was
    DELIBERATELY NOT PORTED; authorization is reconstructed under the customer's
    own policy at the destination, and the channel activates on real data only on
    a controller's lawful authorization. (Plank: permissions — the anti-goal made
    provable.)

Each kind produces a sealed, detached-verifiable genesis shard. No Palantir code,
no credentials, no network. Custody is the genesis ``sh1_``.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Tuple

from . import _seal_common as SC
from .exit_test import verify_detached

KINDS = ("source", "apps", "policy")
NAMESPACE = {k: f"foundry/residual-{k}" for k in KINDS}
SAMPLES = Path(__file__).resolve().parent.parent / "samples"
DEFAULT_CAPTURE = {k: SAMPLES / f"residual_{k}_synthetic" for k in KINDS}

_LANG = {".py": "python", ".ts": "typescript", ".java": "java", ".sql": "sql", ".scala": "scala", ".r": "r"}


class ResidualCaptureError(ValueError):
    pass


def _collect_files(root: Path) -> List[Tuple[str, bytes]]:
    """(relpath, bytes) for every file under root, sorted, one level of nesting max."""
    out: List[Tuple[str, bytes]] = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.name != "README.md":
            rel = str(p.relative_to(root))
            out.append((rel, p.read_bytes()))
    return out


def _build_source(capture_dir: Path):
    files = _collect_files(capture_dir)
    if not files:
        raise ResidualCaptureError("source capture is empty (no transform source files)")
    c = SC.Claims(NAMESPACE["source"])
    ent = c.entity("source-bundle", "residual_exit")
    c.claim(ent, "carries", "transform source (verbatim, byte-for-byte)", "literal:string", 1)
    c.claim(ent, "not_carried", "transform runtime (transforms framework, Spark, incremental engine)", "literal:string", 1)
    content: Dict[str, bytes] = {}
    for rel, raw in files:
        flat = rel.replace("/", "__")
        content[flat] = raw
        sl = c.entity(f"source/{rel}", "source_file")
        c.claim(sl, "sealed", "verbatim", "literal:string", 1)
        c.claim(sl, "language", _LANG.get(Path(rel).suffix.lower(), "other"), "literal:string", 1)
    candidates, source_text, counts = c.build()
    return candidates, source_text, content, {"source_files": len(files)}


def _build_apps(capture_dir: Path):
    manifest_path = capture_dir / "manifest.json"
    if not manifest_path.exists():
        raise ResidualCaptureError("apps capture needs a manifest.json listing apps")
    manifest = json.loads(manifest_path.read_bytes().decode("utf-8"))
    apps = manifest.get("apps") if isinstance(manifest, dict) else None
    if not isinstance(apps, list):
        raise ResidualCaptureError("manifest.json: 'apps' must be a JSON array")
    c = SC.Claims(NAMESPACE["apps"])
    ent = c.entity("apps-exit", "residual_exit")
    c.claim(ent, "carries", "documented app exports (e.g. Slate JSON) + honest no-export attestations", "literal:string", 1)
    c.claim(ent, "not_carried", "Workshop/AIP rendering runtime (no published export)", "literal:string", 1)
    content: Dict[str, bytes] = {"manifest.json": manifest_path.read_bytes()}
    sealed_exports = 0
    attested_noexport = 0
    for app in apps:
        name = str(app.get("name", "unnamed"))
        kind = str(app.get("kind", "unknown"))
        al = c.entity(f"app/{name}", "app")
        c.claim(al, "platform", kind, "literal:string", 1)
        export_file = app.get("exportFile")
        ep = (capture_dir / export_file) if export_file else None
        if ep and ep.exists():
            flat = export_file.replace("/", "__")
            content[flat] = ep.read_bytes()
            c.claim(al, "export", f"{kind}-json (sealed verbatim)", "literal:string", 1)
            sealed_exports += 1
        else:
            c.claim(al, "export", "none — no published export; rebuild-forward on OSDK", "literal:string", 1)
            attested_noexport += 1
    candidates, source_text, counts = c.build()
    return candidates, source_text, content, {"apps": len(apps), "sealed_exports": sealed_exports, "attested_no_export": attested_noexport}


def _build_policy(capture_dir: Path):
    c = SC.Claims(NAMESPACE["policy"])
    ent = c.entity("policy-attestation", "residual_exit")
    # The AXM anti-goal, made into a sealed, verifiable record.
    c.claim(ent, "foundry_permission_model_ported", "false — by design", "literal:string", 1)
    c.claim(ent, "reason", "porting a vendor's markings/PBAC/roles ports the surveillance, not the escape", "literal:string", 1)
    c.claim(ent, "authorization_reconstructed_under", "the customer's own policy at the destination", "literal:string", 1)
    c.claim(ent, "consent_gate", "real records flow only on a data controller's lawful authorization", "literal:string", 1)
    content: Dict[str, bytes] = {}
    # If the customer provides their OWN destination policy, seal it verbatim.
    pol = capture_dir / "policy.md"
    if pol.exists():
        content["policy.md"] = pol.read_bytes()
        c.claim(ent, "destination_policy_sealed", "policy.md", "literal:string", 1)
    candidates, source_text, counts = c.build()
    return candidates, source_text, content, {"attestations": counts["claims"], "destination_policy": pol.exists()}


_BUILDERS = {"source": _build_source, "apps": _build_apps, "policy": _build_policy}

_TIER = {
    "source": ("Transform SOURCE sealed verbatim (git-cloneable from Code Repositories). The transform "
               "RUNTIME has no portable export and is rebuilt on the customer's own infra; attested on the shard."),
    "apps": ("Documented app exports (e.g. Slate JSON) sealed verbatim; Workshop/AIP have NO published "
             "export and are attested as rebuild-forward on OSDK. Synthetic sample, not a live tenant."),
    "policy": ("An ATTESTATION shard: the Foundry permission model is deliberately NOT ported; authorization "
               "is reconstructed under the customer's own policy. Carries no vendor ACLs by design."),
}


def run(kind: str, capture_dir: Path, out_dir: Path) -> dict:
    if kind not in KINDS:
        raise SystemExit(f"unknown kind {kind!r}; choose from {KINDS}")
    if not SC.kernel_available():
        raise SystemExit("axm-genesis kernel not on PATH (need axm-build / axm-verify).")
    candidates, source_text, content, tallies = _BUILDERS[kind](Path(capture_dir))
    work = Path(tempfile.mkdtemp(prefix=f"residual_{kind}_v0_"))
    sealed = SC.seal(candidates, source_text, content, work / "shard",
                     namespace=NAMESPACE[kind], title=f"Foundry residual exit shard ({kind})")
    verify = verify_detached(sealed.shard_dir, sealed.trusted_key_path)
    packet = {
        "artifact": f"AXM Foundry Residual Exit v0 ({kind})",
        "shard_id": sealed.shard_id,
        "kind": kind,
        "counts": {**tallies, "entities": sealed.entity_count, "claims": sealed.claim_count,
                   "sealed_content_files": len(content)},
        "verification": {"status": verify.get("status"), "exit_code": verify.get("exit_code")},
        "evidence_tier": _TIER[kind],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"residual_{kind}_packet.json").write_text(json.dumps(packet, indent=2) + "\n", encoding="utf-8")
    return packet


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run AXM Foundry Residual Exit v0 (source | apps | policy).")
    ap.add_argument("kind", choices=KINDS)
    ap.add_argument("capture_dir", nargs="?", default=None)
    ap.add_argument("--out", default="residual_exit_out")
    args = ap.parse_args(argv)
    cap = Path(args.capture_dir) if args.capture_dir else DEFAULT_CAPTURE[args.kind]
    p = run(args.kind, cap, Path(args.out))
    ok = p["verification"]["status"] == "PASS"
    print(f"[residual exit v0 ({args.kind}): {'OK' if ok else 'FAIL'} — shard={p['shard_id']}, "
          f"verify={p['verification']['status']}, counts={p['counts']}]")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
