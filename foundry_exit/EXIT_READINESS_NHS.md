# Exit readiness — the clean-break channel (NHS / Foundry worked example)

*A lifeboat on the deck, empty and seaworthy, until someone with the authority
to board it decides to. Built on synthetic data; it carries no one until a real
data controller says go.*

This is the honest, buildable shape of "the separate channel for the day a
Foundry customer makes a clean break." It is **not** a shadow of anyone's live
system, it holds **no real data**, and it activates on **explicit
authorization**, never covertly. Those three properties are the whole point —
a covert parallel intake of real records would be the surveillance apparatus
this project exists to oppose, not the escape hatch. The escape hatch is
consented, labeled, and verifiable.

## What this is / is NOT

- **IS:** a demonstration, on an *invented* NHS-shaped ontology in Palantir's
  *published* Ontology API v2 wire shape, that a customer's ontology + data can
  be pulled into a genesis-sealed, detached-verifiable, queryable sovereign
  record that needs neither Palantir nor AXM nor any vendor to stay valid.
- **IS NOT:** a live-tenant run, a copy of any real deployment, or a handler of
  any real patient data. The sample (`samples/nhs_synthetic_exit/`) is
  synthetic — patient references are `SYN-`-prefixed and deliberately not valid
  NHS numbers; every name is an obvious placeholder.

## The four, solved as a lifeboat you offer — not a tap installed in secret

### 1. Map the exit — honestly, including the gaps

| Foundry surface | AXM exit covers it? |
|---|---|
| Ontology structure — object types, properties, primary keys, links, cardinalities | **Yes** — promoted to genesis claims, queryable via Spectra |
| Object instance data | **Yes** — sealed as verbatim content; counts as claims (declared vs captured, never hidden) |
| Verbatim API responses (audit trail of what was pulled) | **Yes** — sealed byte-for-byte |
| Pipelines / transforms (code) | **No — not yet.** The operational moat. |
| Actions / Functions | **No — not yet.** |
| Workshop / Slate applications | **No — not yet.** |
| Permission / security model | **No — deliberately not carried.** No vendor ACLs made portable. |

"Export your ontology and data" is true today. "Export your workflow" is not —
and this table says so on the tin.

### 2. The readiness architecture — the customer's own hands, own authority

The customer, from **their own tenant with their own credentials**, runs three
GETs against Palantir's public Ontology API v2 (`objectTypes`,
`outgoingLinkTypes`, `objects`), saves the JSON, and runs one command:

```
python -m foundry_exit.run_ontology_exit <capture_dir> --out <dir>
```

Out the other side: a genesis-sealed shard whose structure is queryable through
Spectra (`axm ask`), whose verbatim API responses are preserved, and which
`axm-verify` accepts with only the shard bytes + an out-of-band key — Palantir
removed, AXM removed, everything removed. The channel is ready and standing;
nothing flows through it until step 4.

### 3. Timeline + compliance hook (public facts only)

- The NHS Federated Data Platform's first contract term ends **March 2027**;
  continuation requires the Department of Health to actively trigger the first
  extension. 2026 is the decision year. *(Public reporting; see Hansard / the
  Commons Science & Technology Committee.)*
- **DORA** (in force Jan 2025) requires EU/UK financial entities to hold exit
  strategies for critical ICT providers that are **tested**, reviewed annually;
  inspectors ask for the exit strategy *with its most recent test results.*
- **A sealed, detached-verifiable exit shard IS a test result** — a
  cryptographic artifact an auditor or a board can verify, not a slide deck.
  Standing this channel up in advance turns "we could leave" into "we have
  tested leaving, here is the proof."

### 4. The consent gate — what makes it an exit, not an exfiltration

The channel is inert by design. It carries real records only when a **data
controller with the lawful authority to release them** authorizes it — and that
authorization is itself a sealed record, subject to the same custody as
everything else. No "rogue" branch, no covert duplication, supplies that
authority; there is no version of unauthorized that does. In this repo the gate
is honored the only honest way available without a real controller: **the data
is synthetic.** Point it at something real only when a real controller decides —
the same risk-changing, human-owned decision the continuity charter reserves for
the operator, never drifted into.

## The proof (this repo, reproducible)

Synthetic NHS ontology → real ontology exit → sealed → verified detached →
queried through Spectra, no Palantir in the loop:

```
SEALED   : sh1_3b46e8ea6e4a9515d5582c9d2b556d0d0a00600885d70088e6c1f167ef50664d
verify   : detached PASS (out-of-band key)
object types : ['Clinician', 'Encounter', 'Patient', 'Ward']
links        : ['Encounter -> Patient', 'Encounter -> Ward']
properties   : 18 typed property claims
declared vs captured (partial, honestly): [('instance_count_declared', '1200'),
                                           ('instances_captured', '5')]
```

Reproduce:

```
python -m foundry_exit.run_ontology_exit samples/nhs_synthetic_exit --out ./nhs_exit_out
# then mount the sealed shard into axiom_runtime.SpectraEngine and query it
# (see tests/test_foundry_ontology_exit.py::test_sealed_ontology_is_queryable_through_spectra)
```

## Evidence tier (stated plainly)

`foundry-ontology-wire-shape-reconciled`, on a **synthetic** sample:
- reconciled against Palantir's *published* Ontology API v2 wire shapes;
- proven end-to-end here on invented NHS-shaped data — **no real patient data,
  ever**;
- **not** run against an authorized live tenant;
- covers ontology **structure + instance data**, not the workflow layer
  (pipelines, Actions, apps, permissions).

Proven, not deployed. The channel is real and it works. Whether anyone ever
sails it is a decision for a customer with a real tenant and the authority to
make it — and that is exactly as it should be.
