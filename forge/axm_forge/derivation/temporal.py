"""
Temporal Derivation Pass

Reads candidates.jsonl, finds claims with date/time values,
writes ext/temporal.parquet: {claim_id, valid_from, valid_until, temporal_context}.

Adapted from axm-kg derive/temporal.py — stripped of the old Program/Coord
dependency, operates directly on Genesis candidates.jsonl + compiled claims.

Schema: ext/temporal.parquet (temporal@1)
  claim_id         string  — joins to claims.parquet
  valid_from       string  — ISO 8601 or empty
  valid_until      string  — ISO 8601 or empty
  temporal_context string  — human-readable note
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

_DATE_PATTERNS = [
    r"\d{4}-\d{2}-\d{2}",
    r"\d{2}/\d{2}/\d{4}",
    r"\d{2}-\d{2}-\d{4}",
    r"\d{4}/\d{2}/\d{2}",
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}",
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
]
_DATE_RE = re.compile("|".join(f"(?:{p})" for p in _DATE_PATTERNS))

_TIME_LABELS = {"date", "time", "timestamp", "as of", "period",
                "quarter", "year", "effective", "expires", "valid"}


def _parse_iso(s: str) -> Optional[str]:
    s = s.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s + "T00:00:00Z"
    fmts = [
        "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y",
    ]
    for fmt in fmts:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            pass
    return None


def _is_temporal(predicate: str, obj: str) -> bool:
    pred_l = predicate.lower()
    if any(kw in pred_l for kw in _TIME_LABELS):
        return True
    if _DATE_RE.search(obj or ""):
        return True
    return False


def _extract_date(text: str) -> Optional[str]:
    m = _DATE_RE.search(text or "")
    if m:
        return _parse_iso(m.group(0))
    return None


def run_temporal_pass(
    candidates_path: Path,
    shard_dir: Path,
    *,
    claim_id_map: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Scan candidates.jsonl for temporal claims.
    Writes ext/temporal.parquet if any found.

    claim_id_map: optional {candidate_key -> compiled_claim_id}
    If not provided, uses the candidate's own identity fields to build a stable key.

    Returns stats dict.
    """
    rows: List[Dict[str, Any]] = []

    with candidates_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            c = json.loads(line)
            pred = c.get("predicate", "")
            obj = str(c.get("object", ""))
            ev = c.get("evidence", "")

            if not _is_temporal(pred, obj):
                continue

            valid_from = _extract_date(obj) or _extract_date(ev) or ""
            temporal_context = f"{pred}: {obj}"

            # Derive a claim_id key consistent with Genesis identity rules
            # (subject + predicate + object + byte range → hash)
            import hashlib
            key = json.dumps({
                "subject": c.get("subject", ""),
                "predicate": pred,
                "object": obj,
                "byte_start": c.get("byte_start", 0),
                "byte_end": c.get("byte_end", 0),
            }, sort_keys=True)
            claim_id = "claim_" + hashlib.sha256(key.encode()).hexdigest()[:16]
            if claim_id_map:
                claim_id = claim_id_map.get(claim_id, claim_id)

            rows.append({
                "claim_id": claim_id,
                "valid_from": valid_from,
                "valid_until": "",
                "temporal_context": temporal_context,
            })

    if not rows:
        return {"temporal_rows": 0, "written": False}

    ext_dir = shard_dir / "ext"
    ext_dir.mkdir(exist_ok=True)
    out_path = ext_dir / "temporal.parquet"
    _write_parquet(out_path, rows)

    return {"temporal_rows": len(rows), "written": True, "path": str(out_path)}


def _write_parquet(path: Path, rows: List[Dict[str, Any]]) -> None:
    try:
        import duckdb
        con = duckdb.connect()
        con.execute("""
            CREATE TABLE temporal (
                claim_id VARCHAR,
                valid_from VARCHAR,
                valid_until VARCHAR,
                temporal_context VARCHAR
            )
        """)
        for r in rows:
            con.execute(
                "INSERT INTO temporal VALUES (?, ?, ?, ?)",
                [r["claim_id"], r["valid_from"], r["valid_until"], r["temporal_context"]],
            )
        con.execute(f"COPY temporal TO '{path}' (FORMAT PARQUET, COMPRESSION ZSTD)")
        con.close()
    except Exception as e:
        raise RuntimeError(f"Failed to write temporal.parquet: {e}") from e
