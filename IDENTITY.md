# AXM Identity Rules

How IDs are generated. What survives rebuilds. What doesn't.
This is the trust layer for every extension join.

## Stable Across Rebuilds (safe join keys)

| ID | Derivation | Depends On | Stable? |
|----|-----------|------------|---------|
| `shard_id` | `"shard_blake3_" + merkle_root` | All file contents | Yes — content-addressed |
| `entity_id` | `recompute_entity_id(namespace, label)` | Namespace + label string | Yes — deterministic |
| `claim_id` | `recompute_claim_id(subj_id, pred, obj, obj_type)` | Claim content | Yes — deterministic |
| `source_hash` | `SHA-256(content_bytes)` | Source text after normalization | Yes — content-addressed |
| `evidence_addr` | `hash("ea_", source_hash + byte_start + byte_end)` | Source bytes only | Yes — content-addressed |

## Stable but Text-Dependent

| ID | Derivation | Risk |
|----|-----------|------|
| `span_id` | `hash("s_", source_hash + byte_start + byte_end + evidence_text)` | Changes if evidence text changes. Use evidence_addr for position-only joins. |

## Unstable (do NOT use as sole join key in extensions)

| ID | Derivation | Risk |
|----|-----------|------|
| `provenance_id` | `hash("p_", source_hash + byte_start + byte_end)` | Provenance rows can be split/merged/regenerated. |

## Rules for Extension Authors

1. Key to content, not internal IDs. Use `evidence_addr`, `claim_id`, `entity_id`, `shard_id`.
2. Use `span_id` as secondary link only.
3. Never key to `provenance_id` alone.
4. All IDs are deterministic. Same input = same ID.
5. Namespace matters for entity_id.

## Hash Function

All `_b32_id` hashes: SHA-256 of UTF-8 input, first 15 bytes, base32 lowercase no padding, type prefix.
