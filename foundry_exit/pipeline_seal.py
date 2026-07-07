"""Seal a Foundry pipeline capture as a genesis shard (detached-verifiable).

Sibling of ``ontology_seal.py``. Drives the SAME real, already-proven
``axm-build`` / ``axm-verify`` surface (out-of-band key required). Imports no
``ghostbox`` and touches no Palantir endpoint. Custody stays genesis's; this
module only translates a ``PipelineCapture`` into candidates + content and
invokes the compiler.

What the sealed shard holds:
  - ``content/`` = the VERBATIM capture files, byte-for-byte, plus ``source.txt``
    (the canonical text every claim's evidence span cites).
  - a claim graph making the pipeline's STRUCTURE queryable through the repo's
    own Spectra engine: dataset schemas (typed columns), the dependency DAG
    (``dataset A feeds dataset B``), and build/schedule provenance.

What it does NOT hold, and never pretends to: the transform RUNTIME. Every claim
is tier 1 STRUCTURE reconstructed from published wire shapes — no fabricated
row counts, no runtime, no lineage beyond what the captured build I/O exposes.

INVARIANT (mirrors the ontology exit): no Palantir ``rid`` ever appears in the
sealed ``manifest.json``. The custody id is always the genesis-derived ``sh1_``
over the sealed manifest bytes.

BOUNDARY on content layout: the genesis compiler seals only TOP-LEVEL files in
``content/``. The capture's ``schemas/<X>.json`` and ``jobs/<X>.json`` are
therefore staged under FLATTENED top-level names (``schemas__<X>.json`` /
``jobs__<X>.json``). The file BYTES are identical to the capture; only the
sealed filename is flattened. Stated, not hidden.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .pipeline_api import PipelineCapture
from .seal import AXM_BUILD, VerifyStatus, kernel_available  # reuse the proven surface

NAMESPACE = "foundry/pipeline-exit"
PUBLISHER = "@axm_core"
SOURCE_FILE = "source.txt"


@dataclass(frozen=True)
class SealedPipelineShard:
    shard_id: str               # genesis-derived sh1_, the ONLY custody identity
    shard_dir: str
    trusted_key_path: str       # out-of-band publisher pub (sibling to the shard)
    suite: str
    merkle_root: Optional[str]
    sealed_at: Optional[str]
    claim_count: int
    entity_count: int
    dataset_count: int
    edge_count: int
    tier_statement: str


@dataclass
class _Stmt:
    text: str
    subject_label: str
    predicate: str
    object_label: str
    object_type: str            # "entity" | "literal:string"
    tier: int


def build_candidates_and_source(
    capture: PipelineCapture, *, namespace: str = NAMESPACE
) -> Tuple[List[dict], str, Dict[str, int]]:
    """Turn a ``PipelineCapture`` into (candidates, source_text, counts).

    Claims vocabulary (see PIPELINE_EXIT.md):
      * entity  dataset/{name}                                      (dataset)
      * entity  field/{dataset}.{col}                               (field)
      * entity  build/{name}                                        (build)
      * entity  schedule/{name}                                     (schedule)
      * dataset/X has_field field/X.c            entity        tier 1
      * field/X.c has_type "<type>"              literal:string tier 1
      * dataset/A feeds dataset/B                entity        tier 1   (DAG edge)
      * dataset/B produced_by build/{b}          entity        tier 1
      * build/{b} triggered_by schedule/{s}      entity        tier 1
    """
    entities: Dict[str, dict] = {}

    def ent(label: str, entity_type: str) -> None:
        if label not in entities:
            entities[label] = {
                "type": "entity",
                "namespace": namespace,
                "label": label,
                "entity_type": entity_type,
            }

    def ds_label(name: str) -> str:
        return f"dataset/{name}"

    def field_label(ds: str, col: str) -> str:
        return f"field/{ds}.{col}"

    def build_label(name: str) -> str:
        return f"build/{name}"

    def sched_label(name: str) -> str:
        return f"schedule/{name}"

    stmts: List[_Stmt] = []

    # Datasets + their schema fields.
    for d in capture.datasets:
        ent(ds_label(d.name), "dataset")
    for ds_name, schema in capture.schemas_by_dataset.items():
        dl = ds_label(ds_name)
        ent(dl, "dataset")  # in case a schema was captured for a dataset absent from datasets.json
        for f in schema.fields:
            fl = field_label(ds_name, f.name)
            ent(fl, "field")
            stmts.append(_Stmt(f"{dl} has_field {fl}", dl, "has_field", fl, "entity", 1))
            stmts.append(_Stmt(f'{fl} has_type "{f.type}"', fl, "has_type", f.type, "literal:string", 1))

    # The dependency DAG: dataset A feeds dataset B (from build job I/O).
    for src, dst, _build in capture.edges():
        sl, tl = ds_label(src), ds_label(dst)
        ent(sl, "dataset")
        ent(tl, "dataset")
        stmts.append(_Stmt(f"{sl} feeds {tl}", sl, "feeds", tl, "entity", 1))

    # Provenance: which build produced which dataset.
    for ds_name, build_name in capture.outputs_by_build():
        dl = ds_label(ds_name)
        bl = build_label(build_name)
        ent(dl, "dataset")
        ent(bl, "build")
        stmts.append(_Stmt(f"{dl} produced_by {bl}", dl, "produced_by", bl, "entity", 1))

    # Provenance: which schedule triggers which build.
    for b in capture.builds:
        if b.schedule_rid and b.schedule_rid in capture.schedules_by_rid:
            bl = build_label(b.name)
            sname = capture.schedules_by_rid[b.schedule_rid].name
            slab = sched_label(sname)
            ent(bl, "build")
            ent(slab, "schedule")
            stmts.append(_Stmt(f"{bl} triggered_by {slab}", bl, "triggered_by", slab, "entity", 1))

    # Build source.txt incrementally; bind each claim to its byte span.
    claims: List[dict] = []
    source = ""
    for s in stmts:
        start = len(source.encode("utf-8"))
        source += s.text
        end = len(source.encode("utf-8"))
        source += "\n"
        claims.append(
            {
                "type": "claim",
                "subject_label": s.subject_label,
                "predicate": s.predicate,
                "object_label": s.object_label,
                "object_type": s.object_type,
                "tier": s.tier,
                "evidence": {
                    "source_file": SOURCE_FILE,
                    "byte_start": start,
                    "byte_end": end,
                    "text": s.text,
                },
            }
        )

    candidates = list(entities.values()) + claims
    counts = {"entities": len(entities), "claims": len(claims)}
    return candidates, source, counts


def _tier_statement() -> str:
    return (
        "Reconciled against Palantir's PUBLISHED platform API v2 wire shapes "
        "(Datasets API get-dataset / get-dataset-schema; Orchestration API "
        "get-build / list-jobs-of-build / get-schedule). NOT yet proven against "
        "an authorized live tenant; the fixture is an invented sample in the "
        "documented wire shape. Carries pipeline STRUCTURE only — dataset "
        "schemas, the dependency DAG (reconstructed from captured build job "
        "input/output refs), and build/schedule provenance — all tier 1. It does "
        "NOT carry the transform RUNTIME (the transforms framework, decorators, "
        "Spark orchestration, incremental engine); that is rebuilt on the "
        "customer's own infrastructure. See WORKFLOW_EXIT_MAP.md Layer 2."
    )


def seal_pipeline_capture(
    capture: PipelineCapture,
    out_shard_dir: str | Path,
    *,
    namespace: str = NAMESPACE,
    title: str = "Foundry pipeline exit shard",
    created_at: str = "2026-07-07T00:00:00Z",
) -> SealedPipelineShard:
    """Seal the capture through the real genesis compiler; return the shard."""
    out_shard_dir = Path(out_shard_dir)
    work = out_shard_dir.parent
    content_dir = work / "_pipeline_content"
    key_dir = work / "keys"
    if content_dir.exists():
        shutil.rmtree(content_dir)
    content_dir.mkdir(parents=True, exist_ok=True)
    key_dir.mkdir(parents=True, exist_ok=True)

    for cf in capture.files:
        (content_dir / cf.sealed_name).write_bytes(cf.raw_bytes)
    candidates, source_text, counts = build_candidates_and_source(capture, namespace=namespace)
    (content_dir / SOURCE_FILE).write_text(source_text, encoding="utf-8")

    candidates_path = work / "pipeline_candidates.jsonl"
    candidates_path.write_text(
        "\n".join(json.dumps(c) for c in candidates) + "\n", encoding="utf-8"
    )

    key_path = key_dir / "publisher.key"
    pub_path = key_dir / "publisher.pub"
    if not (key_path.exists() and pub_path.exists()):
        subprocess.run(
            [AXM_BUILD, "keygen", str(key_dir), "--name", "publisher"],
            check=True, capture_output=True, text=True,
        )

    subprocess.run(
        [
            AXM_BUILD, "compile", str(candidates_path), str(content_dir), str(out_shard_dir),
            "--private-key", str(key_path),
            "--namespace", namespace, "--title", title, "--created-at", created_at,
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    manifest_bytes = (out_shard_dir / "manifest.json").read_bytes()
    m = json.loads(manifest_bytes)
    return SealedPipelineShard(
        shard_id=_derive_shard_id(manifest_bytes),
        shard_dir=str(out_shard_dir),
        trusted_key_path=str(pub_path),
        suite=m.get("suite", "axm-hybrid1"),
        merkle_root=(m.get("integrity") or {}).get("merkle_root"),
        sealed_at=(m.get("metadata") or {}).get("created_at"),
        claim_count=counts["claims"],
        entity_count=counts["entities"],
        dataset_count=len(capture.datasets),
        edge_count=len(capture.edges()),
        tier_statement=_tier_statement(),
    )


def _derive_shard_id(manifest_bytes: bytes) -> str:
    from axm_verify.crypto import derive_shard_id  # genesis

    return derive_shard_id(manifest_bytes)


__all__ = [
    "NAMESPACE",
    "PUBLISHER",
    "SealedPipelineShard",
    "VerifyStatus",
    "build_candidates_and_source",
    "kernel_available",
    "seal_pipeline_capture",
]
