# AXM Stack Reconciliation: Audit Report

Date: 2026-01-29

This audit used the following zips as provided:
- axm-production-v5-final.zip (contains Genesis + Spectra + Forge + Clarion)
- forge-v2.0.0.zip
- clarion-v2.0.0.zip
- graphkdf-v1.0.0.zip
- nodalflow-v2.zip
- axm_artifact_minimal.zip
- axm-conscience-v1.1.1.zip

## 1) Genesis (Ground Truth) contract

Source of truth inspected:
- axm-production-v5/genesis/src/axm_verify/logic.py
- axm-production-v5/genesis/src/axm_verify/crypto.py
- axm-production-v5/genesis/src/axm_verify/const.py

### Required shard layout

A shard directory MUST contain at minimum:

- manifest.json
- graph/entities.parquet
- graph/claims.parquet
- graph/provenance.parquet
- evidence/spans.parquet
- sig/manifest.sig
- sig/publisher.pub

The shard MAY also contain:
- content/** (any number of additional files)

The verifier rejects:
- missing required items
- any extra file under sig/
- any extra file at top-level (except manifest.json) outside graph/, evidence/, content/, sig/

### Merkle root computation

Genesis computes a Merkle root over all files in the shard EXCEPT:
- manifest.json
- anything under sig/

Leaf hash construction:
- relpath_bytes = UTF-8 relative path with forward slashes
- leaf_bytes = relpath_bytes || 0x00 || file_content_bytes
- leaf_hash = BLAKE3(leaf_bytes)

Leaf ordering:
- sort by relpath_bytes ascending

Internal node hashing:
- parent = BLAKE3(left_hash || right_hash)
- if odd number of nodes at a level, duplicate the last node

Root encoding:
- hex string of final digest

### Signature verification

- manifest.json includes:
  - "publisher_public_key_b64" (bytes of Ed25519 public key, base64)
  - "content_merkle_root" (hex)
- sig/publisher.pub must match publisher_public_key_b64
- sig/manifest.sig is Ed25519 signature over canonical JSON bytes of manifest.json
- The verifier pins trust by comparing sig/publisher.pub against a provided trusted key file path

### Parquet schema expectations

Genesis validates that required parquet files exist and match fixed schemas, and that all columns contain no nulls.

## 2) Forge audit (forge-v2.0.0.zip)

### Findings

1. Candidate conversion drift:
- forge Claim model stores subject in args[] (role="subject"), but the Genesis candidates converter expected legacy fields (entity_id, subject_label, score, tier, etc).
- Result: Forge cannot reliably produce Genesis candidates.jsonl for the Genesis compiler.

2. Encryption drift:
Decision point: Forge's genesis_emission supported an encryption option that did NOT use clarion-v2.0.0 (GraphKDF) and was incompatible with Spectra's envelope expectations.
This violates "one source of truth per concept" for Clarion.

3. Legacy emission still present:
- axm_forge/emission/shard.py implements a legacy shard layout with claims_red.jsonl / claims_green.jsonl, which conflicts with Genesis's single claims.parquet contract.

### Fixes provided (patches)

- forge_candidate_conversion.patch
  - Updates Candidate.from_legacy_claim to read subject from Claim.args and to emit candidates.jsonl aligned to Genesis compiler expectations.

- forge_genesis_emission_clarion.patch
  - Removes Forge-internal encryption and delegates encryption to clarion-v2.0.0 encrypt_shard (GraphKDF topology hash v3).

## 3) Spectra audit (axm-production-v5/spectra)

### Findings

1. Verification invocation drift:
- Spectra shells out to "axm-verify".
- This is brittle and breaks in environments where CLI is not installed, even if Genesis code is available.

2. Envelope / KDF drift:
- Spectra transport supports Clarion envelope "1.0" and "1.1" using HKDF.
- clarion-v2.0.0 defines envelope "2.0" with topology binding via GraphKDF and an AAD scheme that differs from v1.*

### Fixes provided (patches)

- spectra_engine_verify.patch
  - Adds an in-process Genesis verification path when axm-verify is not available.
  - Supports pinning a trusted publisher key via SPECTRA_TRUSTED_PUBKEY. Falls back to shard's publisher.pub as a dev convenience.

- spectra_transport.patch
  - Adds support for clarion_version "2.0" by delegating decryption to clarion.core.decrypt_envelope.

## 4) Clarion / GraphKDF audit

### Findings

- clarion-v2.0.0 already standardizes:
  - GraphKDF topology hash versioning (supports v2 and v3)
  - AAD construction
  - Envelope format and blob hashing
- graphkdf-v1.0.0 exists as a standalone implementation, but clarion-v2.0.0 already includes what Spectra and Forge need.

### Action

- Treat clarion-v2.0.0 as the single encryption/envelope implementation.
- Keep graphkdf-v1.0.0 as a library dependency only, not as an independent envelope format.

## 5) Nodal Flow (UI) audit

Nodal Flow (nodalflow-v2.zip) appears to be a consumer UI layer and does not define cryptography or shard layout.
No required changes were identified in the current reconciliation pass because the primary drift occurred below the UI.

## 6) Environment notes for running tests

This sandbox environment did not have the python packages "blake3" and "pyarrow" available. Genesis uses them in its reference implementation.
The provided integration test assumes the user environment includes these dependencies (or that Genesis is installed as intended).
