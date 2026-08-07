# AXM Spoke API

This document defines the contract between `axm-core` and any package that wants to be an AXM spoke.

---

## Entry point registration

A spoke registers itself via the `axm.spokes` entry-point group in its `pyproject.toml`:

```toml
[project.entry-points."axm.spokes"]
chat = "axm_chat.cli:chat_group"
show = "axm_show.cli:show_group"
```

The value must be a `click.Group`. The key is the spoke name as it appears under `axm <name>`.

When the user runs any `axm` command, `axm-core` calls `importlib.metadata.entry_points(group="axm.spokes")`, loads every registered group, and attaches it to the root CLI. No code in `axm-core` needs to know a spoke exists before install time.

---

## What a spoke may import from Core

| Module | What it provides | Import path |
|---|---|---|
| Spectra engine | Mount shards, run SQL/NL queries | `from axiom_runtime.engine import SpectraEngine` |
| NL→SQL | Natural language to parameterized SQL: `(sql, params)` for `engine.query_json(sql, params)` | `from axiom_runtime.nlquery import natural_language_to_query` |
| NL→SQL (string form) | SQL text only (still contains `?` placeholders; execute with the params from `natural_language_to_query`) | `from axiom_runtime.nlquery import natural_language_to_sql` |
| Forge extractors | Parse documents into topology-preserving blocks / tier-0 candidates | `from axm_forge.ingestion.extractors import extract, extract_chat_json, DocumentBlock, ExtractedDocument` |
| Forge emission | Write claim candidates to a shard via Genesis | `from axm_forge.emission.genesis_emission import emit_genesis_shard, EmissionConfig` |
| Intake model | Validate source-neutral pre-shard observations and adapter manifests | `from axm_core.intake import validate_observation, validate_adapter_manifest` |
| Intake bridges | Translate established standards while preserving exact source bytes | `from axm_core.intake import translate_record, supported_formats` |
| Intake conformance | Compute C0 through C5 compatibility without elevating evidence authority | `from axm_core.intake import conformance_report` |

These are the stable surfaces. Anything not listed here is internal to Core and subject to change without notice.

---

## Universal intake adapters

An intake adapter is narrower than a spoke. It observes or translates source material into `axm-intake/1.0`; it does not define domain claims, compile a shard, or expose a domain CLI. An exporter, telemetry collector, build attestor, browser harvester, or agent runtime can therefore contribute custody without depending on Forge, Spectra, or the Genesis implementation.

Python adapters may register under the optional `axm.intake_adapters` entry-point group:

```toml
[project.entry-points."axm.intake_adapters"]
my_adapter = "my_package.adapter:adapter"
```

The exported object supplies `manifest()` and `translate(record)`. Its manifest must validate against `schemas/intake/adapter-manifest-v1.schema.json`, pin an immutable source revision, record its license, declare telemetry off by default, and set authority to `observation_only`.

Adapters written in any language may implement the newline-delimited `axm-intake-stdio/1` request protocol. Core ships a reference server and schemas, but it never launches a command from a manifest implicitly. The operator or orchestrator retains control of sandboxing, credentials, network access, timeouts, and resource limits.

Every adapter output must preserve exact input bytes or a locally verifiable digest-bound locator. Parsed convenience fields live under `extensions`. Adapter identifiers (`obs1_`, `cnt1_`) are pre-shard identities and must never be represented as Genesis shard, entity, claim, span, or provenance IDs.

See `docs/INTAKE_FLOOR.md`, `docs/ADAPTER_AUTHORING.md`, and `conformance/intake-v1/`.

---

## What a spoke must not reimplement

| Concern | Canonical location | Why |
|---|---|---|
| Shard compilation | `axm-genesis` using `axm_build.compiler_generic` (`CompilerConfig`, `compile_generic_shard`) | The protocol guarantee is that every shard was compiled by the same kernel. Spoke-level compilation bypasses the signature contract. |
| Shard verification | `axm-genesis` using `axm_verify.logic.verify_shard` / CLI `axm-verify shard PATH --trusted-key KEY` | Same reason. Verification must be kernel-level. |
| Merkle tree construction | `axm-genesis` using `axm_build.merkle.compute_merkle_root` | The root commits to every sealed byte. Spoke-level Merkle breaks cross-shard reference integrity. |
| DuckDB schema for shard tables | `axiom_runtime.engine` | Spoke-level schema changes break union views across spokes. |
| Signing keys and crypto suite | `axm-genesis` using `axm_build.sign` (`hybrid1_keygen`, `hybrid1_sign`, `hybrid1_verify`, `hybrid1_public_key`, `SUITE_HYBRID1`, `HYBRID1_SK_LEN`) | One suite (`axm-hybrid1`: Ed25519 ‖ ML-DSA-44, both must verify); one publisher identity per key pool. Keys use `axm-build keygen` (3904-byte secret blob, 1344-byte public key). |
| Entity/claim identity | `axm-genesis` using `axm_verify.identity` (`recompute_entity_id`, `recompute_claim_id`) | Sealed IDs must match what the compiler recomputes. |
| Observation-envelope identity | `axm_core.intake.model` (`compute_observation_id`, `content_id_from_sha256`) | Community adapters need one deterministic pre-shard identity contract, explicitly separate from Genesis identity. |
| Adapter authority and coverage semantics | `axm_core.intake.model` | A transport or parser must not confer authorization or imply completeness. |

---

## What a spoke is responsible for

- **Domain extraction**: turning domain-specific input (chat exports, sensor data, CAD files) into raw claim candidates that Forge can compile.
- **Domain CLI**: the `click.Group` exposed as the entry point, with commands relevant to the spoke's domain.
- **Domain server/UI** (optional): spoke-owned HTTP endpoints or UI components. The Glass Onion pattern (auto-detect local server, fall back to demo mode) is the recommended UI shape.
- **Dependency declaration**: if the spoke needs Spectra for query, it must declare `axm-core` as a dependency in `pyproject.toml`. Import/distill that works without Core is encouraged as a `minimal` optional extra.
- **Intake interpretation**: when consuming `axm-intake/1.0`, the spoke decides which domain candidates are admissible and binds every derived claim to the admitted source bytes. C5 intake conformance does not replace domain evidence review.

---

## Spoke naming convention

| Item | Convention | Example |
|---|---|---|
| Package name | `axm-<domain>` | `axm-chat`, `axm-show` |
| Python package | `axm_<domain>` | `axm_chat`, `axm_show` |
| Entry point key | `<domain>` | `chat`, `show` |
| CLI command group | `<domain>_group` | `chat_group`, `show_group` |
| Repo name | `axm-<domain>` | `axm-chat`, `axm-show` |

---

## Minimum viable spoke

The smallest thing that registers as a spoke:

```python
# src/axm_myspoke/cli.py
import click

@click.group("myspoke")
def myspoke_group():
    """My domain spoke."""
    pass

@myspoke_group.command()
def hello():
    """Prove the spoke loads."""
    click.echo("myspoke: hello from axm")

def main():
    myspoke_group()
```

```toml
# pyproject.toml
[project.entry-points."axm.spokes"]
myspoke = "axm_myspoke.cli:myspoke_group"
```

After `pip install -e .`, running `axm spokes` lists it and `axm myspoke hello` works.

---

## Versioning

Spokes declare a minimum `axm-genesis` version. They declare `axm-core` if they use Spectra, Forge, or the intake floor. They do not pin exact versions of either; that is the user's environment's job.

The Genesis protocol version is the long-term stability guarantee: the v1 kernel (RFC 0002) is frozen, so a shard compiled under any 1.x verifies under every other 1.x. Everything shipped before v1 is the v0.x prototype lineage, archived in git history and not accepted by v1 verifiers.

The intake protocol is independently versioned. `axm-intake/1.x` preserves its required field meanings and identity algorithm. A breaking observation or adapter contract requires `axm-intake/2.0`; it does not change Genesis v1.

Every genesis-facing name in this document is re-proven importable against the pinned kernel by `tests/test_v1_mount.py::test_spoke_api_import_surface`. Intake-facing names and conformance vectors are exercised by `tests/test_intake_protocol.py`.
