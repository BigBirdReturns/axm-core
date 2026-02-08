"""
AXM Forge Tier 3 extraction schemas.

Single source of truth for data shapes consumed by:
  - tier3_segmenter (Stage 0, produces Segment)
  - tier3_stage1    (Stage 1, produces RawClaim)
  - tier3_stage2    (Stage 2, produces CandidateClaim)
  - doctor_tier3    (validation, reads CandidateClaim)

Genesis v1.0 compatibility: CandidateClaim emits single contiguous
byte spans with subject/predicate/object fields.  The claim_text
field lives in meta so Genesis verifier sees standard SPO + evidence.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class Segment:
    """Output of Stage 0: deterministic sentence with byte-exact span."""
    index: int
    text: str
    byte_start: int
    byte_end: int
    page: int = 0


@dataclass(frozen=True)
class RawClaim:
    """Output of Stage 1: LLM extraction, unbound."""
    claim_text: str
    sentence_ids: List[int]
    subject: Optional[str] = None
    predicate: Optional[str] = None
    object: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class CandidateClaim:
    """Output of Stage 2: byte-bound, Genesis-compatible."""
    subject: str
    predicate: str
    object: str
    evidence: str
    byte_start: int
    byte_end: int
    source_page: int
    extraction_tier: int = 3
    extraction_method: str = "llm_sentence_group"
    meta: Optional[Dict[str, Any]] = None


# -- I/O helpers --

CANDIDATE_REQUIRED_KEYS = frozenset(
    ["subject", "predicate", "object", "evidence", "byte_start", "byte_end"]
)


def write_jsonl(path: Path, records: List[Any]) -> None:
    """Write dataclass instances or dicts to JSONL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            d = asdict(r) if hasattr(r, "__dataclass_fields__") else r
            f.write(json.dumps(d, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    """Read JSONL file, skip blank lines."""
    out: List[Dict[str, Any]] = []
    if not path.exists():
        return out
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            out.append(json.loads(s))
    return out
