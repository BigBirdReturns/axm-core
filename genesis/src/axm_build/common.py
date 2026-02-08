from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Dict, List

try:
    import pyarrow as pa  # type: ignore
    import pyarrow.parquet as pq  # type: ignore
except Exception:
    pa = None  # type: ignore
    pq = None  # type: ignore


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def write_parquet_deterministic(
    path: Path,
    rows: List[Dict[str, Any]],
    schema: Any,
    sort_key: str,
) -> None:
    """Write a deterministic Parquet file.

    Preferred implementation uses pyarrow (exact schemas, stable ordering).
    Fallback uses DuckDB to write Parquet when pyarrow is unavailable.
    """

    rows_sorted = sorted(rows, key=lambda r: r[sort_key])
    path.parent.mkdir(parents=True, exist_ok=True)

    if pa is not None and pq is not None:
        table = pa.Table.from_pylist(rows_sorted, schema=schema)
        writer = pq.ParquetWriter(
            where=str(path),
            schema=schema,
            compression="NONE",
            use_dictionary=False,
            write_statistics=False,
        )
        try:
            writer.write_table(table)
        finally:
            writer.close()
        return

    # DuckDB fallback (no pyarrow). Keep column order deterministic.
    import duckdb
    import pandas as pd

    if isinstance(schema, list):
        cols = [c for c, _t in schema]
    else:
        # Best-effort for unexpected schema objects.
        cols = list(rows_sorted[0].keys()) if rows_sorted else []

    df = pd.DataFrame(rows_sorted)
    if cols:
        for c in cols:
            if c not in df.columns:
                df[c] = None
        df = df[cols]
    con = duckdb.connect(database=":memory:")
    con.register("df", df)
    # Uncompressed parquet for closer parity.
    con.execute(
        f"COPY (SELECT * FROM df ORDER BY {sort_key}) TO '{str(path)}' (FORMAT PARQUET, COMPRESSION 'UNCOMPRESSED')"
    )
    con.close()


def normalize_source_text(s: str) -> str:
    """Canonicalize extracted source text.

    Goals:
    - Normalize newlines to LF.
    - Strip trailing whitespace per line.
    - Unwind common PDF soft-wrap artifacts conservatively.
    - Preserve headings and list structure as boundaries.

    This function must stay stable because shard byte offsets depend on it.
    """

    s = s.replace("\r\n", "\n").replace("\r", "\n")
    raw_lines = [ln.rstrip() for ln in s.split("\n")]

    while raw_lines and raw_lines[0] == "":
        raw_lines.pop(0)
    while raw_lines and raw_lines[-1] == "":
        raw_lines.pop()

    out: List[str] = []
    i = 0
    while i < len(raw_lines):
        line = raw_lines[i]

        if line == "":
            # Preserve a single blank line, with one special merge case:
            # If the next non-blank line is a continuation (lowercase/digit)
            # and the previous line does not look like sentence end.
            j = i + 1
            while j < len(raw_lines) and raw_lines[j] == "":
                j += 1

            if out and j < len(raw_lines):
                prev = out[-1]
                nxt = raw_lines[j].lstrip()
                if prev and prev[-1] not in ".:;!?)" and (nxt[:1].islower() or nxt[:1].isdigit()):
                    out[-1] = prev + " " + nxt
                    i = j + 1
                    continue

            if not out or out[-1] != "":
                out.append("")
            i += 1
            continue

        buf = line
        i += 1

        # Soft-wrap merge: keep merging until we hit a boundary.
        while i < len(raw_lines):
            nxt = raw_lines[i]
            if nxt == "":
                break

            # hyphenation join
            if buf.endswith("-"):
                buf = buf[:-1] + nxt.lstrip()
                i += 1
                continue

            looks_like_heading = buf.isupper() or buf.endswith(":")
            looks_like_list = (
                nxt.strip().startswith(("-", "*"))
                or re.match(r"^\(?\d+\)?\.?\s+", nxt) is not None
            )
            if looks_like_heading or looks_like_list:
                break

            buf = buf + " " + nxt.lstrip()
            i += 1

        out.append(buf)

    # Collapse duplicate blank lines
    cleaned: List[str] = []
    for ln in out:
        if ln == "" and cleaned and cleaned[-1] == "":
            continue
        cleaned.append(ln)

    normalized = "\n".join(cleaned) + "\n"

    # Legacy gold shard fixes that must remain stable.
    normalized = normalized.replace("pi'essure", "pressure").replace("pi’essure", "pressure")
    normalized = normalized.replace("bleed-\ning", "bleeding")

    return normalized
