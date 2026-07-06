"""Seal a Foundry Ontology capture as a genesis shard (detached-verifiable).

Drives the SAME real, already-proven ``axm-build`` / ``axm-verify`` surface that
``seal.py`` uses (out-of-band key required; PASS / FAIL / MALFORMED /
NO_TRUSTED_KEY taxonomy). It imports no ``ghostbox`` and touches no Palantir
endpoint. Custody stays genesis's; this module only translates an
``OntologyCapture`` into candidates + content and invokes the compiler.

What the sealed shard holds:
  - ``content/`` = the VERBATIM capture files, byte-for-byte, plus ``source.txt``
    (the canonical text every claim's evidence span cites).
  - a claim graph making the ontology's STRUCTURE queryable through the repo's
    own Spectra engine (axiom_runtime): object types, properties, primary keys,
    links, cardinalities, foreign keys, and instance counts.

INVARIANT (mirrors seal.py's "external-Palantir-ID-never-becomes-custody-ID"):
Palantir ``rid`` / ``apiName`` values appear ONLY as entity labels, claim
literals, and sealed content bytes. They are NEVER the shard identity and never
a custody id. The custody id is the genesis-derived ``sh1_`` on the sealed
manifest bytes (``derive_shard_id``).

BOUNDARY on content layout: the genesis compiler seals only TOP-LEVEL files in
``content/`` (it does not recurse). The capture's ``linkTypes/<X>.json`` and
``objects/<X>.json`` are therefore staged under FLATTENED top-level names
(``linkTypes__<X>.json`` / ``objects__<X>.json``). The file BYTES are identical
to the capture; only the sealed filename is flattened. This is stated, not
hidden.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .ontology_api import OntologyCapture
from .seal import AXM_BUILD, VerifyStatus, kernel_available  # reuse the proven surface

NAMESPACE = "foundry/ontology-exit"
PUBLISHER = "@axm_core"

SOURCE_FILE = "source.txt"


@dataclass(frozen=True)
class SealedOntologyShard:
    shard_id: str               # genesis-derived sh1_, the ONLY custody identity
    shard_dir: str
    trusted_key_path: str       # out-of-band publisher pub (sibling to the shard)
    suite: str
    merkle_root: Optional[str]
    sealed_at: Optional[str]
    claim_count: int
    entity_count: int
    tier_statement: str         # honest evidence-tier statement for the packet


# ---------------------------------------------------------------------------
# Candidate + source construction
# ---------------------------------------------------------------------------


@dataclass
class _Stmt:
    text: str
    subject_label: str
    predicate: str
    object_label: str
    object_type: str            # "entity" | "literal:string" | "literal:integer"
    tier: int


def build_candidates_and_source(
    capture: OntologyCapture, *, namespace: str = NAMESPACE
) -> Tuple[List[dict], str, Dict[str, int]]:
    """Turn an ``OntologyCapture`` into (candidates, source_text, counts).

    Every claim's evidence is bound to a unique span in ``source.txt``, built the
    same incremental way ``seal.py::_candidates_and_source`` builds it.

    Claims vocabulary (see ONTOLOGY_EXIT.md for the full table):
      * entity  objectType/{apiName}                                  (object_type)
      * entity  property/{typeApiName}.{propName}                     (property)
      * entity  link/{linkApiName}                                    (link)
      * objectType/X has_property property/X.p        entity        tier 1
      * property/X.p has_type "<dataType.type>"       literal:string tier 1
      * objectType/X primary_key "<propName>"         literal:string tier 1
      * objectType/X links_to objectType/Y            entity        tier 1
      * link/L cardinality "ONE"|"MANY"               literal:string tier 1
      * link/L foreign_key "<prop>"                   literal:string tier 1
      * objectType/X instance_count "<N>"             literal:integer tier 0
          (only when objects/<X>.json present; N = rows PRESENT in the file)
        OR, when a declared totalCount differs from rows present:
      * objectType/X instance_count_declared "<totalCount>"  literal:integer tier 0
      * objectType/X instances_captured "<rows>"             literal:integer tier 0
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

    stmts: List[_Stmt] = []

    def ot_label(api_name: str) -> str:
        return f"objectType/{api_name}"

    def prop_label(type_api: str, prop_api: str) -> str:
        return f"property/{type_api}.{prop_api}"

    def link_label(api_name: str) -> str:
        return f"link/{api_name}"

    # Object types, properties, primary keys.
    for ot in capture.object_types:
        otl = ot_label(ot.api_name)
        ent(otl, "object_type")
        if ot.primary_key:
            stmts.append(
                _Stmt(f'{otl} primary_key "{ot.primary_key}"', otl, "primary_key",
                      ot.primary_key, "literal:string", 1)
            )
        for p in ot.properties:
            pl = prop_label(ot.api_name, p.api_name)
            ent(pl, "property")
            stmts.append(
                _Stmt(f"{otl} has_property {pl}", otl, "has_property", pl, "entity", 1)
            )
            stmts.append(
                _Stmt(f'{pl} has_type "{p.data_type}"', pl, "has_type", p.data_type,
                      "literal:string", 1)
            )

    # Links (outgoing, keyed by source object type).
    for source_api_name, links in capture.links_by_source.items():
        sl = ot_label(source_api_name)
        ent(sl, "object_type")
        for lk in links:
            tl = ot_label(lk.object_type_api_name)
            ent(tl, "object_type")
            ll = link_label(lk.api_name)
            ent(ll, "link")
            stmts.append(
                _Stmt(f"{sl} links_to {tl}", sl, "links_to", tl, "entity", 1)
            )
            if lk.cardinality:
                stmts.append(
                    _Stmt(f'{ll} cardinality "{lk.cardinality}"', ll, "cardinality",
                          lk.cardinality, "literal:string", 1)
                )
            if lk.foreign_key_property_api_name:
                fk = lk.foreign_key_property_api_name
                stmts.append(
                    _Stmt(f'{ll} foreign_key "{fk}"', ll, "foreign_key", fk,
                          "literal:string", 1)
                )

    # Instance counts (tier 0), only when an objects/<X>.json page is present.
    for type_api_name, page in capture.instances_by_type.items():
        otl = ot_label(type_api_name)
        ent(otl, "object_type")  # in case objects captured for a type absent from objectTypes
        rows = len(page.rows)
        total = page.total_count
        if total is not None and total != str(rows):
            # Partial capture: declare BOTH, never hide the gap.
            stmts.append(
                _Stmt(f'{otl} instance_count_declared "{total}"', otl,
                      "instance_count_declared", total, "literal:integer", 0)
            )
            stmts.append(
                _Stmt(f'{otl} instances_captured "{rows}"', otl,
                      "instances_captured", str(rows), "literal:integer", 0)
            )
        else:
            stmts.append(
                _Stmt(f'{otl} instance_count "{rows}"', otl, "instance_count",
                      str(rows), "literal:integer", 0)
            )

    # Build source.txt incrementally and bind each claim to its byte span.
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
        "Reconciled against Palantir's PUBLISHED Ontology API v2 wire shapes "
        "(list-object-types / list-outgoing-link-types / list-objects). NOT yet "
        "proven against an authorized live tenant; the fixture is an invented "
        "sample in the documented wire shape. Structure is queryable via genesis "
        "claims (tier 1) with tier-0 instance counts; verbatim API responses are "
        "sealed as content."
    )


# ---------------------------------------------------------------------------
# Seal
# ---------------------------------------------------------------------------


def seal_ontology_capture(
    capture: OntologyCapture,
    out_shard_dir: str | Path,
    *,
    namespace: str = NAMESPACE,
    title: str = "Foundry ontology exit shard",
    created_at: str = "2026-07-04T00:00:00Z",
) -> SealedOntologyShard:
    """Seal the capture through the real genesis compiler; return the shard.

    ``content/`` receives the verbatim capture files (flattened top-level names,
    byte-for-byte) and ``source.txt``. The out-of-band keypair is written to a
    ``keys/`` pool sibling to the shard, never inside it.
    """
    out_shard_dir = Path(out_shard_dir)
    work = out_shard_dir.parent
    content_dir = work / "_ontology_content"
    key_dir = work / "keys"
    if content_dir.exists():
        shutil.rmtree(content_dir)
    content_dir.mkdir(parents=True, exist_ok=True)
    key_dir.mkdir(parents=True, exist_ok=True)

    # content/: verbatim capture files (byte-for-byte) under flattened top-level
    # names, plus the canonical source.txt the claims cite.
    for cf in capture.files:
        (content_dir / cf.sealed_name).write_bytes(cf.raw_bytes)
    candidates, source_text, counts = build_candidates_and_source(capture, namespace=namespace)
    (content_dir / SOURCE_FILE).write_text(source_text, encoding="utf-8")

    candidates_path = work / "ontology_candidates.jsonl"
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
    return SealedOntologyShard(
        shard_id=_derive_shard_id(manifest_bytes),
        shard_dir=str(out_shard_dir),
        trusted_key_path=str(pub_path),
        suite=m.get("suite", "axm-hybrid1"),
        merkle_root=(m.get("integrity") or {}).get("merkle_root"),
        sealed_at=(m.get("metadata") or {}).get("created_at"),
        claim_count=counts["claims"],
        entity_count=counts["entities"],
        tier_statement=_tier_statement(),
    )


def _derive_shard_id(manifest_bytes: bytes) -> str:
    """Genesis's own sh1_ derivation. Custody identity is genesis's, not ours."""
    from axm_verify.crypto import derive_shard_id  # genesis

    return derive_shard_id(manifest_bytes)


# Re-export for callers that want the seal + verify taxonomy from one module.
__all__ = [
    "NAMESPACE",
    "PUBLISHER",
    "SealedOntologyShard",
    "VerifyStatus",
    "build_candidates_and_source",
    "kernel_available",
    "seal_ontology_capture",
]
