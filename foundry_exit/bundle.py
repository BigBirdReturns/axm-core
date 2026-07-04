"""Assemble the AXM Foundry exit bundle files (pre-seal).

These are the artifacts genesis then seals into one shard: the exit manifest,
the dataset object manifest, the ontology, the lineage, and an optional graph
projection for later loaders (openCypher / FalkorDB). FalkorDB is not a
dependency -- the sealed graph/lineage JSON is the real requirement.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List

from .planes import FoundryExitManifest

# The bundle files, in the sealed shard's content/.
MANIFEST = "foundry_exit_manifest.json"
DATASETS = "datasets.manifest.jsonl"
ONTOLOGY = "ontology.json"
LINEAGE = "lineage.json"
GRAPH = "graph.json"
BUNDLE_FILES = (MANIFEST, DATASETS, ONTOLOGY, LINEAGE, GRAPH)


def build_bundle(manifest: FoundryExitManifest, out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    (out / MANIFEST).write_text(_json(_manifest_dict(manifest)), encoding="utf-8")

    with (out / DATASETS).open("w", encoding="utf-8") as fh:
        for d in manifest.datasets:
            for o in d.objects:
                fh.write(
                    json.dumps(
                        {
                            "dataset_rid": d.dataset_rid,  # external id, verbatim
                            "branch": d.branch,
                            "version": d.version,
                            "object_path": o.object_path,
                            "file_format": o.file_format,
                            "size_bytes": o.size_bytes,
                            "checksum": o.checksum,
                            "schema": o.schema,
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )

    (out / ONTOLOGY).write_text(
        _json({"object_types": [asdict(o) for o in manifest.object_types]}), encoding="utf-8"
    )
    (out / LINEAGE).write_text(
        _json({"edges": [asdict(e) for e in manifest.lineage]}), encoding="utf-8"
    )
    (out / GRAPH).write_text(_json(_graph_projection(manifest)), encoding="utf-8")
    return out


def _manifest_dict(m: FoundryExitManifest) -> Dict[str, Any]:
    return {
        "source_system": m.source_system,
        "exported_at": m.exported_at,
        "dataset_rids": [d.dataset_rid for d in m.datasets],
        "object_type_ids": [o.object_type_id for o in m.object_types],
        "lineage_edge_count": len(m.lineage),
        "dataset_object_count": m.dataset_object_count(),
        "external_ids": sorted(m.external_ids()),
        "note": "Palantir ids above are EXTERNAL ids, carried verbatim; they are "
        "not AXM custody ids (custody id is the genesis sh1_ on the sealed bundle).",
    }


def _graph_projection(m: FoundryExitManifest) -> Dict[str, Any]:
    """A plain nodes/edges JSON projection for later graph loaders. No FalkorDB
    dependency -- this is just JSON."""
    nodes: List[Dict[str, Any]] = []
    for d in m.datasets:
        nodes.append({"id": d.dataset_rid, "kind": "dataset"})
    for o in m.object_types:
        nodes.append({"id": o.object_type_id, "kind": "object_type"})
    edges: List[Dict[str, Any]] = []
    for o in m.object_types:
        for ds in o.backing_dataset_rids:
            edges.append({"src": o.object_type_id, "rel": "backed_by", "dst": ds})
    for e in m.lineage:
        edges.append(
            {"src": e.downstream_dataset_rid, "rel": "derives_from", "dst": e.upstream_dataset_rid}
        )
    return {"nodes": nodes, "edges": edges}


def _json(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
