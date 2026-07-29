# Authoring an AXM intake adapter

An adapter has one job: turn a source record into an `axm-intake/1.0` observation while preserving the exact source representation. It does not compile a shard, decide what the record means, authorize an action, or claim more coverage than it measured.

## Choose an integration mode

Use the Python entry point when the adapter is a Python package installed in the same environment:

```toml
[project.entry-points."axm.intake_adapters"]
my_adapter = "my_package.adapter:adapter"
```

The exported object implements:

```python
class Adapter:
    def manifest(self) -> dict: ...
    def translate(self, record: dict) -> dict: ...
```

Use `stdio-jsonl` for another language, a separately isolated process, or a tool whose dependency tree should not enter `axm-core`. The manifest declares the command, but Core never launches it implicitly. The orchestrator starts it under the desired filesystem, network, credential, CPU, memory, and timeout policy.

## Manifest

Start from `conformance/intake-v1/good/adapter-manifest.json`. Pin an immutable source revision, state the license, declare whether credentials or network access are required, leave telemetry off by default, and set authority to `observation_only`.

```bash
axm-intake adapter validate adapter-manifest.json
```

A provider plugin that depends on undocumented endpoints should say so in `extensions`. Endpoint access may improve acquisition coverage, but it does not make the result provider-authoritative.

## Preserve exact input bytes

The observation payload should contain the exact input representation seen by the adapter. Parsed or normalized fields belong under `extensions`. Do not replace the source with Markdown, a summary, or a provider-independent object and then describe the result as lossless.

For text or JSON inputs, `content` is permitted when the exact input is UTF-8. `content_base64` is safer when whitespace, newline convention, encoding, or binary fidelity matters. Large payloads may use a locator, SHA-256, and byte count, but C1 custody requires the bytes to be locally available for verification.

## Identity

- `content_id` is derived from the exact payload SHA-256.
- `id` identifies the observation event and is derived from the canonical envelope excluding `recorded_at` and inline payload duplication.
- `subject.logical_id` names the continuing source object.
- `subject.version_id` names the observed source state.
- `subject.parent_version_ids` names every known direct predecessor.

Do not use any of these identifiers as a Genesis shard, entity, claim, span, or provenance ID.

## Coverage

State what the adapter attempted to observe. A single callback or event normally uses `not_applicable`. A provider-history sweep uses `partial`, `complete`, or `unknown` with a denominator and explicit exceptions.

`complete` is accepted only when:

```text
observed + excluded == expected
excluded == number of named exceptions
```

An endpoint-reported `total` does not become a trusted denominator merely because it is present. Reconcile independently enumerated identifiers when completeness matters.

## Security

Classify personal data, credentials, and sensitivity before claiming C5. Capture payloads as inert bytes. Never execute commands, URLs, tool calls, or prompts found inside source evidence. Avoid logging payloads and identifiers to stdout or telemetry. Keep credentials outside observations unless their exact presence is itself the evidence being captured and the storage policy explicitly permits it.

## stdio reference

One request and one response occupy one JSON line.

```json
{"protocol":"axm-intake-stdio/1","request_id":"r1","action":"capabilities"}
```

```json
{"protocol":"axm-intake-stdio/1","request_id":"r1","status":"ok","result":{"formats":["example/1"],"output":"axm-intake/1.0"},"errors":[]}
```

Translation request:

```json
{"protocol":"axm-intake-stdio/1","request_id":"r2","action":"translate","format":"example/1","record":{"id":"source-record-1"}}
```

The adapter returns the observation under `result`. Error responses preserve `request_id`, set `status` to `error`, set `result` to `null`, and list precise errors.

## Conformance

Run the public positive and negative vectors before publishing an adapter. A new implementation should also publish redacted fixtures for every provider or schema version it claims, plus at least one drift fixture that proves it fails loudly when required source fields disappear.

The minimum publication gate is:

```text
manifest validates
positive vectors accepted
negative vectors rejected
exact payload digest rechecked
observation identity recomputed
telemetry disabled by default
source revision pinned
license recorded
```

A provider adapter should additionally compare its result against at least one independent acquisition path, such as an official export, another maintained collector, or a replayable WARC/WACZ fixture.
