"""Read a captured Palantir Foundry pipeline/dataset response set.

Sibling of ``ontology_api.py``, for the pipeline layer (see WORKFLOW_EXIT_MAP.md
Layer 2). This module NEVER talks to Palantir. It consumes JSON a tenant owner
already fetched (their own credentials, out of band) from their own tenant's
PUBLIC platform API v2 and saved locally. No Palantir code, no credentials, no
network calls.

Wire shapes reconciled against Palantir's PUBLISHED public docs:
  - Datasets API v2    get-dataset          -> Dataset {rid, name, parentFolderRid}
                       get-dataset-schema   -> {fieldSchemaList:[{name,type}], ...}
  - Orchestration v2   get-build / list-jobs-of-build -> Build, Job {inputs, outputs, status}
                       get-schedule         -> Schedule {rid, ...}

What this exit carries (the portable, verifiable RECORD of a pipeline):
  - dataset SCHEMAS  (typed columns per dataset) — from Datasets API v2
  - the dependency DAG (dataset A feeds dataset B) — reconstructed from build
    job input/output dataset references, where the API exposes them
  - build + schedule PROVENANCE (which build produced a dataset, what triggered it)

What this exit does NOT carry — and never pretends to: the transform RUNTIME
(the ``transforms`` framework, decorators, Spark orchestration, incremental
engine). Source and schema and the DAG are recoverable; the engine is rebuilt on
the customer's own infrastructure. See WORKFLOW_EXIT_MAP.md Layer 2.

Palantir identifiers (``rid``) are EXTERNAL ids, carried verbatim as data; never
AXM custody identity. Custody is the genesis-derived ``sh1_`` (see
``pipeline_seal.py``), exactly as in the ontology exit.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

# Capture-directory convention (OURS; FILE CONTENTS are Foundry's documented
# wire shapes). See PIPELINE_EXIT.md.
DATASETS_FILE = "datasets.json"
SCHEMAS_DIR = "schemas"
BUILDS_FILE = "builds.json"
JOBS_DIR = "jobs"
SCHEDULES_FILE = "schedules.json"   # optional


class PipelineCaptureError(ValueError):
    """A capture file is missing a required field or is malformed.

    The message always names the offending file and the missing/invalid key so a
    tenant owner can fix the capture, not guess.
    """


# ---------------------------------------------------------------------------
# Wire-shape dataclasses (frozen). Each keeps the verbatim source dict under
# ``raw`` so nothing documented-or-undocumented is silently dropped.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DatasetV2:
    """Datasets API v2 get-dataset -> a dataset."""

    rid: str                      # external Palantir id, verbatim
    name: str
    parent_folder_rid: Optional[str]
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FieldV2:
    """One entry of a dataset schema's fieldSchemaList."""

    name: str
    type: str                     # verbatim type string (e.g. "STRING", "INTEGER")
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DatasetSchema:
    """Datasets API v2 get-dataset-schema for one dataset."""

    dataset_name: str
    fields: Tuple[FieldV2, ...]
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class JobV2:
    """Orchestration v2 Job — its resolved input/output dataset rids and status.

    The DAG edges come from these input->output references. Where a deployment
    does not expose resolved I/O on the job, the lists are simply empty and no
    edge is invented.
    """

    rid: Optional[str]
    status: Optional[str]         # WAITING | RUNNING | SUCCEEDED | ...
    input_dataset_rids: Tuple[str, ...]
    output_dataset_rids: Tuple[str, ...]
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BuildV2:
    """Orchestration v2 Build (with its jobs). ``name`` is the capture handle."""

    name: str                     # our readable handle (the jobs/<name>.json stem)
    rid: Optional[str]
    schedule_rid: Optional[str]
    jobs: Tuple[JobV2, ...] = ()
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScheduleV2:
    rid: str
    name: str
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CaptureFile:
    """A verbatim capture file: raw bytes and a flat sealed name (compiler seals
    only top-level content, so subpaths are flattened; BYTES are identical)."""

    rel_path: str                 # e.g. "schemas/flights_clean.json"
    sealed_name: str              # e.g. "schemas__flights_clean.json"
    raw_bytes: bytes


@dataclass(frozen=True)
class PipelineCapture:
    datasets: Tuple[DatasetV2, ...]
    schemas_by_dataset: Mapping[str, DatasetSchema]     # dataset name -> schema
    builds: Tuple[BuildV2, ...]
    schedules_by_rid: Mapping[str, ScheduleV2]
    files: Tuple[CaptureFile, ...]

    def rid_to_name(self) -> Mapping[str, str]:
        return {d.rid: d.name for d in self.datasets if d.rid}

    def resolve(self, dataset_rid: str) -> str:
        """Dataset rid -> readable name, or a short external label if unknown."""
        name = self.rid_to_name().get(dataset_rid)
        if name:
            return name
        # Unknown rid: keep a stable, obviously-external short label, never invent.
        tail = dataset_rid.rsplit(".", 1)[-1]
        return f"external:{tail}"

    def edges(self) -> List[Tuple[str, str, str]]:
        """(source_dataset, target_dataset, build_name) DAG edges, de-duplicated,
        order-stable. Reconstructed from each build's job input->output refs."""
        seen: Dict[Tuple[str, str, str], None] = {}
        for b in self.builds:
            for job in b.jobs:
                for src_rid in job.input_dataset_rids:
                    for dst_rid in job.output_dataset_rids:
                        key = (self.resolve(src_rid), self.resolve(dst_rid), b.name)
                        seen.setdefault(key, None)
        return list(seen.keys())

    def outputs_by_build(self) -> List[Tuple[str, str]]:
        """(dataset_name, build_name) — which build produced which dataset."""
        seen: Dict[Tuple[str, str], None] = {}
        for b in self.builds:
            for job in b.jobs:
                for dst_rid in job.output_dataset_rids:
                    seen.setdefault((self.resolve(dst_rid), b.name), None)
        return list(seen.keys())

    def external_ids(self) -> Tuple[str, ...]:
        ids: List[str] = []
        for d in self.datasets:
            if d.rid:
                ids.append(d.rid)
        for b in self.builds:
            if b.rid:
                ids.append(b.rid)
            if b.schedule_rid:
                ids.append(b.schedule_rid)
            for job in b.jobs:
                if job.rid:
                    ids.append(job.rid)
        for s in self.schedules_by_rid.values():
            ids.append(s.rid)
        return tuple(dict.fromkeys(ids))


# ---------------------------------------------------------------------------
# Tolerant/strict JSON loading
# ---------------------------------------------------------------------------


def _require(d: Mapping[str, Any], key: str, where: str) -> Any:
    if not isinstance(d, Mapping) or key not in d:
        raise PipelineCaptureError(f"{where}: missing required key {key!r}")
    return d[key]


def _load_json(raw_bytes: bytes, where: str) -> Any:
    try:
        return json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise PipelineCaptureError(f"{where}: not valid UTF-8 JSON: {e}") from e


def _data_list(doc: Any, where: str) -> List[Mapping[str, Any]]:
    """Accept either a bare JSON array or a ``{"data": [...]}`` wrapper (both are
    conventions Palantir list endpoints use). Strict: the result must be a list
    of objects."""
    if isinstance(doc, Mapping) and "data" in doc:
        doc = doc["data"]
    if not isinstance(doc, list):
        raise PipelineCaptureError(f"{where}: expected a JSON array (or {{'data': [...]}})")
    for i, item in enumerate(doc):
        if not isinstance(item, Mapping):
            raise PipelineCaptureError(f"{where}: item [{i}] is not a JSON object")
    return doc


def _dataset_refs(value: Any) -> List[str]:
    """Pull dataset rids out of a job input/output list, tolerantly.

    Accepts: a list of bare rid strings, a list of ``{"datasetRid": "..."}`` or
    ``{"rid": "..."}`` objects, or a missing/None value (-> empty)."""
    if not value:
        return []
    out: List[str] = []
    for item in value:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, Mapping):
            rid = item.get("datasetRid") or item.get("rid")
            if rid:
                out.append(str(rid))
    return out


def _parse_dataset(d: Mapping[str, Any], where: str) -> DatasetV2:
    rid = str(_require(d, "rid", where))
    name = str(_require(d, "name", f"{where} rid={rid!r}"))
    return DatasetV2(
        rid=rid,
        name=name,
        parent_folder_rid=(str(d["parentFolderRid"]) if d.get("parentFolderRid") is not None else None),
        raw=dict(d),
    )


def _parse_schema(doc: Any, dataset_name: str, where: str) -> DatasetSchema:
    if not isinstance(doc, Mapping):
        raise PipelineCaptureError(f"{where}: schema top-level must be a JSON object")
    # get-dataset-schema may nest the schema under "schema"; unwrap if so.
    schema_obj = doc.get("schema") if isinstance(doc.get("schema"), Mapping) else doc
    field_list = schema_obj.get("fieldSchemaList")
    if field_list is None:
        field_list = schema_obj.get("fields")   # tolerant alias
    if not isinstance(field_list, list):
        raise PipelineCaptureError(
            f"{where}: missing required 'fieldSchemaList' (or 'fields') array"
        )
    fields: List[FieldV2] = []
    for i, f in enumerate(field_list):
        fwhere = f"{where} field[{i}]"
        if not isinstance(f, Mapping):
            raise PipelineCaptureError(f"{fwhere}: field must be a JSON object")
        fname = str(_require(f, "name", fwhere))
        ftype = str(_require(f, "type", f"{fwhere} name={fname!r}"))
        fields.append(FieldV2(name=fname, type=ftype, raw=dict(f)))
    return DatasetSchema(dataset_name=dataset_name, fields=tuple(fields), raw=dict(doc))


def _parse_job(d: Mapping[str, Any], where: str) -> JobV2:
    return JobV2(
        rid=(str(d["rid"]) if d.get("rid") is not None else None),
        status=(str(d["jobStatus"]) if d.get("jobStatus") is not None
                else (str(d["status"]) if d.get("status") is not None else None)),
        input_dataset_rids=tuple(_dataset_refs(d.get("inputs") or d.get("inputDatasets"))),
        output_dataset_rids=tuple(_dataset_refs(d.get("outputs") or d.get("outputDatasets"))),
        raw=dict(d),
    )


def load_pipeline_capture(capture_dir: str | Path) -> PipelineCapture:
    """Load a capture directory into a ``PipelineCapture``.

    Required: ``datasets.json``. Optional: ``schemas/<name>.json`` per dataset,
    ``builds.json`` + ``jobs/<buildName>.json``, and ``schedules.json``. Every
    file present is preserved verbatim (byte-for-byte) for sealing.
    """
    capture_dir = Path(capture_dir)
    files: List[CaptureFile] = []

    def record(rel_path: str, raw: bytes) -> None:
        files.append(CaptureFile(rel_path=rel_path, sealed_name=rel_path.replace("/", "__"), raw_bytes=raw))

    # datasets.json (required)
    dpath = capture_dir / DATASETS_FILE
    if not dpath.exists():
        raise PipelineCaptureError(f"{DATASETS_FILE}: capture is missing the required datasets file")
    draw = dpath.read_bytes()
    record(DATASETS_FILE, draw)
    datasets = tuple(_parse_dataset(d, DATASETS_FILE) for d in _data_list(_load_json(draw, DATASETS_FILE), DATASETS_FILE))
    seen_names: set = set()
    for ds in datasets:
        if ds.name in seen_names:
            raise PipelineCaptureError(f"{DATASETS_FILE}: duplicate dataset name={ds.name!r}")
        seen_names.add(ds.name)

    # schemas/<name>.json (optional, per dataset)
    schemas: Dict[str, DatasetSchema] = {}
    schemas_dir = capture_dir / SCHEMAS_DIR
    if schemas_dir.is_dir():
        for sp in sorted(schemas_dir.glob("*.json")):
            rel = f"{SCHEMAS_DIR}/{sp.name}"
            raw = sp.read_bytes()
            record(rel, raw)
            schemas[sp.stem] = _parse_schema(_load_json(raw, rel), sp.stem, rel)

    # builds.json + jobs/<buildName>.json (optional)
    builds: List[BuildV2] = []
    bpath = capture_dir / BUILDS_FILE
    jobs_dir = capture_dir / JOBS_DIR
    if bpath.exists():
        braw = bpath.read_bytes()
        record(BUILDS_FILE, braw)
        for b in _data_list(_load_json(braw, BUILDS_FILE), BUILDS_FILE):
            name = str(b.get("name") or (str(b.get("rid", "")).rsplit(".", 1)[-1]) or f"build{len(builds)}")
            jobs: List[JobV2] = []
            jp = jobs_dir / f"{name}.json"
            if jp.exists():
                jraw = jp.read_bytes()
                record(f"{JOBS_DIR}/{name}.json", jraw)
                jobs = [_parse_job(j, f"{JOBS_DIR}/{name}.json") for j in _data_list(_load_json(jraw, f"{JOBS_DIR}/{name}.json"), f"{JOBS_DIR}/{name}.json")]
            builds.append(BuildV2(
                name=name,
                rid=(str(b["rid"]) if b.get("rid") is not None else None),
                schedule_rid=(str(b["scheduleRid"]) if b.get("scheduleRid") is not None else None),
                jobs=tuple(jobs),
                raw=dict(b),
            ))

    # schedules.json (optional)
    schedules: Dict[str, ScheduleV2] = {}
    spath = capture_dir / SCHEDULES_FILE
    if spath.exists():
        sraw = spath.read_bytes()
        record(SCHEDULES_FILE, sraw)
        for s in _data_list(_load_json(sraw, SCHEDULES_FILE), SCHEDULES_FILE):
            rid = str(_require(s, "rid", SCHEDULES_FILE))
            name = str(s.get("name") or rid.rsplit(".", 1)[-1])
            schedules[rid] = ScheduleV2(rid=rid, name=name, raw=dict(s))

    return PipelineCapture(
        datasets=datasets,
        schemas_by_dataset=schemas,
        builds=tuple(builds),
        schedules_by_rid=schedules,
        files=tuple(files),
    )
