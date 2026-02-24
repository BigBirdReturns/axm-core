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
Every file in the shard (including ext/) is in the Merkle tree. Signature covers manifest.
Tamper with any byte → shard rejected.

**INV-3: ext/ is the only extension point.**
New data goes in ext/. Core directories (content/, graph/, evidence/, sig/) are frozen.
Verifier allows ext/ at root, rejects all other unknown directories.

**INV-4: Extensions are optional.**
Old verifiers ignore ext/. No core functionality depends on ext/ existing.

**INV-5: Manifest "extensions" key is conditional.**
Empty ext/ → no extensions key in manifest. Preserves hash stability.

## Identity (Deterministic)

**INV-6: shard_id = "shard_blake3_" + merkle_root.** Content-addressed.

**INV-7: entity_id = recompute_entity_id(namespace, label).** Stable across rebuilds.

**INV-8: claim_id = recompute_claim_id(subject_id, predicate, object, object_type).** Stable.

**INV-9: evidence_addr = hash(source_hash + byte_start + byte_end).**
Stable join key for ext/locators@1. Does NOT depend on span_id or provenance_id.

**INV-10: span_id = hash(source_hash + byte_range + evidence_text).** Stable.

## Forge Ingestion

**INV-11: Documents are never flattened.**
Extractors return DocumentBlocks with Locators. PDF pages → separate blocks.
Locators survive: extractor → candidates.jsonl → compiler → ext/locators@1 → Spectra.

**INV-12: Structured data never touches an LLM.**
CSV/XLSX/JSON-array/XBRL → tier-0 candidates directly. No segmenter. No Ollama.

**INV-13: Tier semantics are fixed.**
Tier 0 = lossless schema lift (confidence 1.0). Tier 1 = deterministic rule.
Tier 2 = model-extracted, evidence-bound. Tier 3 = model-extracted, weaker.

## Compilation

**INV-14: Compiler self-verifies.** verify_shard() on own output. Fail → no ship.

**INV-15: Parquet written deterministically.** Same input → same bytes. Sorted by PK. ZSTD.

**INV-16: Locators cross compilation via ext/.** Compiler reads locator from candidates,
writes ext/locators@1.parquet keyed by evidence_addr. Core tables unchanged.

## Runtime (Spectra)

**INV-17: Verification gate mandatory.** Every shard passes verify_shard() before mount.

**INV-18: Queries through SQL gate.** query_json() enforces read-only SQL + audit logging.

**INV-19: All tables mounted.** Claims, entities, provenance, spans, AND all ext/ parquets.

## Hallucination Firewall

**INV-20: Every factual statement must cite a shard claim.** Uncited = flagged.

## Change Checklist

1. `py_compile` on all changed files
2. `pytest genesis/tests/` all pass
3. Gold shard still verifies
4. New shards with ext/ verify
5. No invariant violated

## Post-Quantum (v1.1.0)

**INV-21: PQ is default, Ed25519 is backward-compatible.**
New shards use `axm-blake3-mldsa44`. Old Ed25519 shards verify without modification.
The `suite` field in manifest identifies the signing algorithm. Absent means Ed25519.

**INV-22: ML-DSA-44 signatures are deterministic.**
Same key + same message = same signature. No nonce. No randomness. Reproducible builds.

**INV-23: Key convention is sk||pk (3840 bytes) for ML-DSA-44.**
Secret key alone is 2528 bytes. Combined format (sk||pk = 3840 bytes) is canonical for
key storage. The compiler accepts either format.
