"""
Temporal Derivation Pass

Detects claims with date/time values in candidates.jsonl.

Genesis v1 path (canonical): `annotate_temporal_candidates` adds
valid_from / valid_until / temporal_context keys to the candidate rows
in place, BEFORE compilation. The v1 compiler
(axm_build.compiler_generic) reads those keys and emits the sealed
ext/temporal@1.jsonl itself — nothing is ever injected into the shard
directory from outside the compiler.

`run_temporal_pass` (legacy) writes a derived temporal.parquet for local
runtime caches only. Its output must live OUTSIDE any shard directory:
v1 shards carry no Parquet, and their ext/ tables are compiler-emitted
canonical JSONL.

temporal@1 schema (ext/temporal@1.jsonl):
  claim_id         string  — joins to graph/claims.jsonl
  valid_from       string  — RFC 3339 or empty
  valid_until      string  — RFC 3339 or empty
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


def annotate_temporal_candidates(candidates_path: Path) -> int:
    """Annotate temporal candidates in place for the v1 Genesis compiler.

    Rewrites candidates.jsonl, adding valid_from / valid_until /
    temporal_context keys to every candidate whose predicate or object
    looks temporal. The compiler turns those keys into the sealed
    ext/temporal@1.jsonl (one row per claim_id).

    Returns the number of candidates annotated.
    """
    candidates_path = Path(candidates_path)
    if not candidates_path.exists():
        return 0

    rows: List[Dict[str, Any]] = []
    annotated = 0
    with candidates_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            c = json.loads(line)
            pred = str(c.get("predicate", "")).strip()
            obj = str(c.get("object", "")).strip()
            if pred and _is_temporal(pred, obj) and not (
                c.get("valid_from") or c.get("valid_until") or c.get("temporal_context")
            ):
                valid_from = _extract_date(obj) or _extract_date(str(c.get("evidence", ""))) or ""
                c["valid_from"] = valid_from
                c["valid_until"] = ""
                c["temporal_context"] = f"{pred}: {obj}"
                annotated += 1
            rows.append(c)

    if annotated:
        with candidates_path.open("w", encoding="utf-8") as f:
            for c in rows:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
    return annotated


def run_temporal_pass(
    candidates_path: Path,
    out_dir: Path,
    *,
    namespace: str,
    source_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Scan candidates.jsonl for temporal claims and write temporal.parquet
    into out_dir — a LOCAL DERIVED CACHE directory, never a shard: v1
    shards carry no Parquet, and sealed temporal rows come from
    `annotate_temporal_candidates` + the v1 compiler instead.

    namespace:   shard namespace — must match what is passed to the Genesis
                 compiler, since entity/claim IDs are namespace-scoped.
    source_path: raw merged source text. If given, candidates whose evidence
                 the Genesis compiler would drop (evidence not found exactly
                 once in the normalized content bytes) are skipped here too,
                 guaranteeing every emitted claim_id exists in the sealed
                 graph/claims.jsonl.

    Returns stats dict.
    """
    # INV-7/8/27: claim identity is owned by Genesis. Delegate, never reimplement.
    from axm_verify.identity import recompute_claim_id, recompute_entity_id
    try:
        from axm_verify.const import VALID_OBJECT_TYPES
    except ImportError:  # pragma: no cover — older genesis layouts
        from axm_build.schemas import VALID_OBJECT_TYPES

    content_bytes: Optional[bytes] = None
    if source_path is not None and Path(source_path).exists():
        from axm_build.common import normalize_source_text
        content_bytes = normalize_source_text(
            Path(source_path).read_text(encoding="utf-8")
        ).encode("utf-8")

    rows: List[Dict[str, Any]] = []
    seen_claim_ids = set()

    with Path(candidates_path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            c = json.loads(line)

            # Mirror axm_build.compiler_generic.compile_generic_shard's
            # candidate handling exactly so claim_ids line up 1:1.
            subj_label = str(c.get("subject", "")).strip()
            pred = str(c.get("predicate", "")).strip()
            obj = str(c.get("object", "")).strip()
            evidence = c.get("evidence") or c.get("evidence_quote")

            if not subj_label or not pred or not evidence:
                continue  # compiler skips these
            obj_type = c.get("object_type", "entity")
            if obj_type not in VALID_OBJECT_TYPES:
                continue  # compiler skips these

            if not _is_temporal(pred, obj):
                continue

            if content_bytes is not None:
                # Compiler drops candidates whose evidence is not found
                # (and aborts the whole build on ambiguous evidence, in
                # which case no shard exists at all) — only emit temporal
                # rows for claims that will actually be sealed.
                if content_bytes.count(str(evidence).encode("utf-8")) != 1:
                    continue

            subj_id = recompute_entity_id(namespace, subj_label)
            obj_val = recompute_entity_id(namespace, obj) if obj_type == "entity" else obj
            claim_id = recompute_claim_id(subj_id, pred, obj_val, obj_type)

            if claim_id in seen_claim_ids:
                continue
            seen_claim_ids.add(claim_id)

            valid_from = _extract_date(obj) or _extract_date(str(evidence)) or ""
            temporal_context = f"{pred}: {obj}"

            rows.append({
                "claim_id": claim_id,
                "valid_from": valid_from,
                "valid_until": "",
                "temporal_context": temporal_context,
            })

    if not rows:
        return {"temporal_rows": 0, "written": False}

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    # Derived cache only — this file must stay outside any shard directory.
    out_path = out_dir / "temporal.parquet"
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
