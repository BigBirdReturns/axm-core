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

## The planks — 9/9 sealed, each at its honest tier

Every plank now produces a sealed, detached-verifiable artifact. But **sealed is
not sovereign**, and the manifest never pretends otherwise. Each plank carries a
*tier* that states exactly what travels:

| Plank | Tier | What travels |
|---|---|---|
| Ontology structure | ✅ **FULL** | genesis claims via `axm-exit` |
| Ontology instance data | ✅ **FULL** | sealed verbatim content + declared/captured counts |
| Dataset schemas | ✅ **FULL** | genesis claims via `axm-pipeline-exit` |
| Dependency DAG + provenance | ✅ **FULL** | genesis claims, Spectra-queryable |
| Actions | 🟩 **CONTRACT** | definitions sealed via published Actions API (`axm-logic-exit`); engine rebuilt |
| Functions / Queries | 🟩 **CONTRACT** | query defs + function source sealed; runtime rebuilt |
| Transform source & runtime | 🔧 **SOURCE** | transform source sealed verbatim (`axm-residual-exit source`); runtime rebuilt on your infra |
| Applications (Workshop / Slate / AIP) | 📝 **ATTESTED** | Slate JSON sealed; Workshop/AIP no-export attested (`axm-residual-exit apps`) |
| Permission / security model | 📝 **ATTESTED** | deliberate non-port attestation (`axm-residual-exit policy`); authorization under your OWN policy |

**9 of 9 planks are sealed; 4 carry the full surface.** The distinction is the
whole point. The four FULL planks hold the load — your *meaning* (the ontology),
your *data*, and the *shape and flow* of that data (schemas + DAG). The CONTRACT
planks carry the definitions and source but not the engines that run them. The
SOURCE plank carries your transform code verbatim but not the runtime — no export
of a runtime can exist. The ATTESTED planks seal what *is* exportable (Slate JSON)
and honestly attest the rest; the permission plank is sealed as a **deliberate
non-port** — porting a vendor's who-sees-what graph is porting the surveillance,
not escaping it, and now that decision is itself a verifiable record. See
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
- ship shard id: sh1_4c06f47e13ebe764fa5d7738c6e469a75f79575f12815ef4b806680cda9ef4b8
- verification: PASS (exit 0)
- planks sealed: 9 / 9 · tiers — FULL:4 · CONTRACT:2 · SOURCE:1 · ATTESTED:2
- ontology / pipeline / logic / residual:source / residual:apps / residual:policy → all verify PASS
```

The demo runs all six plank-exits (`axm-exit`, `axm-pipeline-exit`,
`axm-logic-exit`, and `axm-residual-exit` for source/apps/policy). Real mode never
falls back to a synthetic sample: under a real capture root, only the exits whose
subdir is present run, so a real ship is never quietly mixed with demo data. The
ship manifest is itself queryable through Spectra — "which planks are FULL tier?"
— proven in
`tests/test_foundry_ship_of_theseus.py::test_ship_queryable_by_tier_through_spectra`.

## Evidence tier (honest)

The ship manifest is a sealed, detached-verifiable genesis shard. Plank statuses
are the honest ledger from `WORKFLOW_EXIT_MAP.md`: PROVEN planks have tested
exits; MAPPED / NO_EXPORT / CUSTOMER_REBUILD / ANTI_GOAL planks are **not** sealed
and say so on the manifest. In demo mode the child shards are synthetic samples,
not a live tenant. Proven, not deployed — and the ship counts its own planks
honestly, in public, on the tin.
