# AXM Extensions Registry

Definitions for `ext/` extension tables as consumed by this runtime. The
normative source for the kernel-registry extensions is
`axm_build/ext_schemas.py` in axm-genesis (spec section 16); this document
mirrors it for Spectra/Forge consumers.

## Format (Genesis v1)

Registry extensions are **canonical JSONL** files with the same encoding
discipline as the core tables: one canonical-JSON record per line (sorted
keys, no whitespace, no floats, no nulls), rows sorted bytewise ascending by
the sort key. `ext/` is opaque to the kernel verifier but fully
Merkle-covered — extension bytes are sealed and signed like everything else.

## Versioning and file naming

Extension identifiers use the `name@version` grammar
(`^[a-z][a-z0-9-]*@[1-9][0-9]*$`). The on-disk file is named
`ext/<name>@<version>.<suffix>` — the `@version` IS part of the filename:
`ext/temporal@1.jsonl`, `ext/lineage@1.jsonl`. A new version is a new
identifier; published extension schemas are frozen. When `ext/` is
non-empty, the manifest's `extensions` array must list every extension
identifier; when `ext/` is empty the field must be absent.

The reference compiler (`compile_generic_shard`) emits all four registry
extensions itself from candidate keys / config; nothing else may write into
a shard.

## Identity Rules (see also INVARIANTS.md)

All extension join keys must be derivable from content, not internal IDs.
This ensures joins survive shard rebuilds.

Stable anchors (safe to join on):
- `evidence_addr` = hash(source_hash + byte_start + byte_end)
- `claim_id` = deterministic from claim content
- `entity_id` = deterministic from namespace + label
- `source_hash` = SHA-256 of content bytes
- `sh1_` shard ids — derived from a manifest's bytes; inside extension
  tables they name only OTHER shards, never the containing shard (a shard's
  own id is ambient and appears nowhere in its own files)

Unstable anchors (DO NOT use as sole join key):
- `provenance_id` — may change if provenance is split/merged
- `span_id` — depends on evidence text, acceptable as secondary key

All values in registry extensions are JSON **strings** (canonical JSONL
forbids floats and nulls; optional integers are decimal strings, `""` when
unknown).

---

## locators@1

**File:** `ext/locators@1.jsonl`
**Purpose:** Structural position of evidence in source documents.
**Producer:** Genesis compiler (reads `locator` dict from candidates.jsonl)
**Consumer:** Spectra (answers "what page did this claim come from?")

| Key | Type | Description |
|-----|------|-------------|
| evidence_addr | string | Stable join key: hash(source_hash + byte_start + byte_end) |
| span_id | string | Link to evidence/spans.jsonl |
| source_hash | string | Content hash |
| kind | string | pdf, docx, html, txt, pptx, xlsx |
| page_index | string | decimal or "" when unknown |
| paragraph_index | string | decimal or "" when unknown |
| block_id | string | Section/div identifier ("" if N/A) |
| file_path | string | Original filename |

**Sort key:** evidence_addr

---

## references@1

**File:** `ext/references@1.jsonl`
**Purpose:** Cross-shard claim references. Enables composition and decision trails.
**Producer:** Genesis compiler (reads `references` list from candidates.jsonl)
**Consumer:** Spectra (multi-shard queries, reference integrity checks)

| Key | Type | Description |
|-----|------|-------------|
| src_claim_id | string | Claim in THIS shard making the reference |
| relation_type | string | supports, contradicts, derives_from, supersedes, cites |
| dst_shard_id | string | Target shard identity, `sh1_<64 hex>` form |
| dst_object_type | string | claim, entity, or shard |
| dst_object_id | string | Target claim_id, entity_id, or shard_id |
| confidence | string | decimal string in [0,1], e.g. "1.0" |
| note | string | Human-readable annotation ("" if none) |

**Sort key:** src_claim_id
**Integrity rule:** If dst_shard_id is mounted in Spectra, target must exist or ref is "broken."

---

## lineage@1

**File:** `ext/lineage@1.jsonl`
**Purpose:** Shard versioning. Which shards this one supersedes.
**Producer:** Genesis compiler (from `CompilerConfig.supersedes` / lineage_action / lineage_note)
**Consumer:** Spectra (shard selection, version chain traversal)

| Key | Type | Description |
|-----|------|-------------|
| supersedes_shard_id | string | Predecessor shard id, `sh1_` form |
| action | string | supersede, amend, retract |
| timestamp | string | RFC 3339 |
| note | string | Context ("" if none) |

**Sort key:** supersedes_shard_id
**No self-id column:** a shard's own id is the BLAKE3 hash of its manifest
and cannot appear in its own files; one Merkle pass suffices.
**Manifest hint:** the compiler also emits `"supersedes": ["sh1_...", ...]`
in the manifest for cheap discovery.

---

## temporal@1

**File:** `ext/temporal@1.jsonl`
**Purpose:** Claim validity windows. When knowledge expires.
**Producer:** Genesis compiler (reads valid_from / valid_until /
temporal_context keys from candidates.jsonl — Forge's
`annotate_temporal_candidates` adds them)
**Consumer:** Spectra (staleness detection, time-scoped queries)

| Key | Type | Description |
|-----|------|-------------|
| claim_id | string | Claim this applies to |
| valid_from | string | RFC 3339 or "" for "always" |
| valid_until | string | RFC 3339 or "" for "until superseded" |
| temporal_context | string | e.g. "valid until Army FM revision" |

**Sort key:** claim_id

---

## coords (runtime-derived cache — NOT a kernel-registry extension)

**File:** `<workdir>/derived/coords.parquet` — outside any shard
**Purpose:** Semantic coordinate space (MM-TT-SS-XXXX from deprecated AXM-KG)
**Producer:** `axm_forge.derivation.coords.run_coords_pass`
**Consumer:** local tooling (geometric queries, pathfinding)

v1 shards carry only compiler-emitted registry extensions, so coords data is
a rebuildable local artifact keyed by `entity_id` (the pass recomputes the
same Genesis entity IDs the compiler seals). It is never Merkle-covered and
never lives inside a shard.

| Column | Type | Description |
|--------|------|-------------|
| entity_id | string | Entity this applies to |
| major | string | Major category |
| type | string | Type within major |
| subtype | string | Subtype |
| instance | string | Instance identifier |

**Sort key:** entity_id
