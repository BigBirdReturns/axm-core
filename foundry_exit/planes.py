"""Foundry exit object model — three source planes + the AXM custody plane.

Palantir identifiers (dataset RIDs, ontology object-type IDs, transform refs) are
EXTERNAL ids, carried verbatim. They are never AXM custody identity; the custody
id is the genesis-derived ``sh1_`` on the sealed bundle.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# 1. Data plane — dataset bytes from the S3-compatible storage plane
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DatasetObject:
    object_path: str                      # path/prefix within the dataset
    file_format: str                      # csv | parquet | ...
    size_bytes: int
    checksum: str                         # sha256 hex of the object bytes
    schema: Optional[Dict[str, Any]] = None
    exported_local_path: Optional[str] = None  # staged local / target object-store path


@dataclass(frozen=True)
class DatasetExport:
    dataset_rid: str                      # external Palantir id, verbatim
    objects: Tuple[DatasetObject, ...] = ()
    branch: Optional[str] = None
    version: Optional[str] = None


# ---------------------------------------------------------------------------
# 2. Ontology plane — metadata plane
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OntologyObjectType:
    object_type_id: str                   # external Palantir id, verbatim
    properties: Tuple[Dict[str, Any], ...] = ()
    links: Tuple[Dict[str, Any], ...] = ()
    backing_dataset_rids: Tuple[str, ...] = ()
    action_refs: Tuple[str, ...] = ()
    # recorded for provenance only. NOT portable permissions — importing an exit
    # bundle never re-creates Palantir's access control.
    security_markings: Tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# 3. Lineage plane — dataset/transform dependency graph
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LineageEdge:
    upstream_dataset_rid: str
    downstream_dataset_rid: str
    transform_ref: Optional[str] = None
    produces_object_type_id: Optional[str] = None


# ---------------------------------------------------------------------------
# The assembled exit manifest — AXM's representation of the Foundry export
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FoundryExitManifest:
    source_system: str
    datasets: Tuple[DatasetExport, ...]
    object_types: Tuple[OntologyObjectType, ...]
    lineage: Tuple[LineageEdge, ...]
    exported_at: Optional[str] = None

    def external_ids(self) -> Set[str]:
        """Every Palantir identifier carried by this manifest (external ids)."""
        ids: Set[str] = set()
        for d in self.datasets:
            ids.add(d.dataset_rid)
        for o in self.object_types:
            ids.add(o.object_type_id)
            ids.update(o.backing_dataset_rids)
        for e in self.lineage:
            ids.update((e.upstream_dataset_rid, e.downstream_dataset_rid))
            if e.transform_ref:
                ids.add(e.transform_ref)
            if e.produces_object_type_id:
                ids.add(e.produces_object_type_id)
        return ids

    def dataset_object_count(self) -> int:
        return sum(len(d.objects) for d in self.datasets)
