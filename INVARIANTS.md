# AXM Stack Invariants

Absolute constraints. Every code change, every LLM session, every PR must preserve these.
If a change violates an invariant, the change is wrong. No exceptions.

---

## Genesis Core (Frozen)

**INV-1: Genesis spec is frozen.**
The five core tables (entities, claims, provenance, spans + source.txt), the Merkle tree,
Ed25519 signatures, and manifest schema do not change. Old verifiers verify new shards.
New verifiers verify old shards.

**INV-2: Merkle root covers all files.**
Every file in the shard (including `ext/`) is in the Merkle tree. Signature covers manifest.
Tamper with any byte → shard rejected.

**INV-3: `ext/` is the only extension point.**
New data goes in `ext/`. Core directories (`content/`, `graph/`, `evidence/`, `sig/`) are frozen.
Verifier allows `ext/` at root, rejects all other unknown directories.

**INV-4: Extensions are optional.**
Old verifiers ignore `ext/`. No core functionality depends on `ext/` existing.

**INV-5: Manifest `extensions` key is conditional.**
Empty `ext/` → no extensions key in manifest. Preserves hash stability.

---

## Identity (Deterministic)

**INV-6: `shard_id = "shard_blake3_" + merkle_root`.** Content-addressed.

**INV-7: `entity_id = recompute_entity_id(namespace, label)`.** Stable across rebuilds.

**INV-8: `claim_id = recompute_claim_id(subject_id, predicate, object, object_type)`.** Stable.

**INV-9: `evidence_addr = hash(source_hash + byte_start + byte_end)`.**
Stable join key for `ext/locators@1`. Does NOT depend on `span_id` or `provenance_id`.

**INV-10: `span_id = hash(source_hash + byte_range + evidence_text)`.** Stable.

---

## Forge Ingestion

**INV-11: Documents are never flattened.**
Extractors return DocumentBlocks with Locators. PDF pages → separate blocks.
Locators survive: extractor → candidates.jsonl → compiler → `ext/locators@1` → Spectra.

**INV-12: Structured data never touches an LLM.**
CSV/XLSX/JSON-array/XBRL → tier-0 candidates directly. No segmenter. No Ollama.

**INV-13: Tier semantics are fixed.**
Tier 0 = lossless schema lift (confidence 1.0). Tier 1 = deterministic rule.
Tier 2 = model-extracted, evidence-bound. Tier 3 = model-extracted, weaker.

---

## Compilation

**INV-14: Compiler self-verifies.** `verify_shard()` on own output. Fail → no ship.

**INV-15: Parquet written deterministically.** Same input → same bytes. Sorted by PK. ZSTD.

**INV-16: Locators cross compilation via `ext/`.** Compiler reads locator from candidates,
writes `ext/locators@1.parquet` keyed by `evidence_addr`. Core tables unchanged.

---

## Runtime (Spectra)

**INV-17: Verification gate mandatory.** Every shard passes `verify_shard()` before mount.

**INV-18: Queries through SQL gate.** `query_json()` enforces read-only SQL + audit logging.

**INV-19: All tables mounted.** Claims, entities, provenance, spans, AND all `ext/` parquets.

---

## Hallucination Firewall

**INV-20: Every factual statement must cite a shard claim.** Uncited = flagged.

---

## Post-Quantum (v1.1.0)

**INV-21: PQ is default, Ed25519 is backward-compatible.**
New shards use `axm-blake3-mldsa44`. Old Ed25519 shards verify without modification.
The `suite` field in manifest identifies the signing algorithm. Absent means Ed25519.

**INV-22: ML-DSA-44 signatures are deterministic.**
Same key + same message = same signature. No nonce. No randomness. Reproducible builds.

**INV-23: Key convention is sk||pk (3840 bytes) for ML-DSA-44.**
Secret key alone is 2528 bytes. Combined format (sk||pk = 3840 bytes) is canonical for
key storage. The compiler accepts either format.

---

## Non-Selective Recording (v1.2.0)

**INV-24: Hot stream continuity is mechanically enforced.**
Any shard with `content/cam_latents.bin` must have a gap-free frame sequence.
`axm-verify` emits `E_BUFFER_DISCONTINUITY` on any missing frame, bad magic, or truncation.
Spokes that do not produce binary hot streams are unaffected — the check is conditional.

**INV-25: Binary format is the single source of truth.**
The values in `axm_embodied_core/protocol.py` (`AXLF`, `AXLR`, `AXRR`, `REC_HEADER_FMT`,
`LATENT_DIM`, etc.) and in `axm_verify/logic.py` must stay synchronized.
If the embodied binary format ever changes, both files change in the same PR.

---

## Spoke Pattern

**INV-26: Every spoke depends on axm-core, not axm-genesis directly.**
Spokes import `axm_build.*` and `axm_verify.*` which resolve through the declared
dependency chain: spoke → axm-core → axm-genesis. Spokes never vendor genesis.

**INV-27: Every spoke has an `axm_<spoke>_core` package for domain-local constants.**
This package contains only what is genuinely spoke-specific: binary format constants,
domain identity functions (e.g. `span_id`, `prov_id`) that have no genesis equivalent.
It never duplicates genesis — `entity_id` and `claim_id` always delegate to
`axm_verify.identity.recompute_entity_id` / `recompute_claim_id`.

**INV-28: Spoke compile always calls `compile_generic_shard`.**
This is the only path to a genesis-verifiable shard. Spokes that bypass it and write
manifest/parquet files directly will produce shards that fail `axm-verify`. There are
no exceptions. If a spoke needs binary files in `content/`, it uses the two-pass
inject-and-reseal pattern (see `axm-embodied/compile.py::_inject_latents_and_reseal`).

**INV-29: Domain extension data goes in `ext/`, not `evidence/`.**
`evidence/spans.parquet` is the only permitted file in `evidence/`. Any spoke-specific
Parquet output (stream metadata, coordinates, references, etc.) must go in `ext/`
using the `name@version` filename convention. The genesis verifier ignores `ext/`.

---

## Change Checklist

Before any PR to any repo in the stack:

1. `py_compile` on all changed Python files
2. `pytest` in the `axm-genesis` repo — all tests pass
3. Gold shard (`fm21-11-hemorrhage-v1`) still verifies
4. Shards with `ext/` still verify
5. No invariant violated
6. If binary format constants changed: both `axm_embodied_core/protocol.py` and `axm_verify/logic.py` updated in the same commit
