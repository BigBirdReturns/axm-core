"""Read + translate a captured Palantir Foundry Ontology API v2 response set.

This module NEVER talks to Palantir. It consumes JSON that a tenant owner has
already fetched (with their own credentials, out of band) from their own
tenant's PUBLIC Ontology API v2 and saved to a local capture directory. No
Palantir code, no credentials, no network calls live here.

Wire shapes are reconciled against Palantir's PUBLISHED public docs
(https://www.palantir.com/docs/foundry/api/ontologies-v2-resources/):
  - object-types/list-object-types      -> ListObjectTypesV2Response
  - object-types/list-outgoing-link-types -> ListOutgoingLinkTypesResponseV2
  - ontology-objects/list-objects       -> ListObjectsResponseV2

Parsing is TOLERANT of unknown/extra fields (Palantir may add fields; we never
crash on them and we keep the verbatim dict), but STRICT about required fields
(a clear error names the file and the missing key). The verbatim response bytes
are carried alongside the parsed model so the seal step can preserve them
byte-for-byte.

Palantir identifiers (``rid``, ``apiName``) are EXTERNAL ids. They are carried
verbatim as data; they are never AXM custody identity. Custody identity is the
genesis-derived ``sh1_`` on the sealed shard (see ``ontology_seal.py``).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

# Capture-directory convention (OUR convention; FILE CONTENTS are Foundry's
# documented wire shapes). See ONTOLOGY_EXIT.md.
OBJECT_TYPES_FILE = "objectTypes.json"
LINK_TYPES_DIR = "linkTypes"
OBJECTS_DIR = "objects"


class OntologyCaptureError(ValueError):
    """A capture file is missing a required field or is malformed.

    The message always names the offending file and the missing/invalid key so
    a tenant owner can fix the capture, not guess.
    """


# ---------------------------------------------------------------------------
# Wire-shape dataclasses (frozen). Each keeps the verbatim source dict under
# ``raw`` so nothing documented-or-undocumented is silently dropped.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PropertyV2:
    """One entry of ListObjectTypesV2Response.data[].properties (keyed by name)."""

    api_name: str                 # the property map KEY
    data_type: str                # properties[name].dataType.type
    rid: Optional[str]            # external Palantir id, verbatim
    description: Optional[str]
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ObjectTypeV2:
    """ListObjectTypesV2Response.data[] — an ontology object type."""

    api_name: str
    display_name: Optional[str]
    description: Optional[str]
    status: Optional[str]
    primary_key: Optional[str]
    rid: Optional[str]            # external Palantir id, verbatim
    properties: Tuple[PropertyV2, ...] = ()
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LinkTypeV2:
    """ListOutgoingLinkTypesResponseV2.data[] — an outgoing link.

    ``object_type_api_name`` is the link's TARGET type (per Palantir's docs the
    field names the object type the link points AT). The SOURCE type is the
    file the link was captured under (linkTypes/<sourceApiName>.json).
    """

    api_name: str
    object_type_api_name: str     # the TARGET object type
    cardinality: Optional[str]    # "ONE" | "MANY"
    foreign_key_property_api_name: Optional[str]
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ObjectInstancePage:
    """ListObjectsResponseV2 for one object type (possibly page-merged)."""

    object_type_api_name: str
    rows: Tuple[Mapping[str, Any], ...]   # verbatim __rid/__primaryKey/__apiName rows
    total_count: Optional[str]            # declared totalCount (string per docs), if any


@dataclass(frozen=True)
class CaptureFile:
    """A verbatim capture file: its raw bytes and a flat sealed name.

    ``sealed_name`` is the byte-for-byte file's name inside the sealed shard's
    ``content/`` (the genesis compiler seals only top-level content files, so
    the capture's ``linkTypes/`` and ``objects/`` subpaths are flattened; the
    BYTES are identical). ``rel_path`` is the file's path in the capture dir.
    """

    rel_path: str                 # e.g. "linkTypes/Flight.json"
    sealed_name: str              # e.g. "linkTypes__Flight.json"
    raw_bytes: bytes


@dataclass(frozen=True)
class OntologyCapture:
    object_types: Tuple[ObjectTypeV2, ...]
    links_by_source: Mapping[str, Tuple[LinkTypeV2, ...]]   # source apiName -> outgoing links
    instances_by_type: Mapping[str, ObjectInstancePage]     # object type apiName -> page
    files: Tuple[CaptureFile, ...]                          # verbatim bytes, one per capture file

    def external_ids(self) -> Tuple[str, ...]:
        """Every Palantir identifier (rid / apiName) carried by this capture.

        These are EXTERNAL ids. None of them is ever AXM custody identity.
        """
        ids: List[str] = []
        for ot in self.object_types:
            ids.append(ot.api_name)
            if ot.rid:
                ids.append(ot.rid)
            for p in ot.properties:
                if p.rid:
                    ids.append(p.rid)
        for links in self.links_by_source.values():
            for lk in links:
                ids.append(lk.api_name)
        return tuple(dict.fromkeys(ids))  # de-dup, order-stable


# ---------------------------------------------------------------------------
# Tolerant/strict JSON loading
# ---------------------------------------------------------------------------


def _require(d: Mapping[str, Any], key: str, where: str) -> Any:
    if not isinstance(d, Mapping) or key not in d:
        raise OntologyCaptureError(f"{where}: missing required key {key!r}")
    return d[key]


def _load_pages(raw_bytes: bytes, where: str) -> List[Mapping[str, Any]]:
    """Decode a capture file into a list of response-page objects.

    A single response object -> [obj]. A JSON ARRAY of response objects ->
    treated as concatenated pages (the multi-page convention). Anything else is
    an error naming the file.
    """
    try:
        doc = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise OntologyCaptureError(f"{where}: not valid UTF-8 JSON: {e}") from e
    if isinstance(doc, list):
        for i, page in enumerate(doc):
            if not isinstance(page, Mapping):
                raise OntologyCaptureError(f"{where}: page [{i}] is not a JSON object")
        return list(doc)
    if isinstance(doc, Mapping):
        return [doc]
    raise OntologyCaptureError(f"{where}: top-level JSON must be an object or array of objects")


def _merged_data(pages: List[Mapping[str, Any]], where: str) -> List[Mapping[str, Any]]:
    """Concatenate the ``data`` arrays across pages (strict: data must be a list)."""
    out: List[Mapping[str, Any]] = []
    for i, page in enumerate(pages):
        data = _require(page, "data", f"{where} (page {i})")
        if not isinstance(data, list):
            raise OntologyCaptureError(f"{where} (page {i}): 'data' must be a JSON array")
        out.extend(data)
    return out


def _last_total_count(pages: List[Mapping[str, Any]]) -> Optional[str]:
    """The declared totalCount from the pages, if any. Kept verbatim as a string."""
    total: Optional[str] = None
    for page in pages:
        if "totalCount" in page and page["totalCount"] is not None:
            total = str(page["totalCount"])
    return total


def _parse_object_type(d: Mapping[str, Any], where: str) -> ObjectTypeV2:
    api_name = str(_require(d, "apiName", where))
    # Tolerant: a type with no properties may omit the key entirely rather than
    # sending an empty map — treat absent as {}.
    props_map = d.get("properties") or {}
    if not isinstance(props_map, Mapping):
        raise OntologyCaptureError(f"{where} apiName={api_name!r}: 'properties' must be an object")
    props: List[PropertyV2] = []
    for prop_name, pv in props_map.items():
        pwhere = f"{where} apiName={api_name!r} property={prop_name!r}"
        if not isinstance(pv, Mapping):
            raise OntologyCaptureError(f"{pwhere}: property value must be an object")
        data_type_obj = _require(pv, "dataType", pwhere)
        if not isinstance(data_type_obj, Mapping):
            raise OntologyCaptureError(f"{pwhere}: 'dataType' must be an object")
        data_type = str(_require(data_type_obj, "type", f"{pwhere} dataType"))
        props.append(
            PropertyV2(
                api_name=str(prop_name),
                data_type=data_type,
                rid=(str(pv["rid"]) if pv.get("rid") is not None else None),
                description=(str(pv["description"]) if pv.get("description") is not None else None),
                raw=dict(pv),
            )
        )
    return ObjectTypeV2(
        api_name=api_name,
        display_name=(str(d["displayName"]) if d.get("displayName") is not None else None),
        description=(str(d["description"]) if d.get("description") is not None else None),
        status=(str(d["status"]) if d.get("status") is not None else None),
        primary_key=(str(d["primaryKey"]) if d.get("primaryKey") is not None else None),
        rid=(str(d["rid"]) if d.get("rid") is not None else None),
        properties=tuple(props),
        raw=dict(d),
    )


def _parse_link_type(d: Mapping[str, Any], where: str) -> LinkTypeV2:
    api_name = str(_require(d, "apiName", where))
    target = str(_require(d, "objectTypeApiName", f"{where} apiName={api_name!r}"))
    return LinkTypeV2(
        api_name=api_name,
        object_type_api_name=target,
        cardinality=(str(d["cardinality"]) if d.get("cardinality") is not None else None),
        foreign_key_property_api_name=(
            str(d["foreignKeyPropertyApiName"])
            if d.get("foreignKeyPropertyApiName") is not None
            else None
        ),
        raw=dict(d),
    )


def load_ontology_capture(capture_dir: str | Path) -> OntologyCapture:
    """Load a capture directory into an ``OntologyCapture``.

    Layout (OUR convention; file CONTENTS are Foundry's documented wire shapes):

        capture_dir/
          objectTypes.json              REQUIRED  ListObjectTypesV2Response
          linkTypes/<apiName>.json      OPTIONAL  ListOutgoingLinkTypesResponseV2
          objects/<apiName>.json        OPTIONAL  ListObjectsResponseV2

    Raises ``OntologyCaptureError`` (naming the file and key) on a missing
    required field. Unknown extra fields are tolerated and preserved verbatim.
    """
    capture_dir = Path(capture_dir)
    if not capture_dir.is_dir():
        raise OntologyCaptureError(f"capture_dir is not a directory: {capture_dir}")

    files: List[CaptureFile] = []

    # --- objectTypes.json (REQUIRED) ---
    ot_path = capture_dir / OBJECT_TYPES_FILE
    if not ot_path.is_file():
        raise OntologyCaptureError(
            f"{capture_dir}: required file {OBJECT_TYPES_FILE!r} not found "
            f"(a ListObjectTypesV2Response capture)"
        )
    ot_bytes = ot_path.read_bytes()
    files.append(CaptureFile(rel_path=OBJECT_TYPES_FILE, sealed_name=OBJECT_TYPES_FILE, raw_bytes=ot_bytes))
    ot_pages = _load_pages(ot_bytes, OBJECT_TYPES_FILE)
    object_types = tuple(
        _parse_object_type(d, OBJECT_TYPES_FILE) for d in _merged_data(ot_pages, OBJECT_TYPES_FILE)
    )
    seen_types: set = set()
    for ot in object_types:
        if ot.api_name in seen_types:
            # Refuse here with a clear name rather than letting the genesis
            # compiler abort later on a duplicate claim_id with an opaque
            # subprocess error (e.g. the same page captured twice).
            raise OntologyCaptureError(
                f"{OBJECT_TYPES_FILE}: duplicate object type apiName={ot.api_name!r} "
                f"(same page captured twice, or overlapping pages merged?)"
            )
        seen_types.add(ot.api_name)
    known_types = {ot.api_name for ot in object_types}

    # --- linkTypes/<apiName>.json (OPTIONAL, one per source object type) ---
    links_by_source: Dict[str, Tuple[LinkTypeV2, ...]] = {}
    link_dir = capture_dir / LINK_TYPES_DIR
    if link_dir.is_dir():
        for lp in sorted(link_dir.glob("*.json")):
            source_api_name = lp.stem
            rel = f"{LINK_TYPES_DIR}/{lp.name}"
            raw = lp.read_bytes()
            files.append(
                CaptureFile(rel_path=rel, sealed_name=f"{LINK_TYPES_DIR}__{lp.name}", raw_bytes=raw)
            )
            pages = _load_pages(raw, rel)
            links = tuple(_parse_link_type(d, rel) for d in _merged_data(pages, rel))
            links_by_source[source_api_name] = links

    # --- objects/<apiName>.json (OPTIONAL) ---
    instances_by_type: Dict[str, ObjectInstancePage] = {}
    obj_dir = capture_dir / OBJECTS_DIR
    if obj_dir.is_dir():
        for op in sorted(obj_dir.glob("*.json")):
            type_api_name = op.stem
            rel = f"{OBJECTS_DIR}/{op.name}"
            raw = op.read_bytes()
            files.append(
                CaptureFile(rel_path=rel, sealed_name=f"{OBJECTS_DIR}__{op.name}", raw_bytes=raw)
            )
            pages = _load_pages(raw, rel)
            rows = tuple(_merged_data(pages, rel))
            instances_by_type[type_api_name] = ObjectInstancePage(
                object_type_api_name=type_api_name,
                rows=rows,
                total_count=_last_total_count(pages),
            )

    return OntologyCapture(
        object_types=object_types,
        links_by_source=links_by_source,
        instances_by_type=instances_by_type,
        files=tuple(files),
    )


# ---------------------------------------------------------------------------
# Translation to the existing exit ontology.json shape (a SUPERSET of it)
# ---------------------------------------------------------------------------


def to_exit_ontology(capture: OntologyCapture) -> Dict[str, Any]:
    """Translate a capture into a SUPERSET of the ontology.json shape that
    ``FoundryExitImporter._object_type`` consumes.

    Per object type it emits the importer's fields:
      - ``object_type_id``          = apiName
      - ``properties``              = [{"id": propName, "type": dataType.type}, ...]
      - ``links``                   = [{"id", "target_object_type_id",
                                        "cardinality", "foreign_key"}, ...]
                                      built from the OUTGOING link types captured
                                      under this object type
      - ``backing_dataset_rids``    = []   # NOT available from the v2 LIST
      - ``action_refs``             = []   #   endpoints — see below
      - ``security_markings``       = []   #

    and SUPERSET fields the importer ignores but reviewers can use:
      - ``display_name``, ``description``, ``status``, ``primary_key``
      - ``external_ids`` = {"rid": <object-type rid>, "property_rids": {...}}

    HONEST BOUNDARY: ``backing_dataset_rids``, ``action_refs``, and
    ``security_markings`` are EMPTY in v0. The Ontology API v2 LIST endpoints
    used for the capture (list-object-types / list-outgoing-link-types /
    list-objects) do not carry dataset backing, action types, or security
    markings, so we do not invent them. A later capture of the actions /
    interfaces surfaces could populate them.
    """
    object_types: List[Dict[str, Any]] = []
    for ot in capture.object_types:
        links = []
        for lk in capture.links_by_source.get(ot.api_name, ()):  # outgoing links FROM this type
            links.append(
                {
                    "id": lk.api_name,
                    "target_object_type_id": lk.object_type_api_name,
                    "cardinality": lk.cardinality,
                    "foreign_key": lk.foreign_key_property_api_name,
                }
            )
        object_types.append(
            {
                "object_type_id": ot.api_name,
                "properties": [{"id": p.api_name, "type": p.data_type} for p in ot.properties],
                "links": links,
                "backing_dataset_rids": [],   # not carried by the v2 list endpoints
                "action_refs": [],            # not carried by the v2 list endpoints
                "security_markings": [],      # not carried by the v2 list endpoints
                # superset (ignored by the importer, kept for reviewers):
                "display_name": ot.display_name,
                "description": ot.description,
                "status": ot.status,
                "primary_key": ot.primary_key,
                "external_ids": {
                    "rid": ot.rid,   # external Palantir id, verbatim; never custody
                    "property_rids": {p.api_name: p.rid for p in ot.properties if p.rid},
                },
            }
        )
    return {"object_types": object_types}
