"""Shared seal machinery for the foundry_exit plank exits.

The ontology and pipeline exits each grew their own copy of the candidate-build +
compile boilerplate. The planks added for 9/9 coverage (logic, source, apps,
policy) share it instead, through:

  * ``Claims`` — accumulates entities + span-bound claims (every claim's evidence
    is a unique byte range in a generated ``source.txt``, exactly as
    ``ontology_seal`` builds it).
  * ``seal`` — writes verbatim ``content/`` files + ``source.txt``, runs the real
    genesis ``axm-build compile``, and returns the genesis-derived ``sh1_``.

No Palantir code, no credentials, no network. Custody is always the
genesis-derived ``sh1_`` over the sealed manifest bytes; no external id becomes
custody identity.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .seal import AXM_BUILD, VerifyStatus, kernel_available  # reuse the proven surface

SOURCE_FILE = "source.txt"


@dataclass(frozen=True)
class SealedResult:
    shard_id: str
    shard_dir: str
    trusted_key_path: str
    suite: str
    merkle_root: Optional[str]
    sealed_at: Optional[str]
    claim_count: int
    entity_count: int


class Claims:
    """Accumulate entities and span-bound claims, then materialize candidates."""

    def __init__(self, namespace: str):
        self.namespace = namespace
        self._entities: Dict[str, dict] = {}
        self._stmts: List[Tuple[str, str, str, str, int]] = []  # subj,pred,obj,obj_type,tier

    def entity(self, label: str, entity_type: str) -> str:
        self._entities.setdefault(label, {
            "type": "entity", "namespace": self.namespace, "label": label, "entity_type": entity_type,
        })
        return label

    def claim(self, subject: str, predicate: str, obj: str, obj_type: str, tier: int) -> None:
        self._stmts.append((subject, predicate, obj, obj_type, tier))

    def _text(self, subj: str, pred: str, obj: str, obj_type: str) -> str:
        # entity objects are printed bare; literals are quoted (matches ontology_seal)
        return f"{subj} {pred} {obj}" if obj_type == "entity" else f'{subj} {pred} "{obj}"'

    def build(self) -> Tuple[List[dict], str, Dict[str, int]]:
        claims: List[dict] = []
        source = ""
        for subj, pred, obj, obj_type, tier in self._stmts:
            text = self._text(subj, pred, obj, obj_type)
            start = len(source.encode("utf-8"))
            source += text
            end = len(source.encode("utf-8"))
            source += "\n"
            claims.append({
                "type": "claim", "subject_label": subj, "predicate": pred,
                "object_label": obj, "object_type": obj_type, "tier": tier,
                "evidence": {"source_file": SOURCE_FILE, "byte_start": start, "byte_end": end, "text": text},
            })
        candidates = list(self._entities.values()) + claims
        return candidates, source, {"entities": len(self._entities), "claims": len(claims)}


def seal(
    candidates: List[dict],
    source_text: str,
    content: Dict[str, bytes],
    out_shard_dir: str | Path,
    *,
    namespace: str,
    title: str,
    created_at: str = "2026-07-07T00:00:00Z",
) -> SealedResult:
    """Seal candidates + verbatim content through the real genesis compiler.

    ``content`` maps a flat top-level filename -> raw bytes (the genesis compiler
    seals only top-level files; callers flatten subpaths themselves, byte-for-byte).
    """
    out_shard_dir = Path(out_shard_dir)
    work = out_shard_dir.parent
    content_dir = work / "_content"
    key_dir = work / "keys"
    if content_dir.exists():
        shutil.rmtree(content_dir)
    content_dir.mkdir(parents=True, exist_ok=True)
    key_dir.mkdir(parents=True, exist_ok=True)

    for name, raw in content.items():
        (content_dir / name).write_bytes(raw)
    (content_dir / SOURCE_FILE).write_text(source_text, encoding="utf-8")

    candidates_path = work / "candidates.jsonl"
    candidates_path.write_text("\n".join(json.dumps(c) for c in candidates) + "\n", encoding="utf-8")

    key_path = key_dir / "publisher.key"
    pub_path = key_dir / "publisher.pub"
    if not (key_path.exists() and pub_path.exists()):
        subprocess.run([AXM_BUILD, "keygen", str(key_dir), "--name", "publisher"],
                       check=True, capture_output=True, text=True)
    subprocess.run(
        [AXM_BUILD, "compile", str(candidates_path), str(content_dir), str(out_shard_dir),
         "--private-key", str(key_path), "--namespace", namespace,
         "--title", title, "--created-at", created_at],
        check=True, capture_output=True, text=True,
    )
    manifest_bytes = (out_shard_dir / "manifest.json").read_bytes()
    m = json.loads(manifest_bytes)
    from axm_verify.crypto import derive_shard_id
    counts_entities = sum(1 for c in candidates if c.get("type") == "entity")
    counts_claims = sum(1 for c in candidates if c.get("type") == "claim")
    return SealedResult(
        shard_id=derive_shard_id(manifest_bytes),
        shard_dir=str(out_shard_dir),
        trusted_key_path=str(pub_path),
        suite=m.get("suite", "axm-hybrid1"),
        merkle_root=(m.get("integrity") or {}).get("merkle_root"),
        sealed_at=(m.get("metadata") or {}).get("created_at"),
        claim_count=counts_claims,
        entity_count=counts_entities,
    )


__all__ = ["Claims", "SealedResult", "VerifyStatus", "kernel_available", "seal", "SOURCE_FILE"]
