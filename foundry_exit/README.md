# AXM Foundry Exit Intake v0

Make AXM ready to import an **authorized** Palantir Foundry export and seal it so
the liberated record survives Palantir, GhostBox, and any AI layer.

Not a MavenOS replacement. A runnable v0 that takes a Foundry-style export and
produces an **AXM-sealed exit bundle** whose record verifies after Palantir,
GhostBox, and the importer are all removed.

## Planes

```
data plane (S3-compatible)   dataset bytes           adapters.py (read-only)
metadata plane               ontology + lineage      explicit JSON inputs
                             dataset inventory
    │
    ▼  importer.py  (pull bytes, checksum, stage locally; assemble manifest)
FoundryExitManifest  ──►  bundle.py  ──►  foundry_exit_manifest.json
                                          datasets.manifest.jsonl
                                          ontology.json · lineage.json · graph.json
    │
    ▼  seal.py  (genesis compiler, out-of-band key)
axm-hybrid1 SealedShard   (genesis-derived sh1_ custody id)
    │
    ▼  verify with an out-of-band key · packet.py · exit_test.py
```

Palantir S3 client logic is **not** in GhostBox. GhostBox is downstream
observation only and is **not in the import path**. Genesis remains custody and
verification only.

## Run it

```bash
# genesis kernel must be on PATH (axm-build / axm-verify); dataset bytes here are
# a local fixture, so no boto3 / real Palantir call is needed.
python -m foundry_exit.run_exit --out foundry_exit_out
python -m pytest tests/test_foundry_exit_v0.py -q     # skips the seal/verify tests without the kernel
```

A captured run is in [`example_packet.md`](example_packet.md).

## Ontology Exit v0 (metadata-only, capture-driven)

A sibling entry point seals a tenant owner's **captured Ontology API v2**
responses (object types, outgoing link types, objects) into a genesis shard
whose **structure is queryable through Spectra** and whose **verbatim responses
are preserved byte-for-byte**. No Palantir code, credentials, or network calls
in our path — the owner runs the three GETs out of band and saves the JSON.

```bash
python -m foundry_exit.run_ontology_exit <capture_dir> --out ontology_exit_out
python -m pytest tests/test_foundry_ontology_exit.py -q   # kernel/duckdb tests skip cleanly if absent
```

See **[`ONTOLOGY_EXIT.md`](ONTOLOGY_EXIT.md)** for the three exact curl commands,
the capture-dir convention, the claims-vocabulary table, the honest evidence
tier, and what v0 deliberately does not do.

## Object model (the four planes)

- **Data plane** — `DatasetObject` / `DatasetExport`: dataset RID, branch/version, object paths, format, schema hints, **object checksums + sizes**, staged local path.
- **Ontology plane** — `OntologyObjectType`: object-type IDs, properties, links, backing dataset RIDs, action refs, security markings (**recorded for provenance only; permissions are not made portable**).
- **Lineage plane** — `LineageEdge`: upstream/downstream dataset, transform ref, produced object type.
- **AXM custody plane** — `FoundryExitManifest` → sealed `axm-hybrid1` shard. `shard_id` is **genesis-derived only**; Palantir IDs are preserved **as external IDs**, never as AXM custody IDs.

## Boundaries (enforced + tested)

- **S3 is the dataset-byte interface only.** Ontology and lineage come from explicit metadata inputs, never from S3.
- **Read-only against sources.** Neither adapter has any `put/write/upload/delete` method — no path writes back to Palantir. S3 credentials come from the environment (`AXM_S3_ACCESS_KEY` / `AXM_S3_SECRET_KEY`), never committed.
- **No GhostBox in the import path** — nothing here imports `ghostbox` (asserted at import + source level).
- **Custody stays genesis's.** Sealing uses the real genesis compiler; `shard_id` is genesis-derived; verification uses an out-of-band key and the frozen `PASS/FAIL/MALFORMED/NO_TRUSTED_KEY` taxonomy. (This reproduces the landed `SealedShard`/`VerifyStatus` seam's semantics without importing `ghostbox`.)

## Live receipts (this environment)

| Check | Result |
|---|---|
| Fixture import → sealed `axm-hybrid1` shard | **PASS** (`sh1_…`, genesis-derived) |
| Sealed bundle verify (out-of-band key) | **PASS** (exit 0) |
| Wrong key | **FAIL** |
| Missing key | **NO_TRUSTED_KEY** (before the CLI) |
| Palantir dataset/ontology IDs preserved verbatim | **yes** (external ids only) |
| S3 object checksums recorded + stable | **yes** (sha256 matches the real bytes) |
| ontology.json + lineage.json inside the sealed shard | **yes** |
| Exit test: verify with only shard bytes + oob pub | **PASS** (no Palantir, GhostBox, or importer) |
| Test suite | **14/14** |

**Evidence tier:** authorized-export readiness — the importer shape, sealed
bundle format, S3-compatible adapter boundary, ontology/lineage preservation,
and exit property proven **against fixtures**. A later live-credential run proves
actual Foundry extraction. Crypto backend is the pure-Python `dilithium-py`
fallback — functional, not load-proven. FalkorDB is not a dependency; the sealed
`graph.json`/`lineage.json` is the real requirement, and a FalkorDB loader can
come downstream.

## Control question

Can a user point AXM at authorized Foundry export surfaces, pull dataset bytes
from the S3-compatible plane, attach ontology and lineage from the metadata
plane, seal the whole exit bundle through genesis, and **still verify the
liberated record after Palantir, GhostBox, and the importer are removed**?

**v0 answer: yes** — proven live against the fixture.
