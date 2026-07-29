# Compatibility-floor gap and commodity sweep

Generated: 2026-07-28

## Finding

The wider ecosystem already supplies most of the mechanisms needed to observe AI work. Browser extensions capture conversations. OpenTelemetry and OpenInference instrument model, retrieval, and tool spans. OpenLineage models jobs, runs, and datasets. MCP, A2A, and AG-UI transport tool, agent, task, and user-interface events. in-toto and SLSA describe how software artifacts were produced. Sigstore and transparency logs witness signatures. WARC and WACZ preserve browser traffic. RO-Crate packages research objects. Automerge and Yjs synchronize mutable local-first projections.

The missing floor was a common custody handoff that could accept all of those records without treating any of them as authoritative merely because they arrived through a standard. The intake protocol in this change set supplies that floor.

## Gaps found beyond browser chat

### Execution-context gap

A rendered conversation omits tool calls, retrieved documents, model settings, prompt templates, memory reads, agent handoffs, latency, token accounting, and runtime errors. OpenTelemetry GenAI semantic conventions and OpenInference are the commodity capture layer. AXM should ingest those spans, preserve sensitive attributes under explicit policy, and bind them to the corresponding conversation or agent-session head.

### Workflow and data-lineage gap

A chat may authorize work that later flows through schedulers, notebooks, databases, build systems, or ETL jobs. OpenLineage already models jobs, runs, inputs, outputs, and parent runs. AXM should consume those events and attach estate decisions and artifacts without inventing another workflow-lineage protocol.

### Artifact-production gap

A Git commit is insufficient when the deliverable is a container, package, binary, model, document bundle, or deployment. in-toto and SLSA provenance already describe builders, materials, commands, and products. AXM should ingest their signed statements, then add human authority and outcome relationships at the estate layer.

### Interaction-protocol gap

MCP exposes tools, resources, and prompts. A2A models tasks and agent collaboration. AG-UI streams agent state, messages, tool calls, and human-in-the-loop events to an interface. These protocols should remain transports. AXM should preserve their events and receipts, but no protocol request or model-generated task state should become human authorization without a separate authority event.

### Multi-writer synchronization gap

Browser extensions, desktop agents, phones, build workers, and offline machines can observe the same estate concurrently. Mutable indexes and queues need a commodity local-first merge layer such as Automerge or Yjs. The authoritative record remains immutable observations and Genesis shards. A CRDT may reconcile working projections; it may not rewrite admitted evidence.

### Portable-preservation gap

A shard is independently verifiable, but a useful transfer also needs payload packaging, manifests, software readers, rights metadata, and contextual relationships. RO-Crate, BagIt, OCFL, WARC, and WACZ provide established package shapes. AXM should publish profiles over those formats instead of defining an opaque estate archive.

### Trust-lifecycle gap

Genesis currently depends on out-of-band publisher keys. A durable estate also needs key rotation, revocation, validity intervals, timestamp renewal, external witnesses, and succession. Sigstore, Rekor, SCITT, RFC 3161, OpenTimestamps, and evidence-record practices are commodities or standards for portions of that problem. They should witness AXM identities rather than replace Genesis verification.

### Privacy and retention gap

Raw traces and browser archives can contain credentials, personal data, proprietary prompts, and private attachments. The intake floor now makes classification explicit, but the estate still needs encrypted payload compartments, derivative redactions, retention schedules, legal holds, deletion traversal, and crypto-shredding receipts.

### Coverage gap

Exporter success is frequently mistaken for archive completeness. The floor requires explicit scope, method, denominator, observed count, excluded count, and exception set. Account-wide reconciliation still needs provider-specific inventory adapters and official exports as independent sets.

### Schema-evolution gap

Standards and provider APIs evolve independently. Each adapter must pin a source revision, name its input format, preserve exact input bytes, emit a versioned observation, and pass differential fixtures. New adapter behavior is a new version rather than a silent parser replacement.

### Conformance and market-adoption gap

An ecosystem floor needs more than source code. It needs public schemas, positive and negative vectors, a language-neutral adapter protocol, compatibility reports, release pins, and a registry that distinguishes implemented, planned, and external-sidecar integrations. This change set establishes those artifacts so community projects can target a stable boundary.

## Commodity map

| Concern | Commodity | AXM-owned seam |
|---|---|---|
| Event envelope | CloudEvents | Exact payload binding, logical/version identity, coverage and authority ceiling |
| LLM and agent traces | OpenTelemetry GenAI and OpenInference | Admission, source reconciliation, decision and artifact relationships |
| Trace UI and evaluations | Phoenix and compatible OTel collectors | Verified estate receipts rather than observability database authority |
| Data lineage | OpenLineage | Human authority, immutable custody, cross-domain evidence references |
| Software provenance | in-toto and SLSA | Estate decision scope, detached Genesis verification, outcome records |
| Signature transparency | Sigstore and Rekor | Optional external witnessing of AXM identities and key lifecycle |
| Tool transport | MCP | Observation of calls/results; no implicit authority to act |
| Agent collaboration | A2A | Task and handoff custody; no implicit human approval |
| Agent UI events | AG-UI | Human interaction evidence; separate authority disposition |
| Local-first merge | Automerge or Yjs | Rebuildable queue/index synchronization only |
| Browser preservation | WARC, WACZ, SingleFile, Webrecorder | AXM observation identity and selective semantic compilation |
| Research packaging | RO-Crate | Genesis-sealed claims and receipts inside or alongside the crate |
| Long-term object storage | OCFL and BagIt | AXM verification, trust history, and restore evidence |
| Dependency metadata | SPDX and CycloneDX | Bind component declarations to exact artifacts and build attestations |

## Public floor versus private implementation

The public floor consists of:

- `axm-intake/1.0` observation schema;
- `axm-intake-adapter/1.0` manifest schema;
- `axm-intake-receipt/1.0` receipt schema;
- `axm-intake-stdio/1` language-neutral request protocol;
- deterministic identity and byte-verification rules;
- C0 through C5 conformance semantics;
- standard bridge registry;
- positive and negative vectors;
- stable Python API and CLI.

Provider credentials, browser cookies, raw private payloads, encryption keys, retention policies, and estate-specific project mappings remain local and outside the public compatibility surface.

## Ordered implementation train

1. Publish and stabilize the observation, adapter, receipt, and stdio contracts.
2. Make `chatgpt-web` emit the floor envelope before its current native-host admission path.
3. Port community-maintained provider interceptors behind adapter manifests rather than adding selectors to the core extension.
4. Add an OpenTelemetry/OpenInference receiver and correlate traces with browser conversation heads and agent sessions.
5. Add OpenLineage and in-toto/SLSA ingestion for jobs, builds, and artifacts.
6. Expose verified estate reads through a separate MCP server; keep write admission behind the intake contract and human policy.
7. Add WARC/WACZ and RO-Crate package profiles for portable evidence bundles.
8. Add local-first projection synchronization while preserving immutable observation streams.
9. Add trust-store, key-rotation, timestamp-renewal, retention, deletion, and succession protocols.
10. Publish a conformance badge only after independent implementations pass the shared vectors.

## Failure modes this floor prevents

- an exporter calling its own successful crawl complete;
- an AI-generated task or tool call being treated as authorization;
- a parser assigning a Genesis shard identity;
- a translated record losing the exact source bytes;
- a mutable observability database becoming the evidence authority;
- a source upgrade silently changing prior interpretation;
- a signed build attestation being mistaken for proof that the build was approved or effective;
- a CRDT merge rewriting immutable evidence;
- a third-party adapter enabling telemetry without disclosure;
- a copyleft or proprietary parser entering the core without an explicit license boundary.

## Control question

Does a proposed AXM feature preserve a custody invariant that no upstream standard provides, or is it infrastructure that should be consumed through the intake floor and maintained by its existing community?
