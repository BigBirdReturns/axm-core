"""
AXM Forge Tier 3 -- Stage 2: Deterministic Binder.

Reads raw_claims.jsonl (from Stage 1) + sentences.jsonl (from Stage 0).
Maps sentence IDs to byte spans via dictionary lookup.
Emits Genesis-compatible candidates.jsonl.

No model calls.  No network dependencies.  Pure Python.
Re-runnable in seconds if you change dedup rules or filtering.

Phase 1A fixes applied:
  - Emits 'tier' (not 'extraction_tier') and 'object_type' for Genesis.
  - Classifies object_type: entity vs literal:{integer,decimal,string,boolean}.
  - Entity Resolution: acronym expansion + case-frequency voting.
  - Predicate normalization (lowercase).
  - Raw extraction values preserved in meta for provenance.

Contiguity logic (Option C with Option A fallback):
  - Contiguous sentence groups [5,6,7] merge into one span
  - Non-contiguous groups [5,9] split into separate candidates
  - This preserves Genesis v1.0 single-span contract

Dedup key: (claim_text, byte_start, byte_end)
  - Catches overlap duplicates (same claim from overlapping batches)
  - Preserves distinct claims sharing the same evidence sentence
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from axm_forge.extraction.schemas import read_jsonl


# ---------------------------------------------------------------------------
# Object Type Classification
# ---------------------------------------------------------------------------

_RE_INTEGER = re.compile(r"^-?\d{1,15}$")
_RE_DECIMAL = re.compile(r"^-?\d+\.\d+$")
_RE_QUANTITY = re.compile(
    r"^-?\d[\d,.]*\s*"
    r"(?:%|mg|kg|g|ml|l|mm|cm|m|km|lb|oz|ft|in|hr|min|sec|days?|weeks?"
    r"|months?|years?|mph|km/h|kph|psi|mmHg|°[CF]|USD|EUR|GBP|\$)s?$",
    re.IGNORECASE,
)
_RE_YEAR = re.compile(r"^(?:1[0-9]{3}|2[0-9]{3})$")
_RE_DATE = re.compile(
    r"^(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}[/-]\d{1,2}[/-]\d{1,2}"
    r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{1,2},?\s+\d{4})$",
    re.IGNORECASE,
)
_RE_BOOLEAN = re.compile(r"^(?:true|false|yes|no)$", re.IGNORECASE)


def _classify_object_type(obj: str) -> str:
    """Classify an object value as entity or literal type.

    Uses heuristics to detect numbers, dates, quantities, and booleans.
    Returns Genesis-valid types: entity, literal:integer, literal:decimal,
    literal:string, literal:boolean.
    Conservative: when in doubt, returns 'entity' (safe default).
    """
    s = obj.strip()
    if not s:
        return "entity"
    if _RE_BOOLEAN.match(s):
        return "literal:boolean"
    if _RE_INTEGER.match(s) or _RE_YEAR.match(s):
        return "literal:integer"
    if _RE_DECIMAL.match(s):
        return "literal:decimal"
    if _RE_QUANTITY.match(s) or _RE_DATE.match(s):
        return "literal:string"
    return "entity"


# Words that should never be standalone entity labels.
# These indicate the LLM extracted a modifier, pronoun, or filler
# instead of a real entity. Claims with these as subject or entity-type
# object are dropped during binding.
_ENTITY_STOPWORDS = frozenset({
    # pronouns / determiners
    "it", "its", "they", "them", "he", "she", "this", "that", "these",
    "those", "which", "what", "who", "whom",
    # articles (only 'the' - 'a'/'an' too aggressive as LLM rarely extracts these alone)
    "the",
    # vague modifiers
    "significantly", "approximately", "generally", "typically", "usually",
    "often", "sometimes", "rarely", "very", "highly", "extremely",
    "however", "therefore", "moreover", "furthermore", "additionally",
    "also", "thus", "hence",
    # filler phrases
    "the study", "the results", "the data", "the authors", "the patient",
    "the above", "the following", "the same",
    # relative clauses / connectors
    "as described above", "as mentioned", "as noted", "as shown",
    "in the control group", "in this case", "in this study",
    "not recommended", "not applicable", "unknown", "various", "other",
    "several", "many", "some", "all", "none", "most", "both",
})


def _is_garbage_entity(label: str) -> bool:
    """Return True if label is too vague/short to be a meaningful entity."""
    s = label.strip()
    if not s:
        return True
    # Single lowercase character
    if len(s) == 1 and not s.isupper():
        return True
    # All-uppercase short tokens are likely acronyms (WHO, TXA, DNA) - keep them
    if s.isupper() and len(s) <= 6:
        return False
    # Known stopword/filler (case-insensitive check)
    if s.lower() in _ENTITY_STOPWORDS:
        return True
    return False


# ---------------------------------------------------------------------------
# Entity Resolution
# ---------------------------------------------------------------------------

class EntityResolver:
    """Resolve entity surface forms to canonical labels.

    Two mechanisms:
    1. Acronym expansion: scans source text for "Full Name (ACRONYM)" patterns.
       Maps ACRONYM -> Full Name for all subsequent occurrences.
    2. Case voting: tracks frequency of each surface form. When multiple
       forms differ only by case, the most frequent one wins.

    Does not touch evidence bytes. Only affects subject/object labels
    in the emitted candidates. Raw labels preserved in meta.
    """

    def __init__(self, source_text: str) -> None:
        # acronym -> full expansion
        self.aliases: Dict[str, str] = {}
        # surface form -> count
        self._counts: Counter = Counter()
        # lowercased key -> canonical surface form
        self._canon: Dict[str, str] = {}

        # Scan source text for "Full Name (ACRONYM)" definitions.
        # Pattern: one or more words (first capitalized) followed by (UPPERCASE2-6)
        # Excludes leading articles (The, A, An) from the captured name.
        for m in re.finditer(
            r"(?:(?:The|A|An)\s+)?([A-Z][a-zA-Z]+(?:\s+[a-zA-Z]+){0,4})\s+\(([A-Z][A-Z0-9]{1,5})\)",
            source_text,
        ):
            full_name = m.group(1).strip()
            acronym = m.group(2).strip()
            if full_name and acronym and len(acronym) >= 2:
                self.aliases[acronym] = full_name

    def register(self, label: str) -> None:
        """Register an entity occurrence to build frequency stats."""
        if not label or not label.strip():
            return
        # Expand acronym first
        resolved = self.aliases.get(label.strip(), label.strip())
        self._counts[resolved] += 1
        # Track canonical form by lowercase key
        key = resolved.lower()
        if key not in self._canon:
            self._canon[key] = resolved
        else:
            # Most frequent surface form wins
            current = self._canon[key]
            if self._counts[resolved] > self._counts[current]:
                self._canon[key] = resolved

    def resolve(self, label: str) -> str:
        """Return canonical form for an entity label."""
        if not label or not label.strip():
            return "Unknown"
        s = label.strip()
        # Step 1: expand acronyms
        s = self.aliases.get(s, s)
        # Step 2: case canonicalization
        return self._canon.get(s.lower(), s)


# ---------------------------------------------------------------------------
# Main Binder
# ---------------------------------------------------------------------------

def run_stage2(
    source_path: Path,
    sentences_path: Path,
    raw_claims_path: Path,
    out_path: Path,
) -> Dict[str, Any]:
    """
    Bind raw claims to byte spans and emit Genesis-compatible candidates.

    Returns summary dict with counts.
    """
    # Load sentence ground truth
    sent_map: Dict[int, Dict[str, Any]] = {}
    for rec in read_jsonl(sentences_path):
        sent_map[rec["index"]] = rec

    source_bytes = source_path.read_bytes()
    source_text = source_bytes.decode("utf-8")

    # Initialize entity resolver from source text
    resolver = EntityResolver(source_text)

    # Pass 1: register all entity mentions to build frequency stats.
    # This ensures case voting has full information before we emit anything.
    raw_lines: List[Dict[str, Any]] = []
    with raw_claims_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            raw_lines.append(raw)

            meta = raw.get("meta", {})
            if isinstance(meta, dict) and meta.get("type") == "progress":
                continue

            subj = (raw.get("subject") or "").strip()
            obj = (raw.get("object") or "").strip()
            if subj:
                resolver.register(subj)
            if obj and _classify_object_type(obj) == "entity":
                resolver.register(obj)

    # Pass 2: bind claims to byte spans and emit
    seen_claims: Set[Tuple[str, int, int]] = set()

    total_raw = 0
    skipped_progress = 0
    skipped_no_ids = 0
    skipped_dedup = 0
    skipped_garbage = 0
    emitted = 0

    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w", encoding="utf-8") as f_out:
        for raw in raw_lines:
            total_raw += 1

            # Skip progress markers
            meta = raw.get("meta", {})
            if isinstance(meta, dict) and meta.get("type") == "progress":
                skipped_progress += 1
                continue

            # Resolve sentence IDs
            ids = raw.get("sentence_ids", [])
            if not isinstance(ids, list):
                skipped_no_ids += 1
                continue

            valid_ids = sorted(set(i for i in ids if isinstance(i, int) and i in sent_map))
            if not valid_ids:
                skipped_no_ids += 1
                continue

            # Group contiguous sentence IDs (same page)
            groups: List[List[int]] = []
            current_group = [valid_ids[0]]

            for k in range(1, len(valid_ids)):
                prev_id = valid_ids[k - 1]
                curr_id = valid_ids[k]
                is_sequential = (curr_id == prev_id + 1)
                same_page = (sent_map[prev_id].get("page", 0) == sent_map[curr_id].get("page", 0))

                if is_sequential and same_page:
                    current_group.append(curr_id)
                else:
                    groups.append(current_group)
                    current_group = [curr_id]
            groups.append(current_group)

            # Raw fields from LLM extraction
            claim_text = (raw.get("claim_text") or "").strip() or "Unknown"
            raw_subj = (raw.get("subject") or "").strip() or "Unknown"
            raw_pred = (raw.get("predicate") or "").strip() or "relates to"
            raw_obj = (raw.get("object") or "").strip() or claim_text

            # Classify object type
            obj_type = _classify_object_type(raw_obj)

            # Resolve entities (only entity-type objects, not literals)
            clean_subj = resolver.resolve(raw_subj)
            clean_obj = resolver.resolve(raw_obj) if obj_type == "entity" else raw_obj
            clean_pred = raw_pred.lower()

            # Quality gate: reject claims with garbage entity labels
            if _is_garbage_entity(clean_subj):
                skipped_garbage += 1
                continue
            # If object is garbage but typed as entity, downgrade to literal:string
            # (preserves the claim, prevents garbage entity node)
            if obj_type == "entity" and _is_garbage_entity(clean_obj):
                obj_type = "literal:string"

            # Emit one candidate per contiguous group
            for group in groups:
                start_id = group[0]
                end_id = group[-1]

                byte_start = sent_map[start_id]["byte_start"]
                byte_end = sent_map[end_id]["byte_end"]
                page = sent_map[start_id].get("page", 0)

                # Content-aware dedup
                dedup_key = (claim_text, byte_start, byte_end)
                if dedup_key in seen_claims:
                    skipped_dedup += 1
                    continue
                seen_claims.add(dedup_key)

                # Evidence: exact bytes from source (ground truth)
                evidence_bytes = source_bytes[byte_start:byte_end]
                evidence_text = evidence_bytes.decode("utf-8")

                candidate = {
                    "subject": clean_subj,
                    "predicate": clean_pred,
                    "object": clean_obj,
                    "object_type": obj_type,
                    "evidence": evidence_text,
                    "tier": 3,
                    "byte_start": byte_start,
                    "byte_end": byte_end,
                    "source_page": page,
                    "extraction_method": "llm_sentence_group",
                    "meta": {
                        "claim_text": claim_text,
                        "sentence_ids": group,
                        "raw_subject": raw_subj,
                        "raw_object": raw_obj,
                    },
                }
                f_out.write(json.dumps(candidate, ensure_ascii=False) + "\n")
                emitted += 1

    return {
        "status": "PASS",
        "total_raw_lines": total_raw,
        "skipped_progress": skipped_progress,
        "skipped_no_valid_ids": skipped_no_ids,
        "skipped_dedup": skipped_dedup,
        "skipped_garbage": skipped_garbage,
        "emitted": emitted,
        "aliases_found": len(resolver.aliases),
        "canonical_entities": len(resolver._canon),
        "out": str(out_path),
    }


def main() -> None:
    import argparse
    p = argparse.ArgumentParser(description="AXM Tier 3 Stage 2: deterministic binder")
    p.add_argument("--source", required=True, help="Path to source.txt")
    p.add_argument("--sentences", required=True, help="Path to sentences.jsonl")
    p.add_argument("--raw", required=True, help="Path to raw_claims.jsonl")
    p.add_argument("--out", required=True, help="Path to candidates.jsonl")
    args = p.parse_args()

    report = run_stage2(
        source_path=Path(args.source),
        sentences_path=Path(args.sentences),
        raw_claims_path=Path(args.raw),
        out_path=Path(args.out),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
