"""
Spectra Shard Diff — compare two mounted Genesis shards.

Queries the Spectra engine's mounted DuckDB views to produce
an added/removed/modified diff of claims and entities between
two shard mounts. Useful for delta shard auditing.

Also exposes a static pack diff for constraint_pack_v1 JSONL packs
(ported from SOCOM tools/pack_diff/pack_diff.py).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Live shard diff — uses Spectra engine DuckDB views
# ---------------------------------------------------------------------------

def diff_mounted_shards(
    engine: Any,
    base_mount_prefix: str,
    delta_mount_prefix: str,
) -> Dict[str, Any]:
    """
    Diff claims and entities between two mounted shards.

    base_mount_prefix, delta_mount_prefix: the mount_prefix values used
    when calling engine.mount_shard().

    Returns:
        {
            "claims": {"added": [...], "removed": [...], "modified": [...]},
            "entities": {"added": [...], "removed": [...], "modified": [...]},
        }
    """
    cat = engine.catalog_json()
    mounts = {m["mount_prefix"]: m for m in cat.get("mounts", [])}

    if base_mount_prefix not in mounts:
        return {"error": f"base mount '{base_mount_prefix}' not found"}
    if delta_mount_prefix not in mounts:
        return {"error": f"delta mount '{delta_mount_prefix}' not found"}

    base_tables = {t.split("__")[0]: t for t in mounts[base_mount_prefix].get("tables", [])}
    delta_tables = {t.split("__")[0]: t for t in mounts[delta_mount_prefix].get("tables", [])}

    result: Dict[str, Any] = {}

    for table_base_name, id_col in [("claims", "claim_id"), ("entities", "entity_id")]:
        base_view = base_tables.get(table_base_name)
        delta_view = delta_tables.get(table_base_name)
        if not base_view or not delta_view:
            result[table_base_name] = {"error": "table not found in one or both mounts"}
            continue

        try:
            base_sql = f'SELECT {id_col} FROM "{base_view}"'
            delta_sql = f'SELECT {id_col} FROM "{delta_view}"'
            base_ids = {r[0] for r in engine.query_json(base_sql).get("rows", [])}
            delta_ids = {r[0] for r in engine.query_json(delta_sql).get("rows", [])}

            added = sorted(delta_ids - base_ids)
            removed = sorted(base_ids - delta_ids)

            # Modified: same ID, different content hash
            common = base_ids & delta_ids
            modified = []
            if common:
                # Use a content hash to detect modifications
                # Compare by joining on id and checking all columns
                hash_sql = f"""
                    SELECT b.{id_col}
                    FROM "{base_view}" b
                    JOIN "{delta_view}" d ON b.{id_col} = d.{id_col}
                    WHERE b.{id_col} IN ({','.join(f"'{i}'" for i in list(common)[:500])})
                      AND b != d
                """
                try:
                    modified = sorted(
                        r[0] for r in engine.query_json(hash_sql).get("rows", []))
                except Exception:
                    modified = []  # best-effort

            result[table_base_name] = {
                "added": added[:200],
                "removed": removed[:200],
                "modified": modified[:200],
                "added_count": len(added),
                "removed_count": len(removed),
                "modified_count": len(modified),
            }
        except Exception as e:
            result[table_base_name] = {"error": str(e)}

    return result


# ---------------------------------------------------------------------------
# Static pack diff — operates on JSONL files (for constraint packs)
# Ported from SOCOM tools/pack_diff/pack_diff.py
# ---------------------------------------------------------------------------

def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _index_by_id(items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {it.get("id"): it for it in items if it.get("id")}


def diff_packs(base: Path, delta: Path) -> Dict[str, Any]:
    """Diff two constraint_pack_v1 directories (concepts.jsonl, relations.jsonl)."""
    base_c = _index_by_id(_load_jsonl(base / "concepts.jsonl"))
    delta_c = _index_by_id(_load_jsonl(delta / "concepts.jsonl"))
    base_r = _index_by_id(_load_jsonl(base / "relations.jsonl"))
    delta_r = _index_by_id(_load_jsonl(delta / "relations.jsonl"))

    def _diff(a: Dict, b: Dict) -> Dict[str, List[str]]:
        ak, bk = set(a), set(b)
        return {
            "added": sorted(bk - ak),
            "removed": sorted(ak - bk),
            "modified": sorted(k for k in ak & bk if a[k] != b[k]),
        }

    return {
        "base": str(base),
        "delta": str(delta),
        "concepts": _diff(base_c, delta_c),
        "relations": _diff(base_r, delta_r),
    }
