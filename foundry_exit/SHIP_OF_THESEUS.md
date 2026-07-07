# The Palantir Ship of Theseus

*If you replace every plank of a ship, one at a time, is it still the same ship?
The exit answers yes — and that is the whole strategy. You do not leave Foundry
in one dramatic move. You replace it plank by plank — the ontology, then its
data, then the pipeline schemas, then the dependency DAG — each swapped for a
sovereign, sealed, detached-verifiable equivalent. The vessel keeps sailing the
entire time. One day the last plank is yours, Palantir is gone, and your system
never stopped running.*

`axm-exit-ship` is the ship: it runs whichever plank-exits have captures and
seals **one ship manifest** — a genesis shard recording every plank of a Foundry
deployment, its sovereign-replacement status, and the child exit shards that have
actually swapped a plank this run. It is honest about which planks are yours yet.

## The planks, and who owns each today

| Plank | Status | Sovereign replacement |
|---|---|---|
| Ontology structure | ✅ **sovereign** | genesis claims via `axm-exit` |
| Ontology instance data | ✅ **sovereign** | sealed verbatim content + declared/captured counts |
| Dataset schemas | ✅ **sovereign** | genesis claims via `axm-pipeline-exit` |
| Dependency DAG + provenance | ✅ **sovereign** | genesis claims, Spectra-queryable |
| Transform runtime | 🔧 **you rebuild** | no export exists — Spark/dbt/Airflow on your infra |
| Actions engine | 🟡 **mapped** | definitions via published Actions API; engine rebuilt |
| Functions / Queries | 🟡 **mapped** | source git-cloneable; runtime rebuilt |
| Applications (Workshop / AIP) | 🔴 **rebuild forward** | no export; build on OSDK, own your code |
| Permission / security model | ⚫ **not carried, by design** | reconstructed under your OWN policy, never ported |

**4 of 9 planks are sovereign and sealed today.** That is not a shortfall hidden
behind a number — it is the honest hull. The four that are swapped hold the
load: your *meaning* (the ontology), your *data*, and the *shape and flow* of
that data (schemas + DAG). The runtime and app planks have **no export that can
exist** — you rebuild them, on your terms, and the map says so. The permission
plank is the one you must **not** carry: porting a vendor's who-sees-what graph
is porting the surveillance, not escaping it. See
[`WORKFLOW_EXIT_MAP.md`](WORKFLOW_EXIT_MAP.md) for the full, sourced breakdown.

## The ship still floats before it is finished

This is the point of the Ship of Theseus framing over a big-bang migration: you
do not need every plank swapped to have a valid, verifiable, sovereign vessel.
The moment you seal the ontology, you have a sovereign record that verifies with
nothing but its own bytes. Seal the pipelines and the record grows. The ship
manifest ties them into one hull whose custody is a single genesis `sh1_`, and
which `axm-verify` accepts detached — Palantir removed, AXM removed.

## Run it

```bash
# Demo ship — runs both synthetic samples, seals one hull:
axm-exit-ship --out ./ship_out

# Real ship — point at a dir with your own captures:
#   my_exit/ontology/   (objectTypes.json, linkTypes/, objects/)
#   my_exit/pipeline/    (datasets.json, schemas/, builds.json, jobs/, schedules.json)
axm-exit-ship ./my_exit --out ./ship_out
```

Real output (verbatim, demo mode):

```
- ship shard id: sh1_1148091ea29627de9d38ddc6c5aae042924affee92f89a4f5e8f72802f681de7
- verification: PASS (exit 0) · sovereign planks: 4 / 9
- ontology → shard sh1_f8e0728f…  (verify PASS)
- pipeline → shard sh1_46974522…  (verify PASS)
```

Real mode never falls back to a synthetic sample: under a real capture root, only
the exits whose subdir is present run, so a real ship is never quietly mixed with
demo data. The ship manifest is itself queryable through Spectra — "which planks
are sovereign?" — proven in
`tests/test_foundry_ship_of_theseus.py::test_ship_is_queryable_through_spectra`.

## Evidence tier (honest)

The ship manifest is a sealed, detached-verifiable genesis shard. Plank statuses
are the honest ledger from `WORKFLOW_EXIT_MAP.md`: PROVEN planks have tested
exits; MAPPED / NO_EXPORT / CUSTOMER_REBUILD / ANTI_GOAL planks are **not** sealed
and say so on the manifest. In demo mode the child shards are synthetic samples,
not a live tenant. Proven, not deployed — and the ship counts its own planks
honestly, in public, on the tin.
