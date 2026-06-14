"""
Coords Derivation Pass — ext/coords.parquet (coords@1)

Assigns 8-category semantic coordinates to entities destined for compiled shards.
Reads candidates.jsonl (pre-compile), recomputes the Genesis entity IDs the
compiler will assign, classifies each entity label into the MM-TT-SS
coordinate space, and writes coords.parquet into a staging directory that the
compile step injects into the shard's ext/ before sealing.

Coordinate schema (from axm-kg coords.py, frozen at v0.5):
  Major categories:
    1=Entity, 2=Action, 3=Property, 4=Relation,
    5=Location, 6=Time, 7=Quantity, 8=Abstract

  Format: entity_id, major (str), type (str), subtype (str), instance (str)
  Joins to graph/entities.parquet via entity_id (Genesis IDs, see
  axm_verify.identity.recompute_entity_id).
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


def run_coords_pass(
    candidates_path: Path,
    *,
    namespace: str,
    out_dir: Path,
) -> Dict[str, Any]:
    """
    Derive entity coordinates BEFORE Genesis compilation, writing
    coords.parquet into out_dir (a staging directory; the compile step
    injects it into the shard's ext/ before the Merkle root is computed,
    so the extension is covered by the seal).

    Entity IDs are recomputed exactly as the Genesis compiler computes them
    (axm_verify.identity.recompute_entity_id over the same labels its
    entity pass collects from candidates.jsonl), so every entity_id here
    matches the sealed graph/entities.parquet (INV-7/27 — Genesis owns
    identity, forge delegates).

    Returns stats dict.
    """
    # INV-7/27: entity identity is owned by Genesis. Delegate, never reimplement.
    from axm_verify.identity import recompute_entity_id

    candidates_path = Path(candidates_path)
    if not candidates_path.exists():
        return {"rows": 0, "written": False, "reason": "candidates.jsonl not found"}

    # Mirror axm_build.compiler_generic.compile_generic_shard pass 1 exactly:
    # every non-empty subject label, plus object labels of entity-typed objects.
    entities: Dict[str, str] = {}
    with candidates_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            c = json.loads(line)
            subj = str(c.get("subject", "")).strip()
            if not subj:
                continue
            entities[subj] = recompute_entity_id(namespace, subj)
            if c.get("object_type", "entity") == "entity":
                obj = str(c.get("object", "")).strip()
                if obj:
                    entities[obj] = recompute_entity_id(namespace, obj)

    if not entities:
        return {"rows": 0, "written": False, "reason": "no entities"}

    # Sort by entity_id to match the deterministic order of the sealed
    # graph/entities.parquet (instance counters stay reproducible).
    rows_raw = sorted(((eid, label) for label, eid in entities.items()),
                      key=lambda x: x[0])

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

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    # Bare on-disk filename: the genesis compiler derives the INV-29 manifest
    # extension name by appending @1 to the stem (coords -> coords@1). Writing
    # coords@1.parquet here would double it to coords@1@1. Matches how genesis
    # writes its own extensions (ext/locators.parquet, ext/temporal.parquet).
    out_path = out_dir / "coords.parquet"
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
