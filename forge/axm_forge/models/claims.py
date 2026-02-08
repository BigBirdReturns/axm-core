from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple
import hashlib
import json

from .types import Locator, TextSpan, Chunk


@dataclass(frozen=True)
class ClaimArg:
    role: str
    entity_id: str


@dataclass(frozen=True)
class SourceSpan:
    locator: Locator
    text_span: TextSpan
    snippet: str

    @property
    def span_key(self) -> str:
        # Stable key used in claim IDs
        h = hashlib.blake2b(digest_size=16)
        h.update(json.dumps(self.locator.to_dict(), sort_keys=True).encode("utf-8"))
        h.update(f"{self.text_span.artifact}:{self.text_span.start}:{self.text_span.end}".encode("utf-8"))
        return h.hexdigest()

    def to_dict(self) -> dict:
        return {
            "locator": self.locator.to_dict(),
            "text_span": {"artifact": self.text_span.artifact, "start": self.text_span.start, "end": self.text_span.end},
            "snippet": self.snippet,
            "span_key": self.span_key,
        }


@dataclass(frozen=True)
class Claim:
    claim_id: str
    predicate: str
    args: Tuple[ClaimArg, ...]
    value: Any
    polarity: str
    conditions: Tuple[Any, ...]
    source_spans: Tuple[SourceSpan, ...]
    provenance: Dict[str, Any]

    def to_dict(self) -> dict:
        return {
            "claim_id": self.claim_id,
            "predicate": self.predicate,
            "args": [{"role": a.role, "entity_id": a.entity_id} for a in self.args],
            "value": self.value,
            "polarity": self.polarity,
            "conditions": list(self.conditions),
            "source_spans": [s.to_dict() for s in self.source_spans],
            "provenance": self.provenance,
        }


@dataclass
class ClaimGenContext:
    doc_id: str
    extracted_text: str
    chunks: List[Chunk]
    entities: Any
    metrics: Dict[str, Any]


def _stable_json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def make_entity_id(doc_id: str, kind: str, name: str) -> str:
    h = hashlib.blake2b(digest_size=16)
    h.update(doc_id.encode("utf-8"))
    h.update(b"|")
    h.update(kind.encode("utf-8"))
    h.update(b"|")
    h.update(name.strip().lower().encode("utf-8"))
    return f"ent:{h.hexdigest()}"


def make_claim_id(
    doc_id: str,
    predicate: str,
    args: List[ClaimArg],
    value: Any,
    primary_span: SourceSpan,
    span_key: str,
) -> str:
    h = hashlib.blake2b(digest_size=16)
    h.update(doc_id.encode("utf-8"))
    h.update(b"|")
    h.update(predicate.strip().lower().encode("utf-8"))
    h.update(b"|")
    h.update(_stable_json_bytes([{"role": a.role, "entity_id": a.entity_id} for a in args]))
    h.update(b"|")
    h.update(_stable_json_bytes(value))
    h.update(b"|")
    h.update(span_key.encode("utf-8"))
    return f"clm:{h.hexdigest()}"
