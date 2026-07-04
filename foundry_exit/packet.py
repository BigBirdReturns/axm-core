"""Human-reviewable Foundry exit packet: what a reviewer inspects instead of
trusting a platform."""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from .planes import FoundryExitManifest
from .seal import SealedExitShard, VerifyStatus

BOUNDARY_NOTES = [
    "Palantir dataset RIDs / ontology object-type IDs are EXTERNAL ids, carried verbatim; "
    "they are NOT AXM custody ids (custody id is the genesis sh1_ on the sealed bundle).",
    "S3 is the dataset-byte interface only -- it does not import the ontology; ontology and "
    "lineage came from explicit metadata inputs.",
    "GhostBox is not in the import path: no ghostbox code is imported or called by the importer.",
    "The importer is read-only against sources: no write path to Palantir or any endpoint exists.",
    "Security markings are recorded for provenance only -- no Palantir permissions were made portable.",
]


def build_packet(
    manifest: FoundryExitManifest,
    sealed: SealedExitShard,
    verify_status: VerifyStatus,
    *,
    exit_test: Optional[dict] = None,
) -> Dict[str, Any]:
    return {
        "artifact": "AXM Foundry Exit Intake v0",
        "claim": "A liberated Foundry record: sealed through genesis, survives Palantir, GhostBox, and the importer.",
        "bundle_shard_id": sealed.shard_id,
        "signature_context": {
            "suite": sealed.suite,
            "merkle_root": sealed.merkle_root,
            "sealed_at": sealed.sealed_at,
        },
        "trusted_key_source": {"source": sealed.trusted_key_path, "kind": "out-of-band"},
        "verification": {
            "status": verify_status.value,
            "verifier": "axm-verify shard <dir> --trusted-key <oob_pub> (real genesis kernel)",
        },
        "datasets_exported": [
            {"dataset_rid": d.dataset_rid, "objects": len(d.objects),
             "checksums": [o.checksum for o in d.objects]}
            for d in manifest.datasets
        ],
        "ontology_object_types_preserved": [o.object_type_id for o in manifest.object_types],
        "lineage_edges_preserved": [
            {"from": e.upstream_dataset_rid, "to": e.downstream_dataset_rid, "transform": e.transform_ref}
            for e in manifest.lineage
        ],
        "external_ids_verbatim": sorted(manifest.external_ids()),
        "boundary_notes": BOUNDARY_NOTES,
        "exit_test": exit_test,
    }


def render_markdown(p: Dict[str, Any]) -> str:
    lines = [
        f"# {p['artifact']} — reviewable packet",
        "",
        f"**Claim:** {p['claim']}",
        "",
        f"- bundle shard id: `{p['bundle_shard_id']}`",
        f"- suite: `{p['signature_context']['suite']}` · merkle_root: `{p['signature_context']['merkle_root']}`",
        f"- trusted key: `{p['trusted_key_source']['source']}` ({p['trusted_key_source']['kind']})",
        f"- verification: **{p['verification']['status']}** via `{p['verification']['verifier']}`",
        "",
        "## Datasets exported (with checksums)",
    ]
    for d in p["datasets_exported"]:
        lines.append(f"- `{d['dataset_rid']}` — {d['objects']} object(s), sha256 {d['checksums']}")
    lines += ["", "## Ontology object types preserved", f"- {p['ontology_object_types_preserved']}", "", "## Lineage edges preserved"]
    for e in p["lineage_edges_preserved"]:
        lines.append(f"- `{e['from']}` → `{e['to']}` (transform: `{e['transform']}`)")
    lines += ["", "## External ids (Palantir), carried verbatim", f"- {p['external_ids_verbatim']}", "", "## Boundary notes"]
    lines += [f"- {n}" for n in p["boundary_notes"]]
    if p.get("exit_test") is not None:
        et = p["exit_test"]
        lines += [
            "",
            "## Exit test — record survives Palantir, GhostBox, and the importer",
            f"- importer involved: **{et.get('importer_involved')}** · ghostbox involved: **{et.get('ghostbox_involved')}**",
            f"- detached verify status: **{et.get('status')}** (exit {et.get('exit_code')})",
            "- verified with only the sealed shard bytes + the out-of-band public key.",
        ]
    lines.append("")
    return "\n".join(lines)


def to_json(p: Dict[str, Any]) -> str:
    return json.dumps(p, indent=2, ensure_ascii=False) + "\n"
