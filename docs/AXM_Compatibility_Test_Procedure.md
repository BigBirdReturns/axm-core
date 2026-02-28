# AXM Compatibility Test Procedure
**Version:** 0.1  
**Status:** Working draft  
**Audience:** Spoke builders, auditors, integration engineers

---

## What this document is

This document defines how a runtime proves it is AXM-compatible.

Compatibility is not self-reported. It is a mechanical test against the Genesis verifier using only the sealed capsule and the public spec. No vendor code. No runtime access. No trust in the producing system.

A runtime either passes or it does not.

---

## The canonical definition

A runtime is AXM-compatible if and only if:

> A Genesis verifier, given only the sealed capsule and the public spec, can confirm that all recorded events — including failures — are present, unmodified, and causally linked from the hot stream buffer through to the sealed artifact.

---

## Five requirements

Each requirement has a pass/fail rule. There is no partial compliance.

---

### Requirement 1 — Manifest integrity

The capsule manifest must be deterministic from inputs.

Required fields:
- Content hashes for all included artifacts (SHA-256, hex)
- Schema version
- Extraction or build config hash
- Merkle root (Blake3) covering all content, graph, and evidence files
- Parent references, if present, must be defined

**Pass/fail rule:** Rebuilding from identical inputs produces an identical manifest hash. Any deviation is a failure.

---

### Requirement 2 — Content identity

All primary artifacts must be content-addressed and covered by the Merkle tree rooted at the manifest.

Required:
- Stable hashing of all artifact bytes
- Every artifact included in Merkle lineage
- Binary artifacts hashed as raw bytes, never re-serialized

**Pass/fail rule:** Flipping any single byte in any artifact changes that artifact's hash and the manifest Merkle root. A verifier must reject the capsule if these do not match.

---

### Requirement 3 — Lineage events

The capsule must record how outputs relate to inputs without requiring the runtime to replay.

Required:
- Source references (what was consumed)
- Transformation step identifiers (what was done)
- Ordering of steps
- Tool or model identity, at string level

This is metadata, not replay data. An independent party must be able to read it without access to the producing runtime.

**Pass/fail rule:** An auditor who has never seen the runtime can reconstruct the causal chain from inputs to outputs using only the capsule contents.

---

### Requirement 4 — Proof bundle

The sealed capsule must be verifiable by a third party using only public artifacts.

Minimum proof bundle contents:
- Manifest
- Referenced artifacts
- Lineage metadata
- Genesis verifier version

**Pass/fail rule:** Running `axm-verify` against the bundle with no runtime access produces a clean result. Any dependency on vendor code is a failure.

---

### Requirement 5 — Non-selective recording

This is the requirement that distinguishes AXM from other verification protocols.

**Failure events must be sealed under the same contract as success events.**

A runtime that seals clean runs and drops failures is not AXM-compatible, regardless of how many success capsules verify.

#### Hot stream buffer

Every spoke runtime must maintain a tamper-evident event buffer.

Required properties:
- Append-only
- Hash-chained (each entry references the hash of the previous entry)
- Gap-free — missing sequence entries are a compliance violation
- Buffer head hash is observable and checkable

#### Immediate record rule

On any failure state, an event entry must be appended to the buffer before the system exits, retries, or resets. The event must record:
- Failure state at the moment of occurrence
- Lineage up to the failure point
- Partial artifacts, content-addressed if they were produced
- Failure reason as structured metadata

#### Deferred sealing

Sealing may be deferred under spoke-defined operational constraints. This is an explicit accommodation for edge hardware, intermittent connectivity, and real-time systems.

However:
- The hot stream buffer must exist and be honest before sealing occurs
- The sealed capsule must commit to the buffer head hash at seal time
- The sealed capsule must reference the covered buffer range
- A gap in the buffer is a compliance violation regardless of why sealing was deferred

**Pass/fail rule:** A verifier that finds a discontinuity in the hot stream sequence or hash chain must reject the spoke as non-compliant, regardless of how many success capsules verify.

If success is sealed but failure is not, the runtime is not AXM-compatible.

---

## Running the test

```bash
# Verify a sealed capsule against a trusted publisher key
axm-verify shard <shard_dir>/ --trusted-key <publisher.pub>
```

Exit code 0 means PASS. Non-zero means FAIL.

Output is JSON. A passing capsule prints:

```json
{"shard": "<path>", "status": "PASS", "error_count": 0, "errors": []}
```

A failing capsule prints the error list with codes, messages, and locations.

### Error codes

All error codes are prefixed with `E_`. The full set is defined in `axm_verify/const.py`.
Codes most relevant to compatibility testing:

| Code | Meaning |
|------|---------|
| `E_MANIFEST_SYNTAX` | `manifest.json` is not valid JSON |
| `E_MANIFEST_SCHEMA` | `manifest.json` is missing required fields or has wrong types |
| `E_SIG_MISSING` | `sig/manifest.sig` or `sig/publisher.pub` not found |
| `E_SIG_INVALID` | Signature does not verify against the trusted key |
| `E_MERKLE_MISMATCH` | Computed Merkle root does not match stored value |
| `E_SCHEMA_TYPE` | A Parquet file has wrong column names or types |
| `E_SCHEMA_NULL` | A required column contains null values |
| `E_REF_ORPHAN` | A claim references an entity that does not exist |
| `E_REF_SOURCE` | A span or provenance record points to a non-existent content file or out-of-bounds byte range |
| `E_LAYOUT_MISSING` | Required directory or file not found at shard root |
| `E_LAYOUT_DIRTY` | Unexpected file present in a required directory |
| `E_BUFFER_DISCONTINUITY` | Hot stream gap detected in `cam_latents.bin` |

These are the canonical strings. Search for them in verification output, not for human-readable variants.

---

## What AXM does not test

AXM does not test:
- Execution quality
- Model accuracy
- Orchestration reliability
- Speed or throughput
- Safety policy correctness

Those are spoke concerns. AXM tests only whether the record is honest and complete.

---

## Reference implementation

See `axm-embodied` — the first spoke and the reference implementation of this contract in the hardest operating environment.
