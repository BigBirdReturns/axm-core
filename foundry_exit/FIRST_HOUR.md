# Your first hour — a Foundry customer, from clone to sealed exit

*You run a Foundry tenant. You want to know, concretely and today, that your
ontology can leave in a shape that outlives Foundry, AXM, and every vendor in
between. This is the hour that proves it — on a synthetic sample first, then
pointed at your own capture only when you decide. Every command below was run
to produce this file; the output is real, not illustrative.*

> **You will not touch real data in this hour, and neither will we.** The walk-
> through runs against an invented, synthetic NHS-shaped sample checked into
> this repo (`SYN-`-prefixed refs, placeholder names, no real patient data
> ever). Pointing the exit at a live tenant is step 6 — a consent-gated
> decision that belongs to a data controller with lawful authority, never to
> this tooling. See [`EXIT_READINESS_NHS.md`](EXIT_READINESS_NHS.md) §4.

---

## What you need

- **Python 3.10+** and **git**. That's it.
- **No `graphkdf`, no `clarion`.** The topology-bound encryption package has its
  own crypto deps that the exit path never imports — it is a *separate* install
  target and is not on this path. If a dependency error mentions `graphkdf`, you
  installed the wrong extra; the exit does not need it.

## Minute 0–10 — Install the kernel and the exit

The exit seals with the genesis kernel (`axm-hybrid1` = Ed25519 + ML-DSA-44) and
verifies detached with `axm-verify`. Install the kernel first — it puts
`axm-build` and `axm-verify` on your PATH:

```bash
git clone https://github.com/BigBirdReturns/axm-genesis
git clone https://github.com/BigBirdReturns/axm-core

# Kernel. [mldsa-compat] = pure-Python ML-DSA (dilithium-py), zero system deps.
# Prefer the liboqs C backend? use [mldsa] instead.
pip install -e './axm-genesis[mldsa-compat]'

# The exit. Installs the `axm-exit` console script.
pip install -e ./axm-core
```

Confirm the kernel and the exit are both on PATH:

```bash
which axm-build axm-verify axm-exit
```

## Minute 10–20 — Run the exit against the synthetic sample

The sample lives at `axm-core/samples/nhs_synthetic_exit/`. One command seals
and self-verifies it:

```bash
axm-exit axm-core/samples/nhs_synthetic_exit --out ./nhs_exit_out
```

Real output from that command (verbatim):

```
- shard id: sh1_3b46e8ea6e4a9515d5582c9d2b556d0d0a00600885d70088e6c1f167ef50664d
- suite: axm-hybrid1 · merkle_root: 4798b623ad1ab2b185f896961ae2208063aa9c746d3e4613af34916c0f5dbdb0
- verification: PASS (exit 0) via axm-verify (real genesis kernel)
## Counts
- object types: 4      (Patient, Encounter, Ward, Clinician)
- link types: 2        (Encounter → Patient, Encounter → Ward)
- entities: 24 · claims: 48
- sealed content files (verbatim): 3
## Instances (declared vs captured)
- Patient: 5 row(s) captured, totalCount declared: 1200
```

That last line is the honesty the whole system is built on: a partial capture is
**shown** (5 of a declared 1200), never smoothed over.

## Minute 20–35 — Verify it yourself, detached, with nothing but the bytes

The point of a sovereign record is that it verifies **without the tool that made
it**. The run wrote an out-of-band public key alongside the shard. Verify with
only the shard bytes and that key:

```bash
axm-verify shard ./nhs_exit_out --trusted-key <path/to/publisher.pub>
# → PASS
```

No Palantir in the loop. No AXM service in the loop. Just the record and a key
you hold. That is the property that survives 20 or 30 years.

## Minute 35–60 — Point it at *your* ontology (when, and only when, you decide)

The exit consumes saved responses from Palantir's **published** Ontology API v2.
From **your own tenant, with your own token**, run three GETs and save the JSON
in this layout (mirror the sample):

```
my_capture/
  objectTypes.json            # ListObjectTypesV2Response
  linkTypes/<Type>.json       # ListOutgoingLinkTypesResponseV2, per object type
  objects/<Type>.json         # ListObjectsResponseV2, per object type
```

```bash
# out-of-band, your credentials, your tenant — never in this repo
curl -H "Authorization: Bearer $YOUR_TOKEN" \
  "https://<your-host>/api/v2/ontologies/<ont>/objectTypes" > my_capture/objectTypes.json
# …then outgoingLinkTypes and objects per type, same shape as the sample…

axm-exit ./my_capture --out ./my_exit_out
axm-verify shard ./my_exit_out --trusted-key <your_oob_pub>
```

Out the other side: a genesis-sealed shard whose structure is queryable through
Spectra (`axm ask`), whose verbatim API responses are preserved byte-for-byte,
and which verifies detached forever. Palantir removed, AXM removed, everything
removed.

---

## What you just proved, and what you didn't

**Proved:** your ontology **structure + instance data** can leave Foundry into a
sealed, detached-verifiable, queryable record that needs no vendor to stay
valid — reproducibly, on your own machine, in under an hour.

**Did not prove:** that your *workflows* came with it. Pipelines, Actions,
Functions, Workshop/AIP apps, and the permission model are **not** exited by
this — and pretending otherwise is exactly the overclaim this project refuses.
[`WORKFLOW_EXIT_MAP.md`](WORKFLOW_EXIT_MAP.md) maps that frontier surface by
surface: what you can take, what you must rebuild, and where there is simply no
export to seal.

Proven, not deployed. The lifeboat is on the deck and seaworthy. Whether you
ever sail it — with real data, from a real tenant — is your decision as the
controller, and that is exactly as it should be.
