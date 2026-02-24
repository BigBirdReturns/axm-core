"""
Coords Derivation Pass — ext/coords.parquet (coords@1)

Assigns 8-category semantic coordinates to entities extracted into compiled shards.
Reads the compiled shard's graph/entities.parquet, classifies each entity label
into the MM-TT-SS coordinate space, writes ext/coords.parquet.

Coordinate schema (from axm-kg coords.py, frozen at v0.5):
  Major categories:
    1=Entity, 2=Action, 3=Property, 4=Relation,
    5=Location, 6=Time, 7=Quantity, 8=Abstract

  Format: entity_id, major (str), type (str), subtype (str), instance (str)
  Joins to entities.parquet via entity_id.

Can run on a compiled shard directory (after Genesis compilation)
or on entities.parquet directly.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Classification rules (keyword → (major, type, subtype))
# Adapted from intake.py XBRL CONCEPT_COORDS + coords.py Major enum
# ---------------------------------------------------------------------------

_ENTITY_KW = {
    "organization", "company", "corp", "inc", "llc", "ltd", "agency",
    "department", "ministry", "bureau", "unit", "force", "command",
    "person", "individual", "personnel", "officer", "soldier", "patient",
    "dr", "mr", "mrs", "ms", "gen", "col", "maj", "sgt", "pvt", "lt",
    "product", "drug", "medication", "device", "system", "platform",
    "document", "report", "manual", "regulation", "directive", "order",
}

_ACTION_KW = {
    "event", "operation", "attack", "procedure", "treatment", "process",
    "transaction", "transfer", "payment", "purchase", "sale", "decision",
    "announcement", "mandate", "requirement",
}

_LOCATION_KW = {
    "city", "country", "region", "area", "zone", "sector", "district",
    "location", "address", "coordinate", "grid", "position", "site",
    "hospital", "facility", "base", "installation",
    "france", "paris", "london", "berlin", "tokyo", "beijing", "moscow",
    "usa", "uk", "germany", "japan", "china", "russia", "canada", "australia",
    "street", "avenue", "road", "highway", "port", "harbor", "airport",
}

_TIME_KW = {
    "date", "time", "year", "month", "quarter", "period",
    "timestamp", "duration", "interval", "fiscal", "as of",
}

_QUANTITY_KW = {
    "amount", "total", "count", "number", "rate", "ratio", "percent",
    "revenue", "cost", "price", "earnings", "income", "loss", "profit",
    "dosage", "dose", "volume", "weight", "measure", "score",
}

_ABSTRACT_KW = {
    "claim", "fact", "statement", "concept", "theory", "opinion",
    "belief", "narrative", "hypothesis", "assertion", "conclusion",
    "policy", "doctrine", "rule", "constraint", "roe",
}

# Financial XBRL concept → (major, type, subtype) direct map
_XBRL_MAP: Dict[str, Tuple[int, int, int]] = {
    "assets": (7, 1, 1), "currentassets": (7, 1, 2),
    "cash": (7, 1, 3), "cashandcashequivalents": (7, 1, 3),
    "inventory": (7, 1, 4), "accountsreceivable": (7, 1, 5),
    "liabilities": (7, 1, 10), "currentliabilities": (7, 1, 11),
    "longtermdebt": (7, 1, 12), "accountspayable": (7, 1, 13),
    "stockholdersequity": (7, 1, 20), "retainedearnings": (7, 1, 21),
    "revenues": (7, 2, 1), "revenue": (7, 2, 1),
    "costofrevenue": (7, 2, 10), "grossprofit": (7, 2, 11),
    "operatingincome": (7, 2, 13), "netincome": (7, 2, 20),
}

_MAJOR_NAMES = {1: "Entity", 2: "Action", 3: "Property",
                4: "Relation", 5: "Location", 6: "Time",
                7: "Quantity", 8: "Abstract"}

_TYPE_NAMES = {
    (1, 1): "Organization", (1, 2): "Person", (1, 3): "Product",
    (1, 4): "Service", (1, 5): "Document", (1, 6): "System",
    (2, 1): "Event", (2, 2): "Transaction", (2, 3): "Process",
    (2, 4): "Announcement", (2, 5): "Decision",
    (3, 1): "Attribute", (3, 2): "State", (3, 3): "Feature",
    (5, 1): "Address", (5, 2): "City", (5, 3): "Region", (5, 4): "Country",
    (6, 1): "Date", (6, 2): "Period", (6, 3): "Timestamp",
    (7, 1): "Financial", (7, 2): "Revenue", (7, 3): "Count", (7, 4): "Measure",
    (8, 1): "Claim", (8, 2): "Opinion", (8, 3): "Narrative", (8, 4): "Concept",
}


def _classify_label(label: str) -> Tuple[int, int, int]:
    """Classify an entity label into (major, type, subtype)."""
    norm = label.lower().strip()
    # Check XBRL map first (financial concepts are exact)
    key = re.sub(r"[^a-z]", "", norm)
    if key in _XBRL_MAP:
        return _XBRL_MAP[key]

    words = set(re.split(r"\W+", norm))

    if words & _TIME_KW:
        return (6, 1, 1)
    if words & _QUANTITY_KW or re.search(r"\$|%|usd|eur|\d+\s*(mg|kg|ml|g|lb)", norm):
        return (7, 3, 1)
    if words & _LOCATION_KW:
        return (5, 3, 1)
    if words & _ACTION_KW:
        return (2, 1, 1)
    if words & _ABSTRACT_KW:
        return (8, 1, 1)
    if words & _ENTITY_KW:
        # Distinguish person vs org
        if words & {"person", "individual", "personnel", "officer", "soldier", "patient"}:
            return (1, 2, 1)
        return (1, 1, 1)

    # Default: treat as an abstract concept (claim-like)
    return (8, 4, 1)


def run_coords_pass(shard_dir: Path) -> Dict[str, Any]:
    """
    Read compiled shard's graph/entities.parquet, assign coords,
    write ext/coords.parquet.

    Returns stats dict.
    """
    entities_path = shard_dir / "graph" / "entities.parquet"
    if not entities_path.exists():
        return {"rows": 0, "written": False, "reason": "entities.parquet not found"}

    try:
        import duckdb
        con = duckdb.connect()
        rows_raw = con.execute(
            f"SELECT entity_id, label FROM read_parquet('{entities_path}')"
        ).fetchall()
        con.close()
    except Exception as e:
        return {"rows": 0, "written": False, "reason": str(e)}

    if not rows_raw:
        return {"rows": 0, "written": False, "reason": "no entities"}

    # Count instances per (major, type, subtype) for the instance counter
    instance_counters: Dict[Tuple[int, int, int], int] = defaultdict(int)
    coord_rows = []

    for entity_id, label in rows_raw:
        m, t, s = _classify_label(label or "")
        instance_counters[(m, t, s)] += 1
        inst = instance_counters[(m, t, s)]

        major_name = _MAJOR_NAMES.get(m, str(m))
        type_name = _TYPE_NAMES.get((m, t), str(t))
        subtype_name = str(s)
        instance_str = f"{m:02d}-{t:02d}-{s:02d}-{inst:04d}"

        coord_rows.append({
            "entity_id": entity_id,
            "major": major_name,
            "type": type_name,
            "subtype": subtype_name,
            "instance": instance_str,
        })

    ext_dir = shard_dir / "ext"
    ext_dir.mkdir(exist_ok=True)
    out_path = ext_dir / "coords.parquet"
    _write_parquet(out_path, coord_rows)

    return {"rows": len(coord_rows), "written": True, "path": str(out_path)}


def _write_parquet(path: Path, rows: List[Dict[str, Any]]) -> None:
    import duckdb
    con = duckdb.connect()
    con.execute("""
        CREATE TABLE coords (
            entity_id VARCHAR,
            major VARCHAR,
            type VARCHAR,
            subtype VARCHAR,
            instance VARCHAR
        )
    """)
    for r in rows:
        con.execute(
            "INSERT INTO coords VALUES (?, ?, ?, ?, ?)",
            [r["entity_id"], r["major"], r["type"], r["subtype"], r["instance"]],
        )
    con.execute(f"COPY coords TO '{path}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    con.close()
