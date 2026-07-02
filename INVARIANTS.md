# AXM Stack Invariants

Absolute constraints. Every code change, every LLM session, every PR must preserve these.
If a change violates an invariant, the change is wrong. No exceptions.

Stated against Genesis v1 (RFC 0002 — the v1.0 reset, `spec/v1/SPECIFICATION.md`,
the commit pinned in `pyproject.toml`). Everything shipped before v1 is the
v0.x prototype lineage: archived in git history, not verifiable by v1.
Enforcement pointers below name the tests/scripts in this repo that re-prove
the invariant by execution (`python -m pytest tests/ -q`, `python
scripts/doctor.py`, `python integration_test.py`).

---

## Genesis Core (Frozen)

**INV-1: The Genesis v1 kernel is frozen.**
The shard layout, the four canonical-JSONL core tables
(`graph/entities.jsonl`, `graph/claims.jsonl`, `graph/provenance.jsonl`,
`evidence/spans.jsonl` + `content/`), the canonical JSON encoding, the
Merkle tree, the `axm-hybrid1` signature suite, the manifest schema, and the
identifier derivations do not change. A shard compiled under any 1.x
verifies under every other 1.x.
*Proven by:* `scripts/doctor.py` (v2 gold shard verifies PASS) and
`tests/test_v1_mount.py::test_compile_verify_mount_roundtrip_with_ext_jsonl`.

**INV-2: Merkle root covers all files.**
Every file in the shard except `manifest.json` and `sig/` (i.e. all of
`content/`, `graph/`, `evidence/`, `ext/`) is in the BLAKE3 Merkle tree.
The hybrid signature covers the manifest. Tamper with any byte → shard rejected.
*Proven by:* `tests/test_v1_mount.py::test_mount_rejects_tampered_gold_copy`.

**INV-3: `ext/` is the only extension point.**
New data goes in `ext/`. Core directories (`content/`, `graph/`, `evidence/`,
`sig/`) are frozen. The verifier allows `ext/` at root, rejects all other
unknown items (`E_LAYOUT_DIRTY`).

**INV-4: Extensions are opaque to the kernel.**
The kernel verifier never reads `ext/` contents (profiles may). No core
functionality depends on `ext/` existing. When `ext/` is non-empty the
manifest's `extensions` array must list every extension identifier; when
empty the field must be absent.

**INV-5: Manifest `extensions` key is conditional.**
Empty `ext/` → no `extensions` key in the manifest (spec section 6.2).
Preserves hash stability.

---

## Identity (Deterministic)

**INV-6: `shard_id = "sh1_" + hex(BLAKE3(manifest_bytes))` — derived, never stored.**
A manifest containing a `shard_id` field is a verification error
(`E_MANIFEST_SCHEMA`). A shard never records its own id anywhere in its own
files; cross-shard references (supersedes, lineage@1, references@1) bind to
predecessor/foreign ids in `sh1_` form.
*Proven by:* `tests/test_v1_mount.py::test_mount_gold_v2_shard_and_query_claim`
(Spectra recomputes the id from manifest bytes and asserts `shard_id` is
absent from the manifest).

**INV-7: `entity_id = recompute_entity_id(namespace, label)`.** Full 32-byte
SHA-256, base32lower, `e1_` prefix. Stable across rebuilds.

**INV-8: `claim_id = recompute_claim_id(subject_id, predicate, object, object_type)`.**
Same shape, `c1_` prefix. Stable.

**INV-9: `evidence_addr = hash(source_hash + byte_start + byte_end)`.**
Stable join key for `ext/locators@1.jsonl`. Does NOT depend on `span_id` or
`provenance_id`.

**INV-10: `span_id = hash(source_hash + byte_range + evidence_text)`.** Stable
(`s1_` prefix; the kernel checks syntax/uniqueness, the reference compiler
derives it this way).

---

## Forge Ingestion

**INV-11: Documents are never flattened.**
Extractors return DocumentBlocks with Locators. PDF pages → separate blocks.
Locators survive: extractor → candidates.jsonl → compiler → `ext/locators@1.jsonl` → Spectra.

**INV-12: Structured data never touches an LLM.**
CSV/XLSX/JSON-array/XBRL → tier-0 candidates directly. No segmenter. No Ollama.

**INV-13: Tier semantics are fixed.**
Tier 0 = lossless schema lift (confidence 1.0). Tier 1 = deterministic rule.
Tier 2 = model-extracted, evidence-bound. Tier 3 = model-extracted, weaker.

---

## Compilation

**INV-14: Compiler self-verifies.** `compile_generic_shard()` runs
`verify_shard()` on its own output. Fail → no ship.
*Proven by:* `integration_test.py` and
`tests/test_v1_mount.py::test_compile_verify_mount_roundtrip_with_ext_jsonl`.

**INV-15: Core tables are canonical JSONL, written deterministically.**
One canonical-JSON record per line (sorted keys, no whitespace, no floats,
no nulls), rows sorted bytewise ascending by primary key, exact key sets.
Same input → same bytes. NO Parquet in shards — Parquet exists only as
runtime-derived caches outside the shard directory.

**INV-16: Locators cross compilation via `ext/`.** The compiler reads the
`locator` dict from candidates and writes `ext/locators@1.jsonl` keyed by
`evidence_addr`. Core tables unchanged.

**INV-17: Only the compiler writes into a shard.** Nothing may inject files
into a shard directory before or after sealing: `manifest.extensions` is a
closed list of compiler-emitted extension identifiers, and a single Merkle
pass seals everything. Pre-compile derivation passes annotate
candidates.jsonl (e.g. temporal keys → `ext/temporal@1.jsonl`); any other
derived output lives outside the shard.
*Proven by:* `tests/test_v1_mount.py::test_compile_verify_mount_roundtrip_with_ext_jsonl`
(asserts no `*.parquet` under the shard and a compiler-emitted
`ext/temporal@1.jsonl`).

---

## Runtime (Spectra)

**INV-18: Verification gate mandatory.** Every shard passes `verify_shard()`
(or CLI `axm-verify shard PATH --trusted-key KEY`) before mount.
*Proven by:* `tests/test_v1_mount.py::test_mount_rejects_tampered_gold_copy`.

**INV-19: Queries through SQL gate.** `query_json()` enforces read-only SQL +
audit logging.

**INV-20: All tables mounted; shards are never written.** Claims, entities,
provenance, spans, AND all `ext/*.jsonl` tables are loaded from JSONL into
DuckDB at mount time. The loaded tables are a rebuildable cache that lives
outside the shard directory (RFC 0002 D2). Unknown or binary `ext/` formats
are skipped with a log line, never a mount failure — `ext/` is opaque.
*Proven by:* `tests/test_v1_mount.py::test_mount_skips_opaque_ext_formats`.

---

## Hallucination Firewall

**INV-21: Every factual statement must cite a shard claim.** Uncited = flagged.

---

## Cryptography (v1)

**INV-22: One suite: `axm-hybrid1` (Ed25519 ‖ ML-DSA-44).**
`sig/publisher.pub` = pk_ed25519(32) ‖ pk_mldsa44(1312) = 1344 bytes;
`sig/manifest.sig` = sig_ed25519(64) ‖ sig_mldsa44(2420) = 2484 bytes.
A signature is valid iff BOTH components verify. There is no suite
negotiation and no detection by key size. The v0.x `ed25519` and
`axm-blake3-mldsa44` suites are gone.
*Proven by:* `tests/test_v1_mount.py::test_spoke_api_import_surface`
(asserts the frozen sizes and suite string against the installed kernel).

**INV-23: The secret key is the 3904-byte hybrid1 blob.**
`sk = ed25519_seed(32) ‖ sk_mldsa44(2560) ‖ pk_mldsa44(1312)` =
`HYBRID1_SK_LEN` (3904 bytes) — generated by `axm-build keygen` or
`hybrid1_keygen()`. The compiler accepts exactly this blob (no sk-only
form, no pre-placed `publisher.pub`). Key pools are suite-aware: key
material of an unexpected size raises — never a silent fallback to a fresh
key. There is deliberately no default signing key anywhere in the
toolchain; tests use throwaway keypairs per run.
*Proven by:* `tests/test_v1_mount.py` (keygen length asserts) and
`forge_run.py::compile_shard` (raises on non-hybrid1 key pools).

---

## Non-Selective Recording (profile `embodied@1`)

**INV-24: Hot stream continuity is profile-gated.**
Shards that declare `"profiles": ["embodied@1"]` must have a gap-free frame
sequence in `content/cam_latents.bin`; a verifier that implements the
profile emits `E_BUFFER_DISCONTINUITY` on any missing frame, bad magic, or
truncation. Unchecked is not passed: consumers must confirm the profile
appears in `profiles_checked` (spec section 15.3). Spokes that do not
declare the profile are unaffected.

**INV-25: Binary format is the single source of truth.**
The values in `axm_embodied_core/protocol.py` (`AXLF`, `AXLR`, `AXRR`,
`REC_HEADER_FMT`, `LATENT_DIM`, etc.) and the genesis `embodied@1` profile
module must stay synchronized. If the embodied binary format ever changes,
both change in the same PR.

---

## Spoke Pattern

**INV-26: Every spoke depends on axm-core, not axm-genesis directly.**
Spokes import `axm_build.*` and `axm_verify.*` which resolve through the declared
dependency chain: spoke → axm-core → axm-genesis. Spokes never vendor genesis.

**INV-27: Every spoke has an `axm_<spoke>_core` package for domain-local constants.**
This package contains only what is genuinely spoke-specific: binary format constants,
domain identity functions that have no genesis equivalent. It never duplicates
genesis identity logic.

The genesis compiler is the authority for sealed `entity_id`/`claim_id`: at seal time
it **recomputes** every entity and claim ID from candidate content via
`axm_verify.identity.recompute_entity_id` / `recompute_claim_id`. Any internal,
pre-compile working IDs that forge or a spoke uses never reach the sealed tables —
the compiler overwrites them. Code outside the compiler (e.g. forge's derivation
passes, whose join keys must match the sealed claims) does not invent its own
scheme; it delegates to the same `axm_verify.identity` functions.

**INV-28: Spoke compile always calls `compile_generic_shard`.**
This is the only path to a genesis-verifiable shard. Spokes that bypass it and write
manifest/table files directly will produce shards that fail `axm-verify`. There are
no exceptions.

**INV-29: Domain extension data goes in `ext/`, not `evidence/`.**
`evidence/spans.jsonl` is the only permitted file in `evidence/`. Extension
tables are named `ext/<name>@<version>.<suffix>` — the `@version` IS in the
filename in v1 (`ext/temporal@1.jsonl`, `ext/lineage@1.jsonl`). The
kernel-registry extensions (`lineage@1`, `references@1`, `temporal@1`,
`locators@1`) are canonical JSONL emitted by the compiler itself
(`axm_build.ext_schemas.EXTENSION_REGISTRY`); the kernel verifier treats
all of `ext/` as opaque bytes under the Merkle root.

---

## Change Checklist

Before any PR to any repo in the stack:

1. `py_compile` on all changed Python files
2. `pytest` in the `axm-genesis` repo — all tests pass
3. Gold shard (`shards/gold/fm21-11-hemorrhage-v2`, trusted key
   `keys/gold-v2-provisional.pub`) still verifies (`python scripts/doctor.py`)
4. Shards with `ext/` still verify and mount (`python -m pytest tests/ -q`)
5. No invariant violated
6. If the embodied binary format changed: `axm_embodied_core/protocol.py` and
   the genesis `embodied@1` profile updated in the same commit
