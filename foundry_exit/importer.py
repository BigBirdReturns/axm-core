"""Read-only Foundry exit importer.

Dataset BYTES come from an ``ExportSource`` (S3-compatible or filesystem).
Ontology and lineage come from EXPLICIT metadata inputs (JSON), never from S3.
The importer pulls each dataset object, checksums it against the inventory,
optionally stages it locally, and assembles a ``FoundryExitManifest``. It never
writes to any source.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional

from .adapters import ExportSource
from .planes import (
    DatasetExport,
    DatasetObject,
    FoundryExitManifest,
    LineageEdge,
    OntologyObjectType,
)


class ChecksumMismatch(RuntimeError):
    """Fetched dataset bytes did not match the inventory checksum."""


def load_json(path: str | Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


class FoundryExitImporter:
    def __init__(self, source: ExportSource, *, stage_dir: Optional[str | Path] = None) -> None:
        self._source = source
        self._stage_dir = Path(stage_dir) if stage_dir else None

    def import_export(self, *, inventory: Dict[str, Any], ontology: Dict[str, Any], lineage: Dict[str, Any]) -> FoundryExitManifest:
        datasets = tuple(self._import_dataset(d) for d in inventory.get("datasets", []))
        object_types = tuple(self._object_type(o) for o in ontology.get("object_types", []))
        edges = tuple(self._edge(e) for e in lineage.get("edges", []))
        return FoundryExitManifest(
            source_system=inventory.get("source_system", "palantir-foundry"),
            datasets=datasets,
            object_types=object_types,
            lineage=edges,
            exported_at=inventory.get("exported_at"),
        )

    def _import_dataset(self, d: Dict[str, Any]) -> DatasetExport:
        objects = []
        for o in d.get("objects", []):
            raw = self._source.read_bytes(o["object_path"])  # read-only pull from the data plane
            digest = hashlib.sha256(raw).hexdigest()
            expected = o.get("checksum")
            if expected and expected != digest:
                raise ChecksumMismatch(
                    f"{o['object_path']}: inventory {expected} != fetched {digest}"
                )
            local = None
            if self._stage_dir is not None:
                dest = self._stage_dir / o["object_path"]
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(raw)  # stage to the LOCAL target; never writes to the source
                local = str(dest)
            objects.append(
                DatasetObject(
                    object_path=o["object_path"],
                    file_format=o.get("file_format", ""),
                    size_bytes=len(raw),
                    checksum=digest,
                    schema=o.get("schema"),
                    exported_local_path=local,
                )
            )
        return DatasetExport(
            dataset_rid=d["dataset_rid"],
            objects=tuple(objects),
            branch=d.get("branch"),
            version=d.get("version"),
        )

    @staticmethod
    def _object_type(o: Dict[str, Any]) -> OntologyObjectType:
        return OntologyObjectType(
            object_type_id=o["object_type_id"],
            properties=tuple(o.get("properties", [])),
            links=tuple(o.get("links", [])),
            backing_dataset_rids=tuple(o.get("backing_dataset_rids", [])),
            action_refs=tuple(o.get("action_refs", [])),
            security_markings=tuple(o.get("security_markings", [])),
        )

    @staticmethod
    def _edge(e: Dict[str, Any]) -> LineageEdge:
        return LineageEdge(
            upstream_dataset_rid=e["upstream_dataset_rid"],
            downstream_dataset_rid=e["downstream_dataset_rid"],
            transform_ref=e.get("transform_ref"),
            produces_object_type_id=e.get("produces_object_type_id"),
        )
