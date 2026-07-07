# The workflow-layer exit map — what leaving Foundry actually costs

*A companion to [`EXIT_READINESS_NHS.md`](EXIT_READINESS_NHS.md). That document
proves the part that already works: the ontology **structure + instance data**
exit. This document maps the part that does **not** work yet — the workflow
layer, which is where a platform's real lock-in lives — honestly, surface by
surface, so the "No — not yet" cells in the readiness table stop being a blank
and become a route someone can pick up.*

**Update 2026-07-07 — every plank now seals to an artifact, at an honest tier.**
All nine planks below produce a sealed, detached-verifiable genesis shard
(`axm-exit-ship` assembles them into one hull). But **sealed is not sovereign**,
and the tier states exactly what travels:

- **FULL** (4): the whole surface — ontology structure/data, pipeline schemas/DAG.
- **CONTRACT** (2): definitions + source, not the engine — actions, functions
  (`axm-logic-exit`).
- **SOURCE** (1): transform source verbatim, not the runtime
  (`axm-residual-exit source`).
- **ATTESTED** (2): what's exportable sealed + the rest honestly attested — apps
  (`axm-residual-exit apps`) and the deliberate non-port of permissions
  (`axm-residual-exit policy`).

So "9/9 sealed · 4 full-surface" is the honest headline — never "9/9 sovereign."
For each surface this document still gives, from Palantir's *published
documentation*, what it is, its wire shape, what a customer can take vs. rebuild,
and now which tier the exit reaches. The engines and runtimes remain the
customer's to rebuild; the map and every shard say so.

> **Sourcing & staleness.** Compiled 2026-07-07 from official Palantir docs
> (`palantir.com/docs`) and named secondary sources. Palantir's docs are
> undated living pages; endpoint *paths* (v2, `/api/v2/...`) have been stable
> for years (high confidence), but product surfaces (Pipeline Builder export,
> AIP naming, runtime limits) drift — **re-verify against current docs before
> relying on any specific claim.** Where a point is our reasoning rather than a
> Palantir statement, it is marked *(inference)*. Where we could not find a
> source, it is marked *(unverified)*.

---

## The one-line summary

You can export the **contracts and the source**. You cannot export the
**engines**. A customer leaving Foundry keeps the *what* — object/link/action/
query interfaces (published wire shapes) and hand-written transform/function
source (git-cloneable) — but must rebuild the *how*: the transforms runtime,
the actions/validation/writeback engine, the functions runtime, and the app
rendering layer, none of which have a portable, documented, self-contained
export. **"Export your ontology and data" is true today. "Export your workflow"
means "rebuild your workflow from recoverable parts," and this map says which
parts are recoverable.**

---

## The layers, most-exitable to least

### Layer 1 — Ontology structure + instance data  ·  **EXITS TODAY** ✅

Covered by the shipped exit ([`ONTOLOGY_EXIT.md`](ONTOLOGY_EXIT.md),
[`EXIT_READINESS_NHS.md`](EXIT_READINESS_NHS.md)). Object types, typed
properties, primary keys, links, cardinalities, and instance data — all pulled
from Palantir's published Ontology API v2 wire shapes, sealed into a genesis
shard, queryable through Spectra, detached-verifiable with an out-of-band key.
Proven end-to-end on synthetic data. This is the floor everything below builds
on.

---

### Layer 2 — Pipelines & data transforms  ·  **structure PROVEN, runtime no** 🟢🔴

> **Update 2026-07-07 — the structure half of this layer is now built and
> proven.** [`PIPELINE_EXIT.md`](PIPELINE_EXIT.md) ships `axm-pipeline-exit`: it
> consumes the published Datasets + Orchestration API v2 wire shapes and seals
> the **dataset schemas + the dependency DAG + build/schedule provenance** into a
> genesis shard, queryable through Spectra ("what feeds `flight_metrics`?"),
> detached-verifiable, proven end-to-end on a synthetic sample
> (`tests/test_foundry_pipeline_exit.py`, incl. the Spectra query test). What
> below is still **not** carried is the **runtime** — that half of this row
> remains 🔴, exactly as described.

**What it is.** Three authoring surfaces: **Pipeline Builder** (low-code,
graph/form UI — logic stored as pipeline config, not source files),
**Code Repositories** (a web IDE over a real **git** repo; Python/PySpark/
Java/SQL/R, `@transform` / `@transform_df` / `@incremental` decorators with
`Input`/`Output` dataset abstractions), and legacy **Code Workbook**.
[docs/building-pipelines/considerations-pb-cr] [docs/transforms-python/transforms-pipelines] *(high)*

**Published wire shape?** Partial.
- **Datasets API v2** exposes dataset **schemas** (`get/put schema`), reads, health. *(high)* [docs/api/datasets-v2-resources]
- **Orchestration API v2** exposes **builds, jobs, schedules** — run metadata, enough to partly reconstruct the DAG. *(high)* [docs/api/orchestration-v2-resources]
- **No API emits transform *logic* as a portable definition.** *(medium — documented absence)*
- **Lineage has no documented programmatic export** beyond an SVG image from the UI. *(medium-high)* [docs/data-lineage/faq]

**Take vs. rebuild.**
- **Take:** hand-written transform **source** via `git clone` of Code Repositories (short-lived read-only token; push to your own remote); dataset **schemas** (Datasets API); the **build/schedule DAG** metadata (Orchestration API). The *algorithms* are yours and readable. *(high)*
- **Rebuild:** the `transforms` **runtime + decorators**, **RID-based dataset bindings** (meaningless off-platform), **Spark orchestration**, and **incremental/lineage** semantics — all Foundry-proprietary services. Even cloned code won't run unmodified. *(high, part inference)*
- **Worst case: Pipeline Builder pipelines** don't export as source at all — only a **lossy, one-way codegen to Java** (`PipelineLogic.java`), where **UDFs and LLM calls become `todo` stubs** and the result "may not be identical." That's a rewrite, not a migration. *(high)* [docs/pipeline-builder/export-pipeline]

**Biggest lock-in in this layer:** the transforms runtime + orchestration/
incremental/lineage fabric — the framework the code is written *against*, not
the code itself. *(inference, high confidence)*

**Honest AXM exit shape — the schema/DAG half is now BUILT
([`PIPELINE_EXIT.md`](PIPELINE_EXIT.md)):** `axm-pipeline-exit` promotes
**dataset schemas + the dependency DAG + build/schedule provenance** to genesis
claims (queryable through Spectra) so the *dependency graph* survives even though
the runtime doesn't, and seals the verbatim API responses as content — the same
seal the ontology exit uses. It is explicit on the tin that the sealed result is
a **portable, verifiable record of the pipeline, not a runnable pipeline.**
Sealing the **cloned transform source** verbatim is now also built
(`axm-residual-exit source`, SOURCE tier). Still the customer's to do: re-hosting
the compute (Spark/dbt/Airflow) on their own infrastructure. AXM makes the
pipeline *auditable and portable as evidence*; it does not resurrect the Foundry
runtime.

---

### Layer 3 — Actions, Functions & Queries (ontology logic)  ·  **CONTRACT sealed, engines no** 🟩🔴

> **Update 2026-07-07 — the contract half is built.** `axm-logic-exit` seals
> action + query **definitions** (published Actions/Query Types wire shapes) as
> genesis claims and function **source** verbatim, attesting on the shard that
> the Actions engine and Functions runtime are NOT carried
> (`tests/test_foundry_logic_exit.py`). CONTRACT tier: the interface and source
> travel; the engines remain the customer's to rebuild.

**What it is.** **Actions** are the ontology's write operations — typed
parameters → ontology edits (create/modify/delete objects & links) landing in a
writeback dataset, gated by **submission criteria** (business-logic
validation). **Functions/Queries** are server-side logic in TypeScript or
Python (Queries = the read-only, side-effect-free subset). *(high)*
[docs/action-types/overview] [docs/functions/overview]

**Published wire shape?** More than the pipeline layer.
- **Action definitions** (api name, parameters, types) via `List/Get Action Types`; **`POST /api/v2/ontologies/{ontology}/actions/{action}/apply`** to invoke; plus a `Validate Action` endpoint. *(high)* [docs/api/ontologies-v2-resources/actions/apply-action]
- **Query metadata** (`Get Query Type`) + **`.../queries/{q}/execute`** to invoke. *(high)*
- **Function/Query source** is git-cloneable from Code Repositories. *(high)*
- **The OSDK** generates typed clients (TS/Python/Java + an **OpenAPI spec**) over objects, links, actions, and queries — so the *wire contract* is well-documented and reconcilable. But OSDK treats "**Foundry as your backend**": it's a typed client against Foundry's hosted APIs, not a runtime you can take. *(high)* [docs/ontology-sdk/overview]

**Take vs. rebuild.**
- **Take:** the **interface** of every action/query/object (published + OSDK/OpenAPI), and the **authored source** of functions/queries (git). The "what the logic is" is largely recoverable in human-readable form. *(medium-high)*
- **Rebuild:** the **Actions engine** — submission-criteria expressions, the rules→edits transformation, validation, and writeback are Foundry-internal and **not emitted as an executable spec**; you get the parameter surface, not "here is exactly what this action does." And the **Functions runtime** (isolated, ontology-aware generated bindings, decorators) doesn't run off-platform. *(medium, inference)*

**Biggest lock-in in this layer:** the **Actions engine** — the
submission-criteria + rules→edits + writeback machinery. It's the governance
logic that makes the ontology *safe to write to*, and it has no portable
export. *(medium)*

**Honest AXM exit shape — BUILT (`axm-logic-exit`):** captures **action + query
definitions** (published metadata) and **function source** (git) as sealed content
+ genesis claims, so the *interface and the intent* are preserved and verifiable. The
submission-criteria / effect logic that has no published spec would have to be
**re-expressed by the customer** at the destination against their own write
path — AXM can seal the *evidence of what the action was* (parameters,
validation intent, invocation history if captured), not reconstitute the engine
that applied it.

---

### Layer 4 — Applications (Workshop / Slate) & AIP  ·  **the deepest lock-in** 🔴

**What it is.** **Workshop** is Foundry's flagship app builder — modules →
widgets → variables bound to the ontology, edited entirely in a visual builder.
**Slate** is the older HTML/CSS/JS builder. **AIP** (AIP Logic; AIP Agent
Studio, recently renamed **AIP Chatbot Studio**; AIP Assist) layers LLM logic
and agents on the ontology. *(high — but AIP naming is evolving; re-verify)*
[docs/workshop/overview] [docs/logic/overview]

**Published wire shape?** Mostly **none**.
- **Workshop:** no export file, **no published definition wire shape, no read API.** Cross-environment movement only via Marketplace packaging (below). The internal serialized format is **unpublished (unverified).** *(high for the documented absence)* [docs/questions-answers/workshop]
- **Slate:** the **one** native builder with a documented **JSON export** (widgets, functions, query logic, events, variables, styles) — but it's **logic-only** (excludes data sources, objects, Actions, assets) and **re-imports meaningfully only into another Foundry** with the same ontology. Partial, not self-contained. *(high)* [docs/slate/applications-import-export]
- **AIP:** no definition export; only a **runtime curl hatch** to *invoke* an AIP Logic function from outside (and not even that when it returns ontology edits). Fully platform-bound. *(high)* [docs/logic/faq]
- **Marketplace / Foundry DevOps "products":** can package Workshop/AIP/Functions/ontology into an installable bundle — but Palantir's own docs disclaim it: *"exported as a file, which should only be used for short-lived transport and not for permanent storage. We hold the right to introduce breaking changes to the format of this file, making it unable to be imported."* **Foundry-to-Foundry only; not a portable/parseable/runnable-elsewhere format.** TypeScript V1 functions ship in it **without user-viewable source**. *(high)* [docs/foundry-devops/export-import-products]

**Take vs. rebuild.** Ranked most→least portable:
1. **OSDK / Developer Console apps + Custom Widgets** — genuinely portable *because you own the source*; only the ontology data dependency ties you to Foundry. This is Palantir's own intended durable route. *(high)*
2. **Slate** — real but partial JSON export; re-imports only into Foundry. *(high)*
3. **Workshop** — no published export, no read API, rendering runtime 100% Foundry. *(high)*
4. **AIP** — fully platform-bound, newest, least stable. *(high)*

**Biggest lock-in overall:** **Workshop + AIP.** There is no open, documented,
self-contained export wire shape, and the runtime that renders these apps is
exclusively Foundry. Leaving means **rebuilding the app/AI layer on a new
stack** — the OSDK "own your code" path is the intended durable route, not an
export.

**Honest AXM exit shape — BUILT as ATTESTED (`axm-residual-exit apps`):** AXM is
honest that there is little to *seal* — no published definition for Workshop/AIP.
So the exit seals what *does* have a documented export (Slate JSON, verbatim) and
records an explicit **no-export attestation** for Workshop/AIP on the shard. The
defensible forward move stays **build on OSDK** (your own code against the
published ontology API, which the Layer-1 exit already makes portable), so the
app layer you build is *yours from the start* and never needs exiting.

---

### Layer 5 — Permission / security model  ·  **deliberately NOT carried** ⚫

**What it is.** A layered model: **mandatory** controls that propagate with data
via provenance — **Markings** (must hold all markings on a resource),
**Organizations** (markings enforcing silos), **restricted views** +
**granular/attribute-based policies** (row-level), **classification-based
access**, **object security policies**, and **Purpose-Based Access Controls**
(you apply for access to a *Purpose* with a written rationale) — plus
**discretionary** project roles (Owner/Editor/Viewer/Discoverer). *(high)*
[docs/security/markings] [docs/security/projects-and-roles] [docs/security/overview]

**Published wire shape?** The *primitives* are individually manageable via the
Administration and Filesystem APIs (users, groups, orgs, markings, resource
roles). But there is **no documented single-call export of the whole
authorization graph** (all markings + granular policies + object policies + PBAC
purposes + role bindings) as one portable artifact. *(medium→low, absence of
evidence)*

**Why AXM does not port it — on purpose.** This is a stated anti-goal, not a
gap. The model is tightly coupled to Foundry-specific constructs (Markings-as-
Organizations, Purposes, provenance propagation) with no clean 1:1 in a generic
destination *(inference, well-grounded)*, and — more to the point of this whole
project — **porting a vendor's fine-grained "who/why can see what" graph is
porting the surveillance apparatus, not escaping it.** The escape hatch
reconstructs authorization under the **customer's own policy** at the
destination. AXM carries the *records*, verifiable and sovereign; it does not
make anyone's ACL model portable. The consent gate
([`EXIT_READINESS_NHS.md`](EXIT_READINESS_NHS.md) §4) is the only access
decision AXM honors, and it belongs to a human data controller, never to the
machine.

---

## The readiness table, filled in

| Foundry surface | Published wire shape | Take | Rebuild | AXM exit today |
|---|---|---|---|---|
| Ontology structure | **Yes** (Ontology API v2) | structure | — | ✅ shipped |
| Instance data | **Yes** (Ontology API v2) | data + verbatim responses | — | ✅ shipped |
| Pipeline / transform source | git clone | algorithms | runtime, bindings, orchestration | 🔧 **SOURCE** (`axm-residual-exit source`) |
| Pipeline (Builder) | lossy Java codegen | little | most (rewrite) | 🟡 mapped |
| Dataset schemas / DAG | **Yes** (Datasets/Orchestration API) | schemas + DAG | — | 🟢 **proven** (`axm-pipeline-exit`) |
| Lineage | image only | — | all | 🟡 mapped |
| Action definitions | **Yes** (Action Types API) | interface | rules/validation/writeback engine | 🟩 **CONTRACT** (`axm-logic-exit`) |
| Function/Query defs + source | git clone + Query API | source + contract | runtime | 🟩 **CONTRACT** (`axm-logic-exit`) |
| Workshop apps | **None** | — | all (rebuild on OSDK) | 📝 **ATTESTED** no-export (`axm-residual-exit apps`) |
| Slate apps | partial JSON | logic only | data/asset wiring | 📝 **ATTESTED** Slate JSON sealed (`axm-residual-exit apps`) |
| AIP logic/agents | **None** (curl invoke only) | — | all | 📝 **ATTESTED** no-export (`axm-residual-exit apps`) |
| Permission model | primitives only | — | reconstruct under own policy | 📝 **ATTESTED** non-port (`axm-residual-exit policy`) |

Legend: ✅ exits today · 🟡 recoverable in part, mapped here, not yet built ·
🔴 no self-contained export exists · ⚫ deliberately not carried.

---

## Why this matters in 2026 (public facts)

- **DORA** (Regulation (EU) 2022/2554, applicable 17 Jan 2025), **Article
  28(8)**: financial entities must hold exit strategies for ICT services
  supporting critical/important functions that are *"comprehensive, documented
  and ... sufficiently tested and reviewed periodically,"* with identified
  alternatives and transition plans. *("Annual" is supervisory expectation, not
  the literal Article — don't conflate.)* A sealed, detached-verifiable exit
  shard **is a test result** an auditor can verify. *(high; cite EUR-Lex for
  the formal text)*
- **NHS Federated Data Platform** (Palantir; £330m ceiling, £182.2m awarded;
  3+2+1+1 term): the **initial 3-year term ends early 2027** (sources say
  Feb–March 2027; verify against the contract notice), and continuation
  requires DHSC/Ministers to **actively trigger** the first extension. A Health
  Minister has said the decision comes **"later this year" (2026)**. 2026 is the
  decision year. *(high)*
- The **Commons Science, Innovation & Technology Committee** urged exercising
  the FDP break clause, warned of *"dangerous levels of supplier lock-in,"* and
  called for a **fully costed exit plan by end of 2026**. *(high)*
  [computerweekly.com/news/366643883]

Public reporting, cited so the claims can be checked; none of it is an
endorsement, and none of it involves any real data touching this repository.

---

## Evidence tier (stated plainly)

`foundry-workflow-layer-exit-map` — a **documentation** artifact:
- reconciled against Palantir's **published** documentation as of 2026-07-07;
- **implementation status is explicit per row:** Layer 1 (ontology) and the
  **structure half of Layer 2** (schemas + DAG + provenance, `axm-pipeline-exit`)
  are built and proven; the Layer 2 **runtime** and Layers 3–5 are mapped, not
  built;
- confidence levels and *(inference)* / *(unverified)* flags are carried inline
  from the underlying research and **must not be silently upgraded**;
- product surfaces drift — **re-verify before relying on any specific endpoint,
  export path, or date.**

The honest headline stays the same as the demo's: the ontology + data exit is
**proven, not deployed**. The workflow layer is **mapped, not built** — and now
that it's mapped, the next person to clone this repo knows exactly where the
frontier is and what crossing it costs.
