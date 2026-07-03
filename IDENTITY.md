# AXM Identity Rules

How IDs are generated. What survives rebuilds. What doesn't.
This is the trust layer for every extension join.

## Stable Across Rebuilds (safe join keys)

| ID | Derivation | Depends On | Stable? |
|----|-----------|------------|---------|
| `shard_id` | `"sh1_" + hex(BLAKE3(manifest.json bytes))` | Manifest bytes (which bind all file contents via `integrity.merkle_root`) | Yes — derived, never stored |
| `entity_id` | `"e1_" + b32(SHA-256(canon(namespace) ‖ 0x00 ‖ canon(label)))` — via `recompute_entity_id` | Namespace + label string | Yes — deterministic |
| `claim_id` | `"c1_" + b32(SHA-256(subject_id ‖ 0x00 ‖ canon(predicate) ‖ 0x00 ‖ object_type ‖ 0x00 ‖ object_value))` — via `recompute_claim_id` | Claim content | Yes — deterministic |
| `source_hash` | `hex(SHA-256(content_bytes))` | Source text after normalization | Yes — content-addressed |
| `evidence_addr` | `"ea_" + b32(SHA-256(source_hash ‖ 0x00 ‖ byte_start ‖ 0x00 ‖ byte_end))` | Source bytes only | Yes — content-addressed |

## Stable but Text-Dependent

| ID | Derivation | Risk |
|----|-----------|------|
| `span_id` | `"s1_" + b32(SHA-256(source_hash ‖ 0x00 ‖ byte_start ‖ 0x00 ‖ byte_end ‖ 0x00 ‖ evidence_text))` | Changes if evidence text changes. Use evidence_addr for position-only joins. |

## Unstable (do NOT use as sole join key in extensions)

| ID | Derivation | Risk |
|----|-----------|------|
| `provenance_id` | `"p1_" + b32(SHA-256(claim_id ‖ 0x00 ‖ source_hash ‖ 0x00 ‖ byte_start ‖ 0x00 ‖ byte_end))` | Provenance rows can be split/merged/regenerated. |

## Rules for Extension Authors

1. Key to content, not internal IDs. Use `evidence_addr`, `claim_id`, `entity_id`, `shard_id`.
2. Use `span_id` as secondary link only.
3. Never key to `provenance_id` alone.
4. All IDs are deterministic. Same input = same ID.
5. Namespace matters for entity_id.

## Hash Function

Row identifiers (`e1_` entity, `c1_` claim, `p1_` provenance, `s1_` span) and the
evidence address (`ea_`) are the **full 32-byte** SHA-256 of the domain-separated
preimage above, base32-encoded (lowercase, no `=` padding) — **52 characters**
after the versioned prefix, as implemented by `axm_verify.identity._derive_id`.
There is no truncation: the retired v0.x "first 15 bytes / `s_` / `p_`" scheme is
gone. `shard_id` is the one exception — `"sh1_"` plus the **hex** BLAKE3 digest of
the canonical manifest bytes (64 hex chars). See `COMPATIBILITY.md` §7 and
`INVARIANTS.md` (INV-7/8/10).
