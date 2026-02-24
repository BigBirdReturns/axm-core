# AXM Stack Reconciliation: Decision Log

Date: 2026-01-29

## Decision 1: KDF strategy

Choice: **GraphKDF with topology hash v3** (length-prefixed, domain separated)

Rationale:
- clarion-v2.0.0 already implements topology-bound keys and uses GraphKDF.
- Topology-bound encryption is the intended security property for color partitioning and replay safety.
- Standardizing on HKDF would remove topology binding and leave multiple incompatible implementations alive.

## Decision 2: Topology hash version

Choice: **v3**

Rationale:
- v3 is explicitly designed to avoid delimiter ambiguity and includes domain separation.
- Backward compatibility is optional and should be implemented only as a read path if needed.

## Decision 3: Clarion envelope format

Choice: **Clarion v2.0 envelope.json + blobs/** as implemented by clarion-v2.0.0

Canonical fields:
- clarion_version: "2.0"
- envelope_id, shard_id
- kdf: { alg, topology_hash_version, topology_hash_b64 }
- epoch
- colors[] with per-color { salt_b64, files[] }
- files[] with per-file { path, blob_hash, nonce_b64, plaintext_hash }

AAD canonical construction (in order):
- envelope_id
- shard_id
- color
- file path
- plaintext_hash
- topology_hash_b64

Blob hash:
- sha256(ciphertext_bytes)

## Decision 4: Red/Green partition

Choice: **No red/green file layout in Genesis shards**

Rationale:
- Genesis contract requires single graph/claims.parquet and other fixed parquet files.
- Confidentiality and partitioning belong in Clarion encryption and envelope metadata, not in Genesis file naming.

## Decision 5: Source of truth ownership

- Genesis: shard layout + verification contract (frozen)
- Forge: produces Genesis shards, does not define its own shard format
- Clarion: the only place that defines encryption and envelope format
- Spectra: consumes verified shards, and can optionally decrypt Clarion envelopes
- Nodal Flow: UI consumer only

## Decision 6: Registry placement and ownership

Date: 2026-02-24

Choice: **Registry as a fourth pillar at axm-core root, above all subsystems**

Rationale:
- Genesis, Forge, Spectra, and Clarion must remain name-blind. They operate on
  shard_ids and cryptographic contracts, not human meaning.
- Human naming is convention. shard_ids are math. They belong in separate layers.
- Placing naming inside any subsystem would pollute frozen contracts (Genesis),
  extraction logic (Forge), or the query engine (Spectra) with mutable,
  human-centric state.
- The Registry is the DNS for cryptographic artifacts. It owns:
  - canonical names (namespace/slug format)
  - aliases and shorthand refs
  - pointer stability (current shard_id per name)
  - append-only lineage (history of why pointers moved)
  - authority selection rules (policy per artifact)
  - lockfile export for reproducible runs

Implementation:
- `registry/artifacts.json` — file-backed store (promotable to SQLite or a shard)
- `registry/resolve.py` — Registry class with resolve, set_current, add_alias,
  list_history, export_lockfile, add_artifact
- `registry/schema.json` — machine-readable schema, validated on load and save
- `registry/SCHEMA.md` — human contract for artifacts.json and axm.lock.json
- `cli/CONTRACT.md` — unified CLI verb reference and machine output contract

The unified `axm_cli.py` at root imports Registry only. It treats Genesis and
Forge as subprocesses and Spectra as an HTTP service. Human names never reach
any subsystem. Only shard_ids do.
