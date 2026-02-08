"""
AXM Tier 3 Doctor: validates candidates.jsonl against source.txt.

Checks:
  1. Every candidate has required fields (subject, predicate, object, evidence, byte_start, byte_end)
  2. byte_start < byte_end, both within source bounds
  3. source_bytes[byte_start:byte_end] == evidence.encode("utf-8")  (byte-exact)
  4. Ambiguity detection: flags evidence strings appearing >1 time in source

Works with both:
  - New sentence-based pipeline (Stage 0/1/2)
  - Legacy string-match pipeline (tier3_ollama)

Can also orchestrate the full pipeline when AXM_DOCTOR_TIER3=1.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from axm_forge.extraction.schemas import read_jsonl


@dataclass(frozen=True)
class Tier3CheckResult:
    ok: bool
    errors: List[str]
    emitted: int = 0
    validated: int = 0
    dropped: int = 0
    ambiguous_spans: int = 0
    ambiguity_rate: Optional[float] = None
    drop_rate: Optional[float] = None


def _count_occurrences_capped(hay: bytes, needle: bytes, cap: int = 2) -> int:
    """Count occurrences of needle in hay, capped at `cap`.  Early exit."""
    if not needle:
        return 0
    count = 0
    start = 0
    while count < cap:
        idx = hay.find(needle, start)
        if idx == -1:
            return count
        count += 1
        start = idx + 1
    return count


def validate_candidates_against_source(
    source_path: Path,
    candidates_path: Path,
) -> Tier3CheckResult:
    """
    Byte-exact validation of candidates.jsonl against source.txt.

    Every candidate must satisfy:
        source_bytes[byte_start:byte_end] == evidence.encode("utf-8")
    """
    errors: List[str] = []

    if not source_path.exists():
        return Tier3CheckResult(ok=False, errors=[f"missing source: {source_path}"])
    if not candidates_path.exists():
        return Tier3CheckResult(ok=False, errors=[f"missing candidates: {candidates_path}"])

    source_bytes = source_path.read_bytes()
    try:
        source_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        return Tier3CheckResult(ok=False, errors=[f"source.txt invalid utf-8: {exc}"])

    candidates = read_jsonl(candidates_path)
    validated = 0
    ambiguous_count = 0
    ambiguity_cache: Dict[bytes, int] = {}

    for i, c in enumerate(candidates):
        bs = c.get("byte_start")
        be = c.get("byte_end")
        ev = c.get("evidence")
        subj = c.get("subject")
        pred = c.get("predicate")
        obj = c.get("object")

        if not all(isinstance(x, str) for x in [ev, subj, pred, obj]):
            errors.append(f"row {i}: subject/predicate/object/evidence must be strings")
            continue
        if not isinstance(bs, int) or not isinstance(be, int):
            errors.append(f"row {i}: byte_start/byte_end must be int")
            continue
        if len(ev) == 0:
            errors.append(f"row {i}: evidence string is empty")
            continue
        if bs >= be:
            errors.append(f"row {i}: zero-length or inverted span {bs}:{be}")
            continue
        if bs < 0 or be > len(source_bytes):
            errors.append(f"row {i}: span {bs}:{be} out of bounds (source len {len(source_bytes)})")
            continue

        slice_bytes = source_bytes[bs:be]
        ev_bytes = ev.encode("utf-8")

        if slice_bytes != ev_bytes:
            errors.append(f"row {i}: span bytes do not match evidence bytes")
            continue

        cached = ambiguity_cache.get(ev_bytes)
        if cached is None:
            cached = _count_occurrences_capped(source_bytes, ev_bytes)
            ambiguity_cache[ev_bytes] = cached
        if cached >= 2:
            ambiguous_count += 1

        validated += 1

    total = len(candidates)
    dropped = total - validated

    ambiguity_rate: Optional[float] = round(ambiguous_count / validated, 4) if validated > 0 else None
    drop_rate: Optional[float] = round(dropped / total, 4) if total > 0 else None

    return Tier3CheckResult(
        ok=(len(errors) == 0),
        errors=errors,
        emitted=total,
        validated=validated,
        dropped=dropped,
        ambiguous_spans=ambiguous_count,
        ambiguity_rate=ambiguity_rate,
        drop_rate=drop_rate,
    )


def maybe_run_tier3_pipeline(
    source_path: Path,
    out_dir: Path,
) -> Dict[str, Any]:
    """Run full Stage 0/1/2 pipeline if AXM_DOCTOR_TIER3=1."""
    if os.environ.get("AXM_DOCTOR_TIER3", "0") != "1":
        return {"skipped": True}

    try:
        from axm_forge.extraction.tiers.tier3_segmenter import run_segmentation
        from axm_forge.extraction.tiers.tier3_stage1 import run_stage1
        from axm_forge.extraction.tiers.tier3_stage2 import run_stage2
    except ImportError as exc:
        return {"skipped": True, "error": f"Import failed: {exc}"}

    sentences_path = out_dir / "sentences.jsonl"
    raw_claims_path = out_dir / "raw_claims.jsonl"
    candidates_path = out_dir / "candidates.jsonl"

    model = os.environ.get("AXM_OLLAMA_MODEL", "qwen2.5:7b-instruct")
    host = os.environ.get("AXM_OLLAMA_HOST", "http://127.0.0.1:11434")

    seg_count = run_segmentation(source_path, sentences_path)
    s1_report = run_stage1(sentences_path=sentences_path, out_path=raw_claims_path, model=model, host=host)
    s2_report = run_stage2(source_path=source_path, sentences_path=sentences_path,
                           raw_claims_path=raw_claims_path, out_path=candidates_path)

    return {"segments": seg_count, "stage1": s1_report, "stage2": s2_report}


def run_tier3_doctor(
    out_dir: Path,
    validation_only: bool = False,
) -> Tuple[bool, Dict[str, Any]]:
    """Run Tier 3 doctor check.  Validates candidates.jsonl byte-exactness."""
    out_dir = Path(out_dir).resolve()
    source_path = out_dir / "source.txt"
    cand_path = out_dir / "candidates.jsonl"

    report: Dict[str, Any] = {}
    tier3_enabled = os.environ.get("AXM_DOCTOR_TIER3", "0") == "1"

    if tier3_enabled and not source_path.exists():
        report["tier3_validate"] = {"ok": False, "status": "missing_source"}
        return False, report

    if not validation_only:
        report["tier3_extract"] = maybe_run_tier3_pipeline(source_path, out_dir)
    else:
        report["tier3_extract"] = {"skipped": True, "reason": "validation_only"}

    if cand_path.exists():
        vr = validate_candidates_against_source(source_path, cand_path)
        report["tier3_validate"] = {
            "ok": vr.ok,
            "emitted": vr.emitted,
            "validated": vr.validated,
            "dropped": vr.dropped,
            "drop_rate": vr.drop_rate,
            "ambiguous_spans": vr.ambiguous_spans,
            "ambiguity_rate": vr.ambiguity_rate,
            "errors": vr.errors[:20],
        }
        return vr.ok, report

    if tier3_enabled:
        report["tier3_validate"] = {"ok": False, "status": "no_candidates_file"}
        return False, report

    report["tier3_validate"] = {"ok": True, "status": "no_candidates_file"}
    return True, report


def main() -> None:
    import argparse
    p = argparse.ArgumentParser(description="AXM Tier 3 doctor check")
    p.add_argument("--out-dir", required=True, help="Directory with source.txt + pipeline artifacts")
    p.add_argument("--validation-only", action="store_true")
    args = p.parse_args()

    ok, report = run_tier3_doctor(Path(args.out_dir), validation_only=args.validation_only)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
