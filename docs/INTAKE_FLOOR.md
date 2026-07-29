# AXM universal intake compatibility floor

## Purpose

The intake floor lets an external collector, agent runtime, observability stack, data-lineage system, build attestor, browser archive, or research package contribute evidence to AXM without becoming an AXM spoke and without learning the Genesis shard format. It provides a source-neutral observation envelope, deterministic pre-shard identities, a language-neutral stdio protocol, standard translators, and a conformance ladder. Domain spokes remain responsible for deciding how admitted observations become evidence-bound candidates. Genesis remains the only authority that compiles, signs, and verifies shards.

The floor is deliberately narrower than an event bus, workflow engine, or universal ontology. It standardizes the custody handoff that those systems previously had to invent independently.

```text
provider or community collector
        │
        ├── CloudEvents
        ├── OpenTelemetry / OpenInference
        ├── OpenLineage
        ├── in-toto / SLSA
        ├── MCP / A2A / AG-UI
        ├── RO-Crate
        └── native AXM adapter
        │
        ▼
axm-intake/1.0 observation
        │
        ├── exact source bytes or hash-bound external locator
        ├── observation identity
        ├── content identity
        ├── logical object and version identity
        ├── explicit continuity relations
        ├── explicit coverage claim
        ├── explicit security classification
        └── authority = observation_only
        │
        ▼
domain spoke preflight and interpretation
        │
        ▼
Genesis compile_generic_shard
        │
        ▼
out-of-band verification and estate admission
```

## Boundary

An intake observation is not a shard, claim, decision, approval, verified fact, or completeness certificate. The `obs1_` and `cnt1_` identifiers use SHA-256 namespaces that are intentionally distinct from Genesis `sh1_`, `e1_`, `c1_`, `s1_`, and `p1_` identities. An adapter cannot assign a shard identity, write into the shard pool, elevate a semantic tier, or confer human authority.

The floor preserves four distinctions that ordinary exporter pipelines collapse:

1. **Observation identity** records one acquisition event. Re-delivery of the same event is idempotent because `recorded_at` is excluded from the observation fingerprint.
2. **Content identity** is the SHA-256 identity of the exact payload representation.
3. **Logical identity** names the continuing source object, such as a conversation, trace, job, task, artifact, or crate.
4. **Version identity** names the observed state of that logical object and carries zero or more parent versions.

## Observation contract

The JSON Schema is `schemas/intake/observation-v1.schema.json`. The executable validator in `axm_core.intake.model` additionally checks conditions that JSON Schema cannot express conveniently, including exact payload byte count, payload SHA-256, deterministic `content_id`, deterministic `observation_id`, coverage arithmetic, duplicate relations, timezone-bearing timestamps, and the authority ceiling.

A complete observation contains:

```json
{
  "specversion": "axm-intake/1.0",
  "type": "observation",
  "id": "obs1_<sha256>",
  "content_id": "cnt1_<payload-sha256>",
  "source": {
    "adapter_id": "org.example.adapter",
    "adapter_version": "1.0.0",
    "producer": "example-system",
    "source_uri": "urn:example:item",
    "source_revision": "pinned-source-revision",
    "source_license": "Apache-2.0"
  },
  "subject": {
    "kind": "event",
    "logical_id": "item-123",
    "version_id": "version-7",
    "parent_version_ids": ["version-6"]
  },
  "observed_at": "2026-07-28T12:00:00Z",
  "recorded_at": "2026-07-28T12:00:02Z",
  "payload": {
    "media_type": "application/json",
    "sha256": "<64 lowercase hex>",
    "bytes": 1234,
    "encoding": "base64",
    "content_base64": "..."
  },
  "authority": "observation_only",
  "relations": [],
  "coverage": {
    "scope": "one event",
    "status": "not_applicable",
    "method": "one input record mapped to one observation",
    "denominator": {
      "kind": "input_record",
      "expected": 1,
      "observed": 1,
      "excluded": 0
    },
    "exceptions": []
  },
  "security": {
    "sensitivity": "unknown",
    "personal_data": "unknown",
    "credentials": "unknown",
    "redactions": []
  },
  "extensions": {
    "bridge": {
      "format": "cloudevents",
      "specversion": "1.0"
    }
  }
}
```

The payload carries exactly one representation:

- `content`, where the exact representation is UTF-8 text;
- `content_base64`, where the exact representation is arbitrary bytes;
- `locator`, where the bytes remain external but are bound by digest and length.

External locators remain at envelope conformance until a verifier reads and hashes the referenced bytes. Remote locators are never fetched implicitly by the validator.

## Conformance ladder

Conformance is cumulative. A system may state the highest level it actually achieves, but it may not skip a lower level.

| Level | Name | Required proposition |
|---|---|---|
| C0 | Envelope | The record is structurally valid, its identities recompute, and the adapter declares `observation_only`. |
| C1 | Custody | The exact payload bytes are locally available and match both the declared byte count and SHA-256. |
| C2 | Continuity | The logical object, observed version, and complete parent-version set are explicit. |
| C3 | Coverage | Scope, method, status, denominator, and exclusions are explicit. `complete` requires `observed + excluded == expected` and one named exception per exclusion. |
| C4 | Provenance | The adapter source revision and license are recorded and the source format is named in the bridge extension. |
| C5 | Estate-ready | Security sensitivity, personal-data presence, and credential presence are classified rather than left unknown. |

C5 does not mean that the observation is true, authorized, complete beyond its stated scope, safe to publish, or admitted to the estate. It means the observation is sufficiently explicit for a domain spoke and human policy to evaluate it without filling silent gaps.

## Adapter contract

The adapter manifest schema is `schemas/intake/adapter-manifest-v1.schema.json`. Every adapter must identify its implementation version, immutable source revision, SPDX or explicit license, supported input forms, output protocol, capabilities, transport, security requirements, size limit, and telemetry default. The following rules are binding:

- output must include `axm-intake/1.0`;
- authority must be `observation_only`;
- telemetry defaults to `off`;
- an adapter that uses `stdio-jsonl` declares its command explicitly;
- source revision is mandatory;
- an adapter may require credentials, but it must declare that fact;
- an adapter may observe provider-internal APIs, but the resulting record remains an observation rather than provider truth.

Python adapters may register an object under the `axm.intake_adapters` entry-point group. An object exposes `manifest()` and `translate(record)`. The runtime isolates discovery failures so one broken adapter cannot hide the built-in bridge inventory.

Non-Python adapters use `axm-intake-stdio/1`, defined by `schemas/intake/stdio-v1.schema.json`. Each line is one JSON request or response. The reference actions are `health`, `capabilities`, and `translate`. Core does not automatically execute commands found in manifests. Process launch, credential injection, resource limits, and sandboxing remain explicit operator or orchestrator actions.

## Built-in bridges

The initial bridge set preserves the exact source JSON bytes and records parsed mappings under `extensions.bridge`:

| Input | AXM subject | Preserved mechanism |
|---|---|---|
| CloudEvents | `event` | Event ID, source, type, subject, time, causal extension fields |
| OpenLineage | `job` | Job namespace/name, run ID, input datasets, output datasets, parent run |
| in-toto / SLSA statement | `attestation` | Statement type, predicate type, artifact subjects, builder, materials |
| OpenTelemetry / OpenInference span | `agent_trace` | Trace/span hierarchy, service/provider/model attributes, OpenInference span kind |
| MCP JSON-RPC | `protocol_message` | Session correlation, method, request or response identity |
| A2A task | `agent_task` | Context, task version, state, parent task |
| AG-UI event | `agent_task` | Thread, run, event type, message/tool-call identity |
| RO-Crate metadata | `research_object` | Crate identity, root dataset, entity count, publication or modification time |

The bridge is intentionally lossless at the payload boundary and selective at the projection boundary. Parsed fields make routing and inspection cheaper, while exact input bytes remain available when a future translator or dispute needs to reconstruct the original record.

## Command line

```bash
# Translate an established format into an AXM observation.
axm-intake translate cloudevents event.json --output observation.json
axm-intake translate otel-openinference span.json --output observation.json

# Verify the envelope and inline bytes.
axm-intake validate observation.json

# Report the cumulative conformance level.
axm-intake conform observation.json

# List built-in bridges and installed Python adapters.
axm-intake adapters

# Validate a third-party adapter declaration.
axm-intake adapter validate adapter-manifest.json

# Serve the reference stdio bridge loop.
axm-intake stdio
```

`conformance/intake-v1/` contains positive and negative vectors. A compatible implementation must accept the positive vectors and reject the negative vectors for the stated reason.

## Security

Input payloads are inert evidence. A translator must not execute embedded commands, follow embedded URLs, call tools described by a trace, or treat archived model text as instructions. The built-in bridges perform no network access. Remote locator verification, adapter process execution, provider authentication, and secret handling require separate explicit actions.

Credentials, cookies, authorization headers, prompt content, tool arguments, model outputs, and browser archives may contain sensitive personal or institutional data. Adapters should minimize collection where it does not damage custody, redact only through explicit derivative records, and retain an immutable digest relationship to any protected original kept in a separate encrypted store.

## What remains outside this floor

The following mechanisms are adjacent and should plug into the floor rather than being reimplemented here:

- OpenTelemetry collectors and OpenInference instrumentation for execution traces;
- OpenLineage emitters for data and workflow lineage;
- in-toto, SLSA, Sigstore, and Rekor for software supply-chain evidence and external transparency;
- WARC, WACZ, SingleFile, and Webrecorder tooling for browser-level preservation;
- RO-Crate, BagIt, and OCFL for portable and long-term object packaging;
- MCP, A2A, and AG-UI for interaction and execution transport;
- Automerge or Yjs for offline multi-writer synchronization of mutable working projections;
- SPDX or CycloneDX for component and dependency declarations.

AXM owns the custody admission, identity separation, evidence authority ceiling, exact source binding, Genesis compilation boundary, and estate relationships. It consumes commodity capture, tracing, packaging, synchronization, and attestation systems through adapters.

## Control question

For any new integration, can the producer deliver exact bytes, stable source and version identity, explicit continuity, explicit coverage, explicit security classification, and a pinned implementation receipt without being allowed to declare truth, authority, or Genesis verification? If so, it belongs above this floor rather than inside the AXM kernel.
