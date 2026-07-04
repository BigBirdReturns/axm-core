# Foundry Exit — Live S3 Probe (report ledger)

**Control question.** *Can AXM run against an authorized live Foundry
S3-compatible export surface and produce the same genesis-sealed exit bundle it
already proved against fixtures?*

**Short answer.** The **code path** does — list → fetch → checksum → the same v0
dataset manifest → genesis seal → out-of-band verify → detached verify — proven
end to end against a real `boto3` S3 client. But it was proven against a **local
`moto` S3-compatible mock, NOT an authorized Palantir Foundry endpoint**. No
authorized Foundry credentials or endpoint exist in this environment, so the
authorized-live leg is **BLOCKED, pending creds**. Nothing about the sealed
bundle model changed.

## What this probe is (and is not)

`foundry_exit/live_probe.py` reuses Foundry Exit Intake v0 **unchanged** — no
redesign, no new bundle model, no FalkorDB. The only new behavior is building the
dataset inventory from a **live listing** (`list_objects`) instead of a fixture
file, then running the existing importer → bundle → seal → verify pipeline.

- `main()` **refuses** (exit 2) unless `FOUNDRY_S3_ENDPOINT`, `FOUNDRY_S3_BUCKET`,
  and `FOUNDRY_DATASET_RID` are all set. It never invents an endpoint and never
  reaches for arbitrary (non-Foundry) S3 credentials. Creds come from
  `AXM_S3_ACCESS_KEY` / `AXM_S3_SECRET_KEY`, out of band, never committed.
- Ontology and lineage attach **only** from explicit metadata inputs
  (`FOUNDRY_ONTOLOGY_JSON` / `FOUNDRY_LINEAGE_JSON`). S3 does not supply ontology;
  when absent, the probe **records the gap** rather than inventing it.

## Report ledger

Two rows, kept honest and separate.

| Field | Authorized live Foundry | Adapter conformance (local `moto` mock) |
|---|---|---|
| **endpoint class** | *(none — blocked)* | `local-moto-mock (NOT authorized Foundry)` |
| **dataset scope** | *(none)* | `ri.foundry.main.dataset.orders`, prefix `datasets/orders/` |
| **object count** | — | 3 (`part-0000.csv`, `part-0001.csv`, `_SUCCESS`) |
| **total bytes** | — | 44 |
| **checksum manifest** | — | sha256 per object, matches the real mock bytes |
| **bundle shard id** | — | `sh1_2cb8a04cf0a4958dede1f69a8bf42a7a903c489733915df0cb1deeaaab3bf35a` (genesis-derived) |
| **verification status** | — | `pass` (out-of-band key, exit 0) |
| **detached verification** | — | `PASS` — importer / ghostbox / palantir all **False** |
| **gaps** | **no authorized creds/endpoint** | none for the mock run itself |

Captured ledger from the mock run:

```json
{
  "endpoint_class": "local-moto-mock (NOT authorized Foundry)",
  "dataset_scope": {"dataset_rid": "ri.foundry.main.dataset.orders", "prefix": "datasets/orders/"},
  "object_count": 3,
  "total_bytes": 44,
  "checksum_manifest": [
    {"object_path": "datasets/orders/_SUCCESS",     "checksum": "e3b0c442…b855", "size_bytes": 0},
    {"object_path": "datasets/orders/part-0000.csv", "checksum": "c6f70dbd…a7bc", "size_bytes": 25},
    {"object_path": "datasets/orders/part-0001.csv", "checksum": "7df007d4…d0e4", "size_bytes": 19}
  ],
  "ontology_object_types": ["Order"],
  "lineage_edges": 1,
  "sealed": true,
  "shard_id": "sh1_2cb8a04cf0a4958dede1f69a8bf42a7a903c489733915df0cb1deeaaab3bf35a",
  "verification": "pass",
  "detached": {"status": "PASS", "importer_involved": false, "ghostbox_involved": false, "palantir_involved": false},
  "gaps": []
}
```

## Permission / metadata / layout gaps recorded (not widened away)

Per the boundary "if the live endpoint exposes missing permissions, paging,
versioning, markings, or object layout issues, record them instead of widening
the importer blindly":

1. **Authorized Foundry access — BLOCKED.** No Foundry endpoint/creds in this
   environment. Generic AWS creds are present but are **neither Foundry nor
   authorized**, so the probe does not touch them. This is the primary gap.
2. **Prefix round-trip (object layout).** v0's `list_objects` returns
   fully-qualified keys while `read_bytes` prepends `S3Config.prefix`, so keys
   only round-trip when the config prefix is empty. The probe scopes via the
   **list prefix** (`S3Config.prefix=""`) so listing keys feed straight back into
   `read_bytes`. Recorded here rather than papered over in the adapter.
3. **Ontology is not in S3.** The data plane supplies bytes only; ontology and
   lineage are separate metadata planes. Absent metadata is recorded as a gap.
4. **Not yet exercised against a live endpoint:** paging (>1000 keys →
   `ContinuationToken`), object versioning, and security markings. These are real
   Foundry surfaces the mock does not model; they remain open for the authorized
   live run and must be recorded, not assumed, when creds arrive.

## Boundaries held

- **Read-only.** `S3ExportSource` uses only `get_object` / `list_objects_v2`; no
  `put/write/upload/delete` path (asserted in v0 tests).
- **No Palantir ID becomes an AXM custody ID.** `shard_id` is genesis-derived
  (`sh1_` + BLAKE3 of the canonical manifest); Palantir RIDs survive only as
  external IDs.
- **Planes stay separate.** Ontology/lineage never come from S3.
- **Untouched:** the v0 bundle model, GhostBox, ScreenGhost, axm-chat, PR #1, and
  genesis. No FalkorDB. The probe imports no `ghostbox` (asserted).

## Evidence tier

**Adapter conformance against a local `moto` S3-compatible mock — proven.** The
real `boto3` read-only calls, listing-driven inventory, checksum manifest, and
the full genesis seal → verify → detached exit property all pass. **Authorized
live Foundry extraction — not run; blocked on credentials.** To close it, supply
an authorized read-only Foundry S3 surface via `FOUNDRY_S3_ENDPOINT` /
`FOUNDRY_S3_BUCKET` / `FOUNDRY_DATASET_RID` (+ `AXM_S3_*`) and re-run
`python -m foundry_exit.live_probe`; the ledger it prints is the live receipt.
