"""Ontology Exit v0 — one command: capture dir -> sealed, verified shard.

    python -m foundry_exit.run_ontology_exit <capture_dir> --out <dir>

Pipeline:
    load  (ontology_api.load_ontology_capture)   parse + verbatim bytes
      -> translate (ontology_api.to_exit_ontology)  superset ontology.json
      -> seal      (ontology_seal.seal_ontology_capture)  real genesis compiler
      -> verify DETACHED (axm-verify subprocess, out-of-band key)
      -> packet    (JSON + md: shard_id, counts, verify status, tier statement)

No Palantir code, no credentials, no network calls. Exits nonzero if the
detached verify does not PASS.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from .exit_test import verify_detached
from .ontology_api import load_ontology_capture, to_exit_ontology
from .ontology_seal import kernel_available, seal_ontology_capture

DEFAULT_CAPTURE = (
    Path(__file__).resolve().parent.parent / "samples" / "foundry_ontology_fixture"
)


def run(capture_dir: Path, out_dir: Path) -> dict:
    if not kernel_available():
        raise SystemExit(
            "axm-genesis kernel not on PATH (need `axm-build` and `axm-verify`).\n"
            "Install it, e.g.:  pip install -e '/path/to/axm-genesis[dev]'"
        )

    capture = load_ontology_capture(capture_dir)
    exit_ontology = to_exit_ontology(capture)   # translation kept for review/downstream

    work = Path(tempfile.mkdtemp(prefix="ontology_exit_v0_"))
    sealed = seal_ontology_capture(capture, work / "shard")

    # Detached verify: only the sealed shard bytes + the out-of-band public key.
    exit_result = verify_detached(sealed.shard_dir, sealed.trusted_key_path)

    packet = _build_packet(capture, exit_ontology, sealed, exit_result)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "ontology_exit_packet.json").write_text(
        json.dumps(packet, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (out_dir / "ontology_exit_packet.md").write_text(_render_md(packet), encoding="utf-8")
    return packet


def _build_packet(capture, exit_ontology, sealed, exit_result) -> dict:
    object_types = [ot["object_type_id"] for ot in exit_ontology["object_types"]]
    link_count = sum(len(v) for v in capture.links_by_source.values())
    instances = {
        t: {"rows_captured": len(p.rows), "total_count_declared": p.total_count}
        for t, p in capture.instances_by_type.items()
    }
    return {
        "artifact": "AXM Foundry Ontology Exit v0",
        "claim": (
            "A tenant owner's captured Ontology API v2 responses, sealed as a "
            "genesis shard: structure queryable through Spectra, verbatim responses "
            "preserved as sealed content, detached-verifiable with an out-of-band key."
        ),
        "shard_id": sealed.shard_id,
        "signature_context": {
            "suite": sealed.suite,
            "merkle_root": sealed.merkle_root,
            "sealed_at": sealed.sealed_at,
        },
        "trusted_key_source": {"source": sealed.trusted_key_path, "kind": "out-of-band"},
        "counts": {
            "object_types": len(object_types),
            "link_types": link_count,
            "entities": sealed.entity_count,
            "claims": sealed.claim_count,
            "sealed_content_files": len(capture.files),
        },
        "object_types": object_types,
        "instances": instances,
        "verification": {
            "status": exit_result.get("status"),
            "exit_code": exit_result.get("exit_code"),
            "verifier": "axm-verify shard <dir> --trusted-key <oob_pub> (real genesis kernel)",
            "palantir_involved": exit_result.get("palantir_involved"),
        },
        "external_ids_note": (
            "Palantir rid / apiName values are EXTERNAL ids, carried verbatim as "
            "labels, literals, and sealed content only. They are never the shard "
            "identity and never a custody id (custody id is the genesis sh1_)."
        ),
        "evidence_tier": sealed.tier_statement,
    }


def _render_md(p: dict) -> str:
    c = p["counts"]
    v = p["verification"]
    lines = [
        f"# {p['artifact']} — packet",
        "",
        f"**Claim:** {p['claim']}",
        "",
        f"- shard id: `{p['shard_id']}`",
        f"- suite: `{p['signature_context']['suite']}` · merkle_root: `{p['signature_context']['merkle_root']}`",
        f"- trusted key: `{p['trusted_key_source']['source']}` ({p['trusted_key_source']['kind']})",
        f"- verification: **{v['status']}** (exit {v['exit_code']}) via `{v['verifier']}`",
        "",
        "## Counts",
        f"- object types: {c['object_types']}",
        f"- link types: {c['link_types']}",
        f"- entities: {c['entities']} · claims: {c['claims']}",
        f"- sealed content files (verbatim): {c['sealed_content_files']}",
        "",
        "## Object types",
        f"- {p['object_types']}",
        "",
        "## Instances (declared vs captured)",
    ]
    if p["instances"]:
        for t, info in p["instances"].items():
            lines.append(
                f"- `{t}`: {info['rows_captured']} row(s) captured, "
                f"totalCount declared: {info['total_count_declared']}"
            )
    else:
        lines.append("- (no objects/*.json captured)")
    lines += [
        "",
        "## External ids",
        f"- {p['external_ids_note']}",
        "",
        "## Evidence tier (honest)",
        f"- {p['evidence_tier']}",
        "",
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run AXM Foundry Ontology Exit v0.")
    ap.add_argument("capture_dir", nargs="?", default=str(DEFAULT_CAPTURE),
                    help="Directory of captured Ontology API v2 responses.")
    ap.add_argument("--out", default="ontology_exit_out")
    args = ap.parse_args(argv)
    packet = run(Path(args.capture_dir), Path(args.out))
    print(_render_md(packet))
    ok = packet["verification"]["status"] == "PASS"
    print(f"[ontology exit v0: {'OK' if ok else 'FAIL'} — "
          f"shard={packet['shard_id']}, verify={packet['verification']['status']}, "
          f"object_types={packet['counts']['object_types']}, claims={packet['counts']['claims']}]")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
