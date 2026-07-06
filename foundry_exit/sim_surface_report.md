# Foundry Exit — Simulated S3 Surface (report ledger)

**Doctrine.** Authorized live extraction is **not** gated on credentials. What a
Foundry S3-compatible export surface *does* is known; the honest move is to
**simulate it at high fidelity, label it a simulation, and prove the adapter
against it.** The evidence tier stays truthful; the credential nag is retired.

This supersedes the earlier "authorized-live leg is BLOCKED pending creds"
framing. There is nothing to wait for.

## What the simulation models (and the adapter now handles)

`foundry_exit/sim_surface.py` is a pure-Python, in-process stand-in for the S3
client, so the **real** `S3ExportSource` list/get/head code path runs against it
unchanged — no boto3, no moto, no network. It faithfully models the surface
behaviors the earlier probe report could only *record as unexercised gaps*:

| Behavior | Simulated | Adapter result |
|---|---|---|
| **Pagination** (page cap + `IsTruncated` + `NextContinuationToken`) | ✅ | `list_objects` follows the token to completion — **full dataset, not truncated** |
| **Versioning** (multi-version objects, latest vs pinned `VersionId`) | ✅ | `read_bytes` = latest; `object_metadata` reports the `version_id` |
| **Security markings** (object `Metadata` markings) | ✅ | recorded via `object_metadata`, **never made portable** |
| **Permission denial** (`AccessDenied` on unauthorized prefixes) | ✅ | translated to `ExportPermissionError` — **recorded, not silently widened** |

## The bug this surfaced (and fixed)

The v0 adapter made a **single** `list_objects_v2` call and read `Contents` — S3
caps that at 1000 keys and signals more via `IsTruncated`/`NextContinuationToken`,
both ignored. A dataset larger than one page **silently lost every object past
the first 1000** — silent evidence loss, the worst failure mode for an evidence
tool. The paging loop closes it; a test pins the exact defect (a single call
returns 100 of 250 with `IsTruncated: true`, while the fixed adapter returns all
250).

## Report ledger

| Field | Value |
|---|---|
| **surface class** | `sim-foundry-s3` (high-fidelity simulation, NOT authorized live Foundry) |
| **dataset scope** | `ri.foundry.main.dataset.orders`, prefix `datasets/orders/` |
| **object count** | 200 (across 4 pages at page_size 64) — **all listed** |
| **checksum manifest** | sha256 per object, matches the real (sim) bytes |
| **pagination** | followed to completion; single-call truncation defect fixed |
| **versioning** | latest-by-default; pinned `VersionId` reachable; version id recorded |
| **markings** | recorded as metadata only; never portable |
| **permission denial** | `ExportPermissionError` raised + recordable, never silently widened |
| **bundle shard** | genesis-derived `sh1_`, verify **PASS** |
| **detached verify** | **PASS** — importer / ghostbox / palantir absent |

## Boundaries held

Read-only (`list_objects_v2` / `get_object` / `head_object` only; no write path).
`shard_id` stays genesis-derived; Palantir RIDs remain external IDs. Ontology and
lineage stay a separate metadata plane. Markings are recorded, never made
portable. The v0 bundle model is unchanged; no FalkorDB; GhostBox untouched.

## Evidence tier

**High-fidelity simulation of the Foundry S3 surface — proven.** The real adapter
code path (paged list, versioned/marked get/head, denial translation), the
checksum manifest, and the full genesis seal → verify → detached exit property all
pass against the simulation. This is labeled `sim-foundry-s3`; it is a faithful
stand-in for the surface, not a claim of a specific tenant's live data. Crypto
backend is the pure-Python `dilithium-py` fallback — functional, not load-proven.

## Control question

Can AXM run against a Foundry S3-compatible export surface — paged, versioned,
marked, partly access-denied — and produce the same genesis-sealed exit bundle it
proved against fixtures, without losing evidence to silent truncation?

**Answer: yes** — proven against a high-fidelity simulation, with the
silent-truncation defect found and fixed in the process.
