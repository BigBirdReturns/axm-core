from __future__ import annotations

from enum import Enum
try:
    import pyarrow as pa  # type: ignore
except Exception:
    pa = None  # type: ignore


class ErrorCode(str, Enum):
    E_LAYOUT_DIRTY = "E_LAYOUT_DIRTY"
    E_LAYOUT_MISSING = "E_LAYOUT_MISSING"
    E_LAYOUT_TYPE = "E_LAYOUT_TYPE"
    E_DOTFILE = "E_DOTFILE"
    E_MANIFEST_SYNTAX = "E_MANIFEST_SYNTAX"
    E_MANIFEST_SCHEMA = "E_MANIFEST_SCHEMA"
    E_SIG_MISSING = "E_SIG_MISSING"
    E_SIG_INVALID = "E_SIG_INVALID"
    E_MERKLE_MISMATCH = "E_MERKLE_MISMATCH"
    E_SCHEMA_READ = "E_SCHEMA_READ"
    E_SCHEMA_MISSING = "E_SCHEMA_MISSING"
    E_SCHEMA_TYPE = "E_SCHEMA_TYPE"
    E_SCHEMA_NULL = "E_SCHEMA_NULL"
    E_SCHEMA_ENUM = "E_SCHEMA_ENUM"
    E_ID_ENTITY = "E_ID_ENTITY"
    E_ID_CLAIM = "E_ID_CLAIM"
    E_REF_ORPHAN = "E_REF_ORPHAN"
    E_REF_SOURCE = "E_REF_SOURCE"
    E_REF_READ = "E_REF_READ"

# Strict schemas (types must match exactly)
if pa is not None:
    ENTITIES_SCHEMA = pa.schema([
        ("entity_id", pa.string()),
        ("namespace", pa.string()),
        ("label", pa.string()),
        ("entity_type", pa.string()),
    ])

    CLAIMS_SCHEMA = pa.schema([
        ("claim_id", pa.string()),
        ("subject", pa.string()),
        ("predicate", pa.string()),
        ("object", pa.string()),
        ("object_type", pa.string()),
        ("tier", pa.int8()),
    ])

    PROVENANCE_SCHEMA = pa.schema([
        ("provenance_id", pa.string()),
        ("claim_id", pa.string()),
        ("source_hash", pa.string()),
        ("byte_start", pa.int64()),
        ("byte_end", pa.int64()),
    ])

    SPANS_SCHEMA = pa.schema([
        ("span_id", pa.string()),
        ("source_hash", pa.string()),
        ("byte_start", pa.int64()),
        ("byte_end", pa.int64()),
        ("text", pa.string()),
    ])
else:
    # Fallback schemas when pyarrow is unavailable. Types use DuckDB-style strings.
    ENTITIES_SCHEMA = [
        ("entity_id", "VARCHAR"),
        ("namespace", "VARCHAR"),
        ("label", "VARCHAR"),
        ("entity_type", "VARCHAR"),
    ]
    CLAIMS_SCHEMA = [
        ("claim_id", "VARCHAR"),
        ("subject", "VARCHAR"),
        ("predicate", "VARCHAR"),
        ("object", "VARCHAR"),
        ("object_type", "VARCHAR"),
        ("tier", "TINYINT"),
    ]
    PROVENANCE_SCHEMA = [
        ("provenance_id", "VARCHAR"),
        ("claim_id", "VARCHAR"),
        ("source_hash", "VARCHAR"),
        ("byte_start", "BIGINT"),
        ("byte_end", "BIGINT"),
    ]
    SPANS_SCHEMA = [
        ("span_id", "VARCHAR"),
        ("source_hash", "VARCHAR"),
        ("byte_start", "BIGINT"),
        ("byte_end", "BIGINT"),
        ("text", "VARCHAR"),
    ]


VALID_OBJECT_TYPES = {
    "entity",
    "literal:string",
    "literal:integer",
    "literal:decimal",
    "literal:boolean",
}

VALID_TIERS = {0, 1, 2, 3, 4}

REQUIRED_ROOT_ITEMS = {
    "manifest.json",
    "sig",
    "content",
    "graph",
    "evidence",
}

REQUIRED_SIG_FILES = {"manifest.sig", "publisher.pub"}
REQUIRED_GRAPH_FILES = {"entities.parquet", "claims.parquet", "provenance.parquet"}
REQUIRED_EVIDENCE_FILES = {"spans.parquet"}

# Legacy aliases (used by older code paths)
PUBKEY_LEN = 32
SIG_LEN = 64

# Suite-aware key/sig sizes (v1.1+)
KNOWN_SUITES = {"ed25519", "axm-blake3-mldsa44"}

SUITE_SIZES = {
    "ed25519": {"pk": 32, "sig": 64},
    "axm-blake3-mldsa44": {"pk": 1312, "sig": 2420},
}
