# AXM chat continuity current state

## Authority

This document is the authoritative integration map for the chat-continuity, browser-sync, evidence-intake, semantic-memory, project-binding, and OSS-commodity work discussed across the related AXM sessions. Repository state and executable acceptance gates outrank prior chat summaries and downloadable design bundles.

This document does not declare any draft pull request accepted, merged, deployed, or live-tested. It states which component currently owns each responsibility, what earlier work it supersedes, which work remains useful but must be migrated, and which discussed capabilities are still design obligations rather than implemented facts.

## Current responsibility map

| Responsibility | Current owner | Current artifact | Status |
|---|---|---|---|
| Frozen shard format, compilation, sealed identity, Merkle construction, signing, detached verification | `axm-genesis` | Genesis v1 mainline | Authoritative and unchanged |
| Source-neutral pre-shard observation envelope, deterministic observation/content identity, adapter declarations, standard translators, validation, conformance, stdio protocol | `axm-core` | PR #28, `agent/intake-compatibility-floor` | Implemented on a draft branch; not merged |
| Conversation source compilation, exact evidence binding, semantic memory, receipt-first query, archive catalog/index | `axm-chat` | PR #9, `agent/evidence-bound-memory` | Implemented on a draft branch; real-corpus acceptance pending |
| Browser extension, native-messaging transport, raw observation store, conversation-head diffing, browser receipts, handoff publication into `axm-chat` | `chatgpt-web` | PR #1, `agent/browser-sync-native-host` | Current browser-acquisition implementation; draft and live-profile smoke pending |
| Earlier authenticated ChatGPT inventory, project matching, Git context, semantic job queue, and project-binding implementation | `axm-chat` | PR #10, `agent/browser-sync-v0`, stacked on PR #9 | Partially superseded; unique modules require salvage and migration |
| Human review and attributed disposition | `axm-console` | Existing review doctrine plus future intake-release work | Not integrated with this train |
| Estate-wide campaign circulation and resumable work routing | `axm-bloodstream` | Existing job ledger plus future intake-routing work | Not integrated with this train |

## Supersession decisions

### Browser acquisition

`chatgpt-web` PR #1 supersedes `axm-chat` PR #10 as the owner of browser extension code, native messaging, browser-side queues, raw browser observations, and conversation-head diffing. Provider-specific acquisition must remain outside `axm-chat` and above the neutral Core intake floor.

PR #10 must therefore not merge unchanged as the final browser architecture. Its unique, still-useful work is limited to:

- authenticated ChatGPT account inventory and change planning;
- exact provider-response retention patterns;
- conservative estate-project matching;
- Git capture/build context;
- semantic-memory job scheduling and recovery;
- claim-level project-binding shard construction.

Those modules must be separated from the duplicate extension/native-host implementation and moved to the component that owns each concern.

### Neutral intake contract

`axm-core` PR #28 supersedes the private browser capture envelope as the intended common observation contract. `chatgpt-web` has not yet migrated to `axm-intake/1.0`; until that migration lands, the Core floor and browser collector are adjacent implementations rather than one integrated system.

The current Core branch implements:

- the closed `axm-intake/1.0` observation envelope;
- `cnt1_` content identity and `obs1_` observation identity;
- authority, continuity, coverage, and security validation;
- adapter manifests and Python entry-point discovery;
- bounded translators for established event, trace, lineage, attestation, agent-protocol, and research-object formats;
- a language-neutral NDJSON stdio protocol;
- public schemas and positive/negative conformance vectors.

It does **not** currently implement the broader substrate previously described in conversation, including a generic durable admission object store, spool worker, adapter-release catalog, operator eligibility policy, signed multi-writer checkpoints, retention/deletion machinery, preservation campaigns, or a hosted ingress service. Those remain planned gaps unless and until code and tests are committed.

### Semantic memory

`axm-chat` PR #9 remains the current semantic-memory candidate. It supersedes the older shard-level-only decision-reference path by requiring quote-backed, exact source-claim references and receipt-first query results. Its code is not accepted as the current mainline until the clean source-pool rebuild, real out-of-band verification, real-corpus golden queries, and other P0 gates in its queue pass.

### Design bundles

The previously generated continuity-protocol V1 and evidence-intake V2 ZIP bundles are design antecedents. They are not executable release artifacts and are not authoritative over this repository map. Concepts from those bundles are retained only where they appear in current code, current schemas, this map, or an explicit open gap.

## Coverage of the original request

The work discussed in the sessions has produced executable or reviewable artifacts for:

- initial ChatGPT and Claude export ingestion;
- immutable conversation source shards;
- exact evidence-bound semantic memory and receipt-first query;
- one-click ChatGPT and Claude rendered-browser capture;
- browser-side retry queues and native messaging;
- raw-first local browser custody and content-head diffing;
- new, unchanged, extended, revised, diverged, and truncated head handling;
- detached verification against an out-of-band key;
- periodic official-export reconciliation as the stated completeness authority;
- OSS and community commodity mapping with pinned upstream candidates;
- a neutral pre-shard observation protocol and public adapter-conformance vectors;
- project/Git association and binding prototypes in the stacked `axm-chat` branch.

## Material gaps that remain

The following items were discussed but are not yet present as one accepted, current, end-to-end production state:

1. None of PR #28, `chatgpt-web` PR #1, `axm-chat` PR #9, or `axm-chat` PR #10 is merged.
2. The browser collector does not yet emit and validate `axm-intake/1.0` observations.
3. Two browser-sync implementations still exist and have not been mechanically de-duplicated.
4. PR #10's project/Git/memory-job work has not been ported onto the current ownership map.
5. PR #9 has not passed its real-corpus P0 acceptance gates.
6. The browser collector has not passed the required real Edge or Chrome operator-profile sequence for first capture, unchanged capture, changed-head capture, retry, and detached verification.
7. ChatGPT and Claude official-export reconciliation is not yet one automated campaign with signed coverage and exception reports.
8. Gemini, Perplexity, Grok, provider artifacts, binary attachments, hidden/archived coverage, and provider drift adapters remain incomplete.
9. Human authority, delegation, requirement/constraint/waiver state, action, artifact, verification proposition, deployment, and outcome are not yet implemented as one generic estate model.
10. Multi-writer/offline merge, trust-store rotation and revocation, privacy deletion and legal hold, access audit, preservation renewal, succession, and clean-machine restore remain future work.
11. The Core floor currently validates and translates observations but does not yet provide a generic durable admission store or routing campaign.
12. No release has yet proven that a third-party collector can pass the public conformance contract, enter the estate, route to a domain spoke, and produce a detached-verified shard without bespoke glue.

## Closure sequence

The train is complete only after the following sequence passes:

1. Accept or repair `axm-chat` PR #9 through its real-corpus P0 gates.
2. Rebase the current browser collector on the accepted conversation-import and verification interfaces.
3. Make `chatgpt-web` emit `axm-intake/1.0`, run the public conformance vectors, and preserve the original provider representation as the observation payload.
4. Port PR #10's unique inventory, project matching, Git context, semantic-job, and project-binding modules; remove its duplicate extension/native-host lane; then close or replace PR #10.
5. Add deterministic observation routing and quarantine in Core, with no adapter allowed to write shards or claim authority.
6. Run the live browser acceptance sequence and official-export reconciliation against a representative account.
7. Publish signed coverage reports that state exactly what was enumerated, captured, missing, excluded, inaccessible, or unknown.
8. Merge in dependency order, release pinned packages, and run a clean-machine installation and restore rehearsal.
9. Update this document and `docs/current-state.json` in the same change whenever ownership, supersession, or acceptance status changes.

## Control question

For every capability discussed in the sessions, can the estate point to one current owner, one executable artifact or explicit gap, one acceptance gate, and one supersession relation, without relying on a chat summary as the source of truth?