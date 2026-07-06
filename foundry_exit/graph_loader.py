"""Downstream graph loader over an ALREADY-SEALED Foundry Exit bundle.

This is strictly downstream of Foundry Exit Intake v0 and the S3 probe: it does
NOT import, does NOT re-fetch from S3, does NOT call Palantir. It reads only the
sealed shard, and only AFTER genesis verifies it with an out-of-band trusted key.

Flow:
    sealed shard  --(genesis verify, out-of-band key)-->  verified?
        yes -> read content/ontology.json + lineage.json + datasets.manifest.jsonl
            -> project a graph (nodes/edges) preserving Palantir ids as EXTERNAL
               ids only (never as custody identity)
            -> foundry_exit_graph.json (+ optional OpenCypher, + optional FalkorDB)
        no  -> REFUSE (LoadRefused). Verification precedes any read of the graph.

Boundaries:
  - shard_id (genesis-derived sh1_) is the ONLY custody identity. Palantir dataset
    RIDs / ontology object ids / relationship ids / transform refs are external
    ids, carried verbatim; they never become AXM custody ids.
  - Security markings / permissions are metadata only -- NO permission portability
    is implied or created.
  - No ghostbox import. No Palantir/S3 call. Reads only the sealed bundle.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .exit_test import verify_detached
from .seal import VerifyStatus

# Bundle content files the loader reads out of the sealed shard's content/.
_ONTOLOGY = "ontology.json"
_LINEAGE = "lineage.json"
_DATASETS = "datasets.manifest.jsonl"
_MANIFEST = "foundry_exit_manifest.json"

# Node kinds -> OpenCypher labels.
_LABELS = {
    "dataset": "Dataset",
    "object_type": "ObjectType",
    "property": "Property",
    "relationship": "Relationship",
    "transform": "Transform",
    "file": "File",
}

BOUNDARY_NOTES = [
    "Built ONLY from a genesis-verified sealed bundle; verification precedes any graph read.",
    "Palantir dataset RIDs, ontology object ids, relationship ids, and transform refs are "
    "EXTERNAL ids carried verbatim; the custody id is the genesis sh1_ on the sealed bundle.",
    "Security markings are metadata only -- no Palantir permission was made portable.",
    "No Palantir call, no S3 call, no ghostbox import: the loader reads only the sealed bundle.",
]


class LoadRefused(RuntimeError):
    """The loader refused to build the graph. ``status`` is the verify verdict."""

    def __init__(self, message: str, status: VerifyStatus) -> None:
        super().__init__(message)
        self.status = status


# --- verification gate -------------------------------------------------------


def _status_from_exit(code: int) -> VerifyStatus:
    if code == 0:
        return VerifyStatus.PASS
    if code == 2:
        return VerifyStatus.MALFORMED
    return VerifyStatus.FAIL


def _verify_or_refuse(shard_dir: Path, trusted_key: Optional[str | Path]) -> Dict[str, Any]:
    """Genesis-verify with an out-of-band key BEFORE any content is read.

    No key -> NO_TRUSTED_KEY (decided before invoking the CLI). Any non-PASS
    verdict -> refuse. Returns the detached receipt on PASS.
    """
    if not trusted_key:
        raise LoadRefused(
            "no out-of-band trusted key supplied; refusing to read the bundle",
            VerifyStatus.NO_TRUSTED_KEY,
        )
    receipt = verify_detached(shard_dir, trusted_key)
    status = _status_from_exit(int(receipt.get("exit_code", 1)))
    if status is not VerifyStatus.PASS:
        raise LoadRefused(
            f"sealed bundle did not verify (status={status.value}); refusing to load",
            status,
        )
    return receipt


# --- read the sealed content -------------------------------------------------


def _content_dir(shard_dir: Path) -> Path:
    return shard_dir / "content"


def _load_json(path: Path) -> Any:
    if not path.exists():
        raise LoadRefused(f"sealed bundle missing {path.name}", VerifyStatus.MALFORMED)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise LoadRefused(f"sealed bundle {path.name} is not valid JSON: {exc}", VerifyStatus.MALFORMED)


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        raise LoadRefused(f"sealed bundle missing {path.name}", VerifyStatus.MALFORMED)
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


# --- graph projection (pure, deterministic) ----------------------------------


def _prop_id(p: Dict[str, Any]) -> Optional[str]:
    for k in ("id", "property_id", "apiName", "api_name", "name"):
        if p.get(k):
            return str(p[k])
    return None


def _link_id(l: Dict[str, Any]) -> Optional[str]:
    for k in ("id", "link_type_id", "apiName", "api_name", "name"):
        if l.get(k):
            return str(l[k])
    return None


def _link_target(l: Dict[str, Any]) -> Optional[str]:
    for k in ("target_object_type_id", "object_type_id", "target"):
        if l.get(k):
            return str(l[k])
    return None


def build_graph_projection(
    *,
    ontology: Dict[str, Any],
    lineage: Dict[str, Any],
    datasets: List[Dict[str, Any]],
    source_system: Optional[str] = None,
) -> Dict[str, Any]:
    """Project the verified ontology + lineage + dataset manifest into a
    deterministic nodes/edges graph. Pure: no verification, no I/O.

    Every node carries ``external_id`` verbatim (the Palantir id) and never a
    fabricated custody id. Nodes and edges are sorted for byte-stable output.
    """
    nodes: Dict[str, Dict[str, Any]] = {}
    edges: List[Dict[str, Any]] = []

    def add_node(node_id: str, kind: str, external_id: Optional[str], **attrs: Any) -> None:
        if node_id not in nodes:
            nodes[node_id] = {
                "id": node_id,
                "kind": kind,
                "external_id": external_id,  # verbatim Palantir/Foundry id (or None)
                "attributes": {k: v for k, v in attrs.items() if v not in (None, (), [], {})},
            }

    def add_edge(src: str, rel: str, dst: str, **attrs: Any) -> None:
        edge = {"src": src, "rel": rel, "dst": dst}
        extra = {k: v for k, v in attrs.items() if v not in (None, (), [], {})}
        if extra:
            edge["attributes"] = extra
        edges.append(edge)

    # datasets + their exported files (from the sealed dataset manifest)
    for row in datasets:
        rid = row["dataset_rid"]
        add_node(rid, "dataset", rid, branch=row.get("branch"), version=row.get("version"))
        obj_path = row.get("object_path")
        if obj_path:
            file_id = f"{rid}::file::{obj_path}"
            add_node(
                file_id, "file", obj_path,
                dataset_rid=rid, file_format=row.get("file_format"),
                size_bytes=row.get("size_bytes"), checksum=row.get("checksum"),
            )
            add_edge(rid, "has_file", file_id)

    # ontology object types, properties, relationships (links)
    for ot in ontology.get("object_types", []):
        otid = ot["object_type_id"]
        add_node(
            otid, "object_type", otid,
            # markings recorded as metadata ONLY -- not portable permissions.
            security_markings=tuple(ot.get("security_markings", ())),
            action_refs=tuple(ot.get("action_refs", ())),
        )
        for ds in ot.get("backing_dataset_rids", []):
            add_node(ds, "dataset", ds)
            add_edge(otid, "backed_by", ds)  # object-type -> backing dataset
        for p in ot.get("properties", []):
            pid = _prop_id(p)
            node_id = f"{otid}::property::{pid}" if pid else f"{otid}::property::_{len(nodes)}"
            add_node(node_id, "property", pid, object_type_id=otid,
                     data_type=p.get("data_type") or p.get("type"))
            add_edge(otid, "has_property", node_id)  # object-property link
        for l in ot.get("links", []):
            lid = _link_id(l)
            node_id = f"{otid}::relationship::{lid}" if lid else f"{otid}::relationship::_{len(nodes)}"
            add_node(node_id, "relationship", lid, object_type_id=otid,
                     cardinality=l.get("cardinality"))
            add_edge(otid, "has_relationship", node_id)  # object-relationship link
            target = _link_target(l)
            if target:
                add_node(target, "object_type", target)
                add_edge(node_id, "targets", target)

    # lineage: upstream/downstream + transforms + transform output
    for e in lineage.get("edges", []):
        up = e["upstream_dataset_rid"]
        down = e["downstream_dataset_rid"]
        add_node(up, "dataset", up)
        add_node(down, "dataset", down)
        add_edge(down, "derives_from", up)  # downstream/upstream lineage
        tref = e.get("transform_ref")
        if tref:
            add_node(tref, "transform", tref)
            add_edge(up, "input_to", tref)
            add_edge(tref, "produces", down)  # transform output -> dataset
            produced_ot = e.get("produces_object_type_id")
            if produced_ot:
                add_node(produced_ot, "object_type", produced_ot)
                add_edge(tref, "produces_object_type", produced_ot)

    sorted_nodes = sorted(nodes.values(), key=lambda n: (n["kind"], n["id"]))
    sorted_edges = sorted(edges, key=lambda e: (e["src"], e["rel"], e["dst"]))
    by_kind: Dict[str, int] = {}
    for n in sorted_nodes:
        by_kind[n["kind"]] = by_kind.get(n["kind"], 0) + 1
    return {
        "source_system": source_system,
        "nodes": sorted_nodes,
        "edges": sorted_edges,
        "stats": {
            "node_count": len(sorted_nodes),
            "edge_count": len(sorted_edges),
            "by_kind": dict(sorted(by_kind.items())),
        },
    }


# --- OpenCypher -------------------------------------------------------------


def _cy(s: Any) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def to_cypher(graph: Dict[str, Any]) -> List[str]:
    """Deterministic OpenCypher MERGE statements for the projected graph."""
    stmts: List[str] = []
    for n in graph["nodes"]:
        label = _LABELS.get(n["kind"], "Node")
        parts = [f"MERGE (x:{label} {{id: {_cy(n['id'])}}})"]
        sets = [f"x.kind = {_cy(n['kind'])}"]
        if n.get("external_id") is not None:
            sets.append(f"x.external_id = {_cy(n['external_id'])}")
        parts.append("SET " + ", ".join(sets))
        stmts.append(" ".join(parts) + ";")
    for e in graph["edges"]:
        stmts.append(
            f"MATCH (a {{id: {_cy(e['src'])}}}), (b {{id: {_cy(e['dst'])}}}) "
            f"MERGE (a)-[:{e['rel'].upper()}]->(b);"
        )
    return stmts


# --- top-level: verify, then load -------------------------------------------


@dataclass(frozen=True)
class LoadedGraph:
    graph: Dict[str, Any]
    provenance: Dict[str, Any]

    def document(self) -> Dict[str, Any]:
        return {"provenance": self.provenance, "graph": self.graph, "boundary_notes": BOUNDARY_NOTES}


def load_verified_graph(
    shard_dir: str | Path,
    trusted_key: Optional[str | Path],
) -> LoadedGraph:
    """Verify the sealed bundle with an out-of-band key, THEN project the graph.

    Raises ``LoadRefused`` if the key is missing, the bundle fails verification,
    or it is malformed -- before any ontology/lineage is read.
    """
    shard_dir = Path(shard_dir)
    receipt = _verify_or_refuse(shard_dir, trusted_key)

    content = _content_dir(shard_dir)
    ontology = _load_json(content / _ONTOLOGY)
    lineage = _load_json(content / _LINEAGE)
    datasets = _load_jsonl(content / _DATASETS)
    source_system = None
    fx_manifest = content / _MANIFEST
    if fx_manifest.exists():
        source_system = _load_json(fx_manifest).get("source_system")

    graph = build_graph_projection(
        ontology=ontology, lineage=lineage, datasets=datasets, source_system=source_system
    )

    provenance = {
        "verified": True,
        "verify_status": VerifyStatus.PASS.value,
        "detached": True,
        "detached_status": receipt.get("status"),
        "detached_exit_code": receipt.get("exit_code"),
        "source": "genesis-verified foundry exit sealed bundle",
        "importer_involved": bool(receipt.get("importer_involved")),
        "ghostbox_involved": bool(receipt.get("ghostbox_involved")),
        "palantir_involved": bool(receipt.get("palantir_involved")),
        "s3_involved": False,
    }
    return LoadedGraph(graph=graph, provenance=provenance)


def write_graph_export(loaded: LoadedGraph, out_dir: str | Path, *, cypher: bool = True) -> Dict[str, Path]:
    """Write a reviewable, byte-stable export: foundry_exit_graph.json (+ .cypher)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: Dict[str, Path] = {}

    graph_path = out / "foundry_exit_graph.json"
    graph_path.write_text(
        json.dumps(loaded.document(), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    written["graph"] = graph_path

    if cypher:
        cy_path = out / "foundry_exit_graph.cypher"
        cy_path.write_text("\n".join(to_cypher(loaded.graph)) + "\n", encoding="utf-8")
        written["cypher"] = cy_path
    return written


# --- optional FalkorDB path (skips cleanly if unavailable) -------------------


def falkordb_available() -> bool:
    try:
        import falkordb  # noqa: F401
    except Exception:
        return False
    return True


def load_into_falkordb(
    graph: Dict[str, Any],
    *,
    graph_name: str = "foundry_exit",
    host: str = "localhost",
    port: int = 6379,
):
    """Optional: load the projected graph into FalkorDB. Raises RuntimeError if
    the driver is unavailable so callers can skip cleanly. Never a hard test dep.
    """
    if not falkordb_available():
        raise RuntimeError("falkordb driver not installed; the JSON/OpenCypher export is the portable target")
    from falkordb import FalkorDB  # lazy

    db = FalkorDB(host=host, port=port)
    g = db.select_graph(graph_name)
    for stmt in to_cypher(graph):
        g.query(stmt)
    return g
