# Pipeline Exit v0 — the dependency DAG leaves the silo, sealed

*The second workflow-layer frontier, crossed. The ontology exit
([`ONTOLOGY_EXIT.md`](ONTOLOGY_EXIT.md)) took the ontology structure + data out.
This takes the next layer down: a customer's **dataset schemas** and the
**dependency DAG** — the map of how their data actually flows — pulled from
Palantir's published Datasets + Orchestration API v2, sealed into a genesis
shard, queryable through Spectra, detached-verifiable with an out-of-band key.*

**Structure only, and it says so everywhere.** This carries what the published
wire shapes actually expose — schemas, the `dataset A feeds dataset B` graph,
and build/schedule provenance — all tier 1. It does **not** carry the transform
**runtime** (the `transforms` framework, decorators, Spark orchestration, the
incremental engine). Those are rebuilt on the customer's own infrastructure. See
[`WORKFLOW_EXIT_MAP.md`](WORKFLOW_EXIT_MAP.md) Layer 2 for exactly what travels
and what doesn't.

## What it consumes (published wire shapes)

A tenant owner captures these from **their own tenant, with their own token**,
out of band — no Palantir code and no credentials live in this repo:

| Capture file | Palantir API (published) |
|---|---|
| `datasets.json` | Datasets API v2 — `get-dataset` (list) |
| `schemas/<name>.json` | Datasets API v2 — `get-dataset-schema` (`fieldSchemaList`) |
| `builds.json` | Orchestration API v2 — builds |
| `jobs/<buildName>.json` | Orchestration API v2 — `list-jobs-of-build` (resolved input/output refs) |
| `schedules.json` | Orchestration API v2 — schedules *(optional)* |

The loader is **tolerant** of unknown/extra fields (Palantir may add them; we
keep the verbatim dict, never crash) and **strict** about required ones (a clear
error names the file and the missing key). Dataset RIDs in job I/O are resolved
to readable names; an RID not present in `datasets.json` is kept as an
obviously-external `external:<tail>` label — never dropped, never invented.

## What it seals

```
raw_flights ─┐
             ├─▶ flights_clean ─▶ flight_metrics
airport_ref ─┘
```

A genesis shard whose `content/` holds the **verbatim** capture files
(byte-for-byte) plus `source.txt`, and whose claim graph makes the pipeline
queryable through Spectra:

| Claim | Meaning | Tier |
|---|---|---|
| `dataset/X has_field field/X.c` | X has column c | 1 |
| `field/X.c has_type "<type>"` | c's declared type | 1 |
| `dataset/A feeds dataset/B` | the DAG edge (from build job I/O) | 1 |
| `dataset/B produced_by build/{b}` | which build produced B | 1 |
| `build/{b} triggered_by schedule/{s}` | what triggers the build | 1 |

No fabricated metrics — no row counts, no runtime, no lineage beyond what the
captured build I/O exposes. If a deployment doesn't expose resolved job I/O, the
edges are simply absent, not inferred.

## Run it

```bash
axm-pipeline-exit samples/pipeline_exit_synthetic --out ./pipeline_exit_out
# or: python -m foundry_exit.run_pipeline_exit samples/pipeline_exit_synthetic --out ./pipeline_exit_out
```

Real output (verbatim, from the synthetic sample):

```
- shard id: sh1_46974522e35b87561cd682d3a1cf9fe54ba433d727f7ff5dce953dd63595d8d3
- suite: axm-hybrid1 · verification: PASS (exit 0)
- datasets: 4 · dependency DAG edges: 3 · entities: 22 · claims: 37
- raw_flights   → flights_clean   (via build build_flights_clean)
- airport_ref   → flights_clean   (via build build_flights_clean)
- flights_clean → flight_metrics  (via build build_flight_metrics)
```

Then mount the sealed shard into `axiom_runtime.SpectraEngine` and ask it
questions — "what feeds `flight_metrics`?", "what's the transitive upstream?",
"what columns does `flights_clean` have?" — all proven in
`tests/test_foundry_pipeline_exit.py::test_sealed_pipeline_is_queryable_through_spectra`.

## Custody invariant (same as the ontology exit)

No Palantir `rid` ever appears in the sealed `manifest.json`. The custody id is
always the genesis-derived `sh1_` over the manifest bytes. Palantir identifiers
are external ids carried verbatim as sealed content only — never the shard's
identity.

## Evidence tier (stated plainly)

`foundry-pipeline-wire-shape-reconciled`, on a **synthetic** sample:
- reconciled against Palantir's **published** Datasets + Orchestration API v2
  wire shapes;
- proven end-to-end here on an invented flight pipeline — **no real data, ever**;
- **not** run against an authorized live tenant;
- carries pipeline **structure** — schemas, dependency DAG, provenance — **not**
  the transform runtime.

Proven, not deployed. One more layer of the workflow moat is now a real,
verifiable, queryable artifact instead of a line on a map. The runtime is still
the customer's to rebuild — and this document, and every claim's tier, says so.
