"""
Decision Shard Loop — closes the knowledge compounding loop.

Records every query/response/citation tuple and converts them into
Genesis candidates (SPO triples) that can be compiled into a
"decision shard" — a shard whose knowledge base is the system's
own cited responses over time.

Flow:
    User query → Spectra chat → cited answer
        ↓
    DecisionLogger.record(query, answer, citations)
        ↓
    interactions.jsonl  (append-only audit log)
        ↓
    DecisionForgeAdapter.export_candidates(out_path)
        ↓
    candidates.jsonl  (feed into nodal_run.py → Genesis)
        ↓
    decision shard (mountable via Spectra)

This closes the loop: system responses become indexed knowledge,
making the knowledge graph self-compounding from real usage.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


class DecisionLogger:
    """
    Append-only interaction log.
    Each record: {ts, query, answer, citations, session_id, metadata}
    """

    def __init__(self, log_path: str | Path) -> None:
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        query: str,
        answer: str,
        citations: List[Dict[str, Any]],
        session_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Append an interaction. Returns the interaction ID."""
        ts = time.time()
        interaction_id = "int_" + hashlib.sha256(
            f"{ts}:{query}".encode()
        ).hexdigest()[:16]

        record = {
            "interaction_id": interaction_id,
            "ts": ts,
            "query": query,
            "answer": answer,
            "citations": citations,
            "session_id": session_id or "",
            "metadata": metadata or {},
        }

        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

        return interaction_id

    def load(self) -> List[Dict[str, Any]]:
        """Load all recorded interactions."""
        if not self.log_path.exists():
            return []
        out = []
        for line in self.log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out

    def count(self) -> int:
        if not self.log_path.exists():
            return 0
        return sum(1 for line in self.log_path.read_text().splitlines() if line.strip())


class DecisionForgeAdapter:
    """
    Converts recorded interactions into Genesis candidates.jsonl.

    Each interaction becomes a set of SPO triples:
      - query → answered_by → answer_summary
      - query → cites → claim_id  (for each citation)
      - answer_summary → derived_from → source_shard_id

    These can be fed directly to compiler_generic.py to build a decision shard.
    """

    def __init__(self, logger: DecisionLogger) -> None:
        self.logger = logger

    def export_candidates(
        self,
        out_path: str | Path,
        *,
        min_citations: int = 1,
        max_records: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Export interactions as candidates.jsonl.

        min_citations: skip interactions with fewer citations (uncited responses
                       are noise — we don't want to canonize hallucinated claims).
        Returns stats dict.
        """
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        interactions = self.logger.load()
        if max_records:
            interactions = interactions[-max_records:]

        written = 0
        skipped = 0

        with out_path.open("w", encoding="utf-8") as f:
            for rec in interactions:
                citations = rec.get("citations") or []
                if len(citations) < min_citations:
                    skipped += 1
                    continue

                query = rec["query"]
                answer = rec["answer"]
                ts = rec.get("ts", 0)
                int_id = rec.get("interaction_id", "unknown")

                # Triple 1: query → was_answered_by → answer_summary
                answer_summary = answer[:200].replace("\n", " ")
                f.write(json.dumps({
                    "subject": f"query:{int_id}",
                    "predicate": "was_answered_by",
                    "object": answer_summary,
                    "object_type": "literal:string",
                    "tier": 0,
                    "confidence": 1.0,
                    "evidence": query,
                    "locator": {
                        "kind": "decision",
                        "interaction_id": int_id,
                        "ts": ts,
                    },
                }) + "\n")
                written += 1

                # Triple 2: one claim per citation (links query to source claims)
                for cite in citations[:10]:
                    claim_id = cite.get("claim_id") or cite.get("id") or ""
                    shard_id = cite.get("shard_id") or ""
                    subj = cite.get("subject") or cite.get("subject_label") or ""
                    pred = cite.get("predicate") or "cited_by"
                    obj = cite.get("object") or cite.get("object_label") or ""
                    if not claim_id:
                        continue
                    f.write(json.dumps({
                        "subject": f"query:{int_id}",
                        "predicate": "cites_claim",
                        "object": claim_id,
                        "object_type": "entity",
                        "tier": 0,
                        "confidence": 1.0,
                        "evidence": f"{subj} → {pred} → {obj}",
                        "locator": {
                            "kind": "decision",
                            "interaction_id": int_id,
                            "source_shard": shard_id,
                        },
                    }) + "\n")
                    written += 1

        return {
            "total_interactions": len(interactions),
            "exported": written,
            "skipped_uncited": skipped,
            "output": str(out_path),
        }
