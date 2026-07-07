"""Pipeline Exit v0 — one command: capture dir -> sealed, verified shard.

    python -m foundry_exit.run_pipeline_exit <capture_dir> --out <dir>
    axm-pipeline-exit <capture_dir> --out <dir>

Pipeline:
    load  (pipeline_api.load_pipeline_capture)   parse + verbatim bytes
      -> seal      (pipeline_seal.seal_pipeline_capture)   real genesis compiler
      -> verify DETACHED (axm-verify subprocess, out-of-band key)
      -> packet    (JSON + md: shard_id, counts, DAG edges, verify, tier)

Carries pipeline STRUCTURE only (schemas, dependency DAG, provenance). Does NOT
carry the transform runtime — see WORKFLOW_EXIT_MAP.md Layer 2. No Palantir
code, no credentials, no network. Exits nonzero if detached verify != PASS.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from .exit_test import verify_detached
from .pipeline_api import load_pipeline_capture
from .pipeline_seal import kernel_available, seal_pipeline_capture

DEFAULT_CAPTURE = (
    Path(__file__).resolve().parent.parent / "samples" / "pipeline_exit_synthetic"
)


def run(capture_dir: Path, out_dir: Path) -> dict:
    if not kernel_available():
        raise SystemExit(
            "axm-genesis kernel not on PATH (need `axm-build` and `axm-verify`).\n"
            "Install it, e.g.:  pip install -e '/path/to/axm-genesis[mldsa-compat]'"
        )

    capture = load_pipeline_capture(capture_dir)

    work = Path(tempfile.mkdtemp(prefix="pipeline_exit_v0_"))
    sealed = seal_pipeline_capture(capture, work / "shard")

    exit_result = verify_detached(sealed.shard_dir, sealed.trusted_key_path)

    packet = _build_packet(capture, sealed, exit_result)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "pipeline_exit_packet.json").write_text(
        json.dumps(packet, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (out_dir / "pipeline_exit_packet.md").write_text(_render_md(packet), encoding="utf-8")
    return packet


def _build_packet(capture, sealed, exit_result) -> dict:
    edges = [{"from": s, "to": t, "via_build": b} for s, t, b in capture.edges()]
    return {
        "artifact": "AXM Foundry Pipeline Exit v0",
        "claim": (
            "A tenant owner's captured Datasets + Orchestration API v2 responses, "
            "sealed as a genesis shard: dataset schemas and the dependency DAG "
            "queryable through Spectra, verbatim responses preserved as sealed "
            "content, detached-verifiable with an out-of-band key. Structure only "
            "— not the transform runtime."
        ),
        "shard_id": sealed.shard_id,
        "signature_context": {
            "suite": sealed.suite,
            "merkle_root": sealed.merkle_root,
            "sealed_at": sealed.sealed_at,
        },
        "trusted_key_source": {"source": sealed.trusted_key_path, "kind": "out-of-band"},
        "counts": {
            "datasets": sealed.dataset_count,
            "dag_edges": sealed.edge_count,
            "entities": sealed.entity_count,
            "claims": sealed.claim_count,
            "sealed_content_files": len(capture.files),
        },
        "datasets": [d.name for d in capture.datasets],
        "dag_edges": edges,
        "verification": {
            "status": exit_result.get("status"),
            "exit_code": exit_result.get("exit_code"),
            "verifier": "axm-verify shard <dir> --trusted-key <oob_pub> (real genesis kernel)",
            "palantir_involved": exit_result.get("palantir_involved"),
        },
        "external_ids_note": (
            "Palantir rid values are EXTERNAL ids, carried verbatim as sealed "
            "content only. They are never the shard identity and never a custody "
            "id (custody id is the genesis sh1_)."
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
        f"- datasets: {c['datasets']}",
        f"- dependency DAG edges: {c['dag_edges']}",
        f"- entities: {c['entities']} · claims: {c['claims']}",
        f"- sealed content files (verbatim): {c['sealed_content_files']}",
        "",
        "## Datasets",
        f"- {p['datasets']}",
        "",
        "## Dependency DAG (dataset feeds dataset)",
    ]
    if p["dag_edges"]:
        for e in p["dag_edges"]:
            lines.append(f"- `{e['from']}` → `{e['to']}`  (via build `{e['via_build']}`)")
    else:
        lines.append("- (no build I/O captured — no edges inferred)")
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
    ap = argparse.ArgumentParser(description="Run AXM Foundry Pipeline Exit v0.")
    ap.add_argument("capture_dir", nargs="?", default=str(DEFAULT_CAPTURE),
                    help="Directory of captured Datasets + Orchestration API v2 responses.")
    ap.add_argument("--out", default="pipeline_exit_out")
    args = ap.parse_args(argv)
    packet = run(Path(args.capture_dir), Path(args.out))
    print(_render_md(packet))
    ok = packet["verification"]["status"] == "PASS"
    print(f"[pipeline exit v0: {'OK' if ok else 'FAIL'} — "
          f"shard={packet['shard_id']}, verify={packet['verification']['status']}, "
          f"datasets={packet['counts']['datasets']}, edges={packet['counts']['dag_edges']}, "
          f"claims={packet['counts']['claims']}]")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
