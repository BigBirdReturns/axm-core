# SYNTHETIC pipeline-exit sample

**This directory contains NO real data.** It is an *invented* four-dataset
flight pipeline, expressed in Palantir's published **Datasets API v2** and
**Orchestration API v2** wire shapes, used to prove the pipeline-layer exit
(see [`../foundry_exit/PIPELINE_EXIT.md`](../../foundry_exit/PIPELINE_EXIT.md)).

- Every `rid` is `...5ynth-...` — deliberately not a valid Foundry RID. No real
  dataset, build, schedule, or tenant is represented.
- Layout (OUR capture convention; FILE CONTENTS are Foundry's documented shapes):
  - `datasets.json` — Datasets API v2 `get-dataset` list (4 datasets)
  - `schemas/<name>.json` — `get-dataset-schema` (`fieldSchemaList`) per dataset
  - `builds.json` — Orchestration API v2 builds
  - `jobs/<buildName>.json` — `list-jobs-of-build` with resolved input/output refs
  - `schedules.json` — Orchestration API v2 schedules

The dependency DAG this proves:

```
raw_flights ─┐
             ├─▶ flights_clean ─▶ flight_metrics
airport_ref ─┘
```

Run it:

```
axm-pipeline-exit samples/pipeline_exit_synthetic --out ./pipeline_exit_out
# or: python -m foundry_exit.run_pipeline_exit samples/pipeline_exit_synthetic --out ./pipeline_exit_out
```

It seals **structure only** — schemas, the DAG, and build/schedule provenance.
It does **not** carry the transform runtime. See
[`../foundry_exit/WORKFLOW_EXIT_MAP.md`](../../foundry_exit/WORKFLOW_EXIT_MAP.md)
Layer 2 for exactly what travels and what must be rebuilt.
