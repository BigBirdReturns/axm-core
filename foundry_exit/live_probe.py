"""Live Foundry S3-compatible extraction probe.

Reuses Foundry Exit Intake v0 UNCHANGED (no redesign, no new bundle model): it
drives the existing ``S3ExportSource`` + importer + seal against a live,
authorized S3-compatible export surface, building the dataset inventory from a
listing instead of a fixture file.

AUTHORIZED USE ONLY. ``main()`` refuses unless an explicit Foundry S3 endpoint +
bucket are configured via the environment; it never invents an endpoint and
never reaches for arbitrary S3 credentials. Ontology and lineage come from
explicit metadata inputs only -- S3 does not supply the ontology.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from foundry_exit.adapters import ExportSource, S3Config, S3ExportSource
from foundry_exit.bundle import build_bundle
from foundry_exit.exit_test import verify_detached
from foundry_exit.importer import FoundryExitImporter
from foundry_exit.seal import kernel_available, seal_exit_bundle, verify_exit_bundle

_FORMAT_BY_EXT = {
    ".csv": "csv", ".parquet": "parquet", ".json": "json", ".jsonl": "jsonl",
    ".txt": "txt", ".avro": "avro", ".orc": "orc",
}


def _fmt(path: str) -> str:
    return _FORMAT_BY_EXT.get(Path(path).suffix.lower(), "")


def build_inventory_from_listing(
    source: ExportSource,
    *,
    dataset_rid: str,
    prefix: str = "",
    source_system: str = "palantir-foundry",
    branch: Optional[str] = None,
    version: Optional[str] = None,
) -> Dict[str, Any]:
    """List a prefix and build a v0-shaped inventory. No checksums yet -- the
    importer computes them on fetch and records them."""
    objects = [{"object_path": p, "file_format": _fmt(p)} for p in source.list_objects(prefix)]
    return {
        "source_system": source_system,
        "datasets": [
            {"dataset_rid": dataset_rid, "branch": branch, "version": version, "objects": objects}
        ],
    }


def run_live_probe(
    source: ExportSource,
    *,
    dataset_rid: str,
    prefix: str = "",
    endpoint_class: str,
    ontology: Optional[Dict[str, Any]] = None,
    lineage: Optional[Dict[str, Any]] = None,
    out_dir: str | Path,
    stage_dir: Optional[str | Path] = None,
) -> Dict[str, Any]:
    """List -> fetch -> checksum -> v0 dataset manifest -> seal -> verify ->
    detached, all through the existing v0 pipeline. Returns the probe ledger."""
    inventory = build_inventory_from_listing(source, dataset_rid=dataset_rid, prefix=prefix)
    importer = FoundryExitImporter(source, stage_dir=stage_dir)
    manifest = importer.import_export(
        inventory=inventory,
        ontology=ontology or {"object_types": []},
        lineage=lineage or {"edges": []},
    )

    objs = [o for d in manifest.datasets for o in d.objects]
    gaps: List[str] = []
    if ontology is None:
        gaps.append("no ontology metadata input supplied (S3 does not supply ontology; attach explicitly)")
    if lineage is None:
        gaps.append("no lineage metadata input supplied (attach explicitly from the metadata plane)")

    ledger: Dict[str, Any] = {
        "endpoint_class": endpoint_class,
        "dataset_scope": {"dataset_rid": dataset_rid, "prefix": prefix},
        "object_count": len(objs),
        "total_bytes": sum(o.size_bytes for o in objs),
        "checksum_manifest": [
            {"object_path": o.object_path, "checksum": o.checksum, "size_bytes": o.size_bytes}
            for o in objs
        ],
        "ontology_object_types": [t.object_type_id for t in manifest.object_types],
        "lineage_edges": len(manifest.lineage),
        "sealed": False,
        "shard_id": None,
        "verification": None,
        "detached": None,
        "gaps": gaps,
    }

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if kernel_available():
        bundle_dir = build_bundle(manifest, out_dir / "bundle")
        sealed = seal_exit_bundle(manifest, bundle_dir, out_dir / "shard")
        status = verify_exit_bundle(sealed.shard_dir, sealed.trusted_key_path)
        detached = verify_detached(sealed.shard_dir, sealed.trusted_key_path)
        ledger.update(
            sealed=True,
            shard_id=sealed.shard_id,
            verification=status.value,
            detached={
                "status": detached["status"],
                "importer_involved": detached["importer_involved"],
                "ghostbox_involved": detached["ghostbox_involved"],
                "palantir_involved": detached["palantir_involved"],
            },
        )
    else:
        gaps.append("genesis kernel not on PATH: bundle listed/checksummed but not sealed")
    return ledger


def _load_optional(path: Optional[str]) -> Optional[Dict[str, Any]]:
    if not path:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv=None) -> int:
    """Authorized live entrypoint. Refuses unless a Foundry S3 surface is
    explicitly configured. It never invents an endpoint and never uses arbitrary
    (non-Foundry) S3 credentials."""
    endpoint = os.environ.get("FOUNDRY_S3_ENDPOINT")
    bucket = os.environ.get("FOUNDRY_S3_BUCKET")
    dataset_rid = os.environ.get("FOUNDRY_DATASET_RID")
    if not (endpoint and bucket and dataset_rid):
        print("REFUSED: no authorized Foundry S3 surface configured.")
        print("  Required (authorized, out of band): FOUNDRY_S3_ENDPOINT, FOUNDRY_S3_BUCKET,")
        print("  FOUNDRY_DATASET_RID, and AXM_S3_ACCESS_KEY / AXM_S3_SECRET_KEY.")
        print("  Optional: FOUNDRY_S3_PREFIX, FOUNDRY_S3_REGION, FOUNDRY_ONTOLOGY_JSON, FOUNDRY_LINEAGE_JSON.")
        print("  This probe never invents an endpoint and never uses arbitrary S3 credentials.")
        return 2

    # Scope via the LIST prefix, not S3Config.prefix: v0's list_objects returns
    # fully-qualified keys, and read_bytes prepends S3Config.prefix, so keys only
    # round-trip when the config prefix is empty. (Recorded gap, not a redesign.)
    cfg = S3Config(
        endpoint_url=endpoint,
        bucket=bucket,
        prefix="",
        region=os.environ.get("FOUNDRY_S3_REGION"),
    )
    ledger = run_live_probe(
        S3ExportSource(cfg),
        dataset_rid=dataset_rid,
        prefix=os.environ.get("FOUNDRY_S3_PREFIX", ""),
        endpoint_class="authorized-foundry-s3-compatible",
        ontology=_load_optional(os.environ.get("FOUNDRY_ONTOLOGY_JSON")),
        lineage=_load_optional(os.environ.get("FOUNDRY_LINEAGE_JSON")),
        out_dir=os.environ.get("FOUNDRY_OUT", "foundry_live_out"),
        stage_dir=os.environ.get("FOUNDRY_STAGE", "foundry_live_staged"),
    )
    print(json.dumps(ledger, indent=2))
    ok = ledger.get("verification") == "pass" and (ledger.get("detached") or {}).get("status") == "PASS"
    return 0 if ok else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
