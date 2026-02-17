# AXM Extensions Registry

Normative definitions for ext/ parquet files. Each extension has a name, version,
Arrow schema, sort key, stable join strategy, and consumer contract.

## Versioning

Extensions use `name@version` format in manifest.json. Version is integer.
Breaking schema changes increment version. New versions are new extensions.

## Identity Rules (see also INVARIANTS.md)

All extension join keys must be derivable from content, not internal IDs.
This ensures joins survive shard rebuilds where internal IDs may change.

Stable anchors (safe to join on):
- `evidence_addr` = hash(source_hash + byte_start + byte_end)
- `shard_id` = content-addressed from Merkle root
- `claim_id` = deterministic from claim content
- `entity_id` = deterministic from namespace + label
- `source_hash` = SHA-256 of content bytes

Unstable anchors (DO NOT use as sole join key):
- `provenance_id` — may change if provenance is split/merged
- `span_id` — depends on evidence text, acceptable as secondary key

---

## locators@1

**File:** `ext/locators.parquet`
**Purpose:** Structural position of evidence in source documents.
**Producer:** Genesis compiler (reads `locator` dict from candidates.jsonl)
**Consumer:** Spectra (answers "what page did this claim come from?")

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| evidence_addr | string | no | Stable join key: hash(source_hash + byte_start + byte_end) |
| span_id | string | no | Link to spans.parquet |
| source_hash | string | no | Content hash |
| kind | string | no | pdf, docx, html, txt, pptx, xlsx |
| page_index | int16 | yes | 0-indexed page number |
| paragraph_index | int32 | yes | Paragraph index within page/document |
| block_id | string | no | Section/div identifier (empty if N/A) |
| file_path | string | no | Original filename |

**Sort key:** evidence_addr
**Validation:** evidence_addr must be unique. span_id must exist in spans.parquet.

---

## references@1

**File:** `ext/references.parquet`
**Purpose:** Cross-shard claim references. Enables composition and decision trails.
**Producer:** Decision shard compiler, multi-source ingestion
**Consumer:** Spectra (multi-shard queries, reference integrity checks)

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| src_claim_id | string | no | Claim in THIS shard making the reference |
| relation_type | string | no | supports, contradicts, derives_from, supersedes, cites |
| dst_shard_id | string | no | Target shard ID |
| dst_object_type | string | no | claim, entity, or shard |
| dst_object_id | string | no | Target claim_id, entity_id, or shard_id |
| confidence | float32 | no | 0.0-1.0 |
| note | string | yes | Human-readable annotation |

**Sort key:** src_claim_id
**Validation:** src_claim_id must exist in claims.parquet.
**Integrity rule:** If dst_shard_id is mounted in Spectra, target must exist or ref is "broken."

---

## lineage@1

**File:** `ext/lineage.parquet`
**Purpose:** Shard versioning. Which shards this one supersedes.
**Producer:** Compiler when building delta/update shards
**Consumer:** Spectra (shard selection in C(Q)), version chain traversal

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| shard_id | string | no | THIS shard's ID |
| supersedes_shard_id | string | no | Shard being superseded |
| action | string | no | supersede, amend, retract |
| timestamp | string | no | ISO 8601 |
| note | string | yes | Context |

**Sort key:** shard_id
**Manifest hint:** Also add `"supersedes": ["shard_id_1", ...]` to manifest for cheap discovery.

---

## temporal@1

**File:** `ext/temporal.parquet`
**Purpose:** Claim validity windows. When knowledge expires.
**Producer:** Compiler with temporal metadata from source
**Consumer:** Spectra (staleness detection, time-scoped queries)

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| claim_id | string | no | Claim this applies to |
| valid_from | string | yes | ISO 8601 or empty for "always" |
| valid_until | string | yes | ISO 8601 or empty for "until superseded" |
| temporal_context | string | yes | e.g. "valid until Army FM revision" |

**Sort key:** claim_id

---

## coords@1

**File:** `ext/coords.parquet`
**Purpose:** Semantic coordinate space (MM-TT-SS-XXXX from deprecated AXM-KG)
**Producer:** Coordinate assignment pipeline
**Consumer:** Spectra (geometric queries, pathfinding)

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| entity_id | string | no | Entity this applies to |
| major | string | no | Major category |
| type | string | no | Type within major |
| subtype | string | no | Subtype |
| instance | string | no | Instance identifier |

**Sort key:** entity_id

---

## Dependency Order

```
locators@1     (no dependencies — first extension, proves the envelope)
references@1   (no dependencies — enables composition)
lineage@1      (no dependencies — enables versioning)
temporal@1     (no dependencies — enables staleness)
coords@1       (no dependencies — enables geometric queries)
spatial@1      (future — depends on locators@1 for structural position)
```
