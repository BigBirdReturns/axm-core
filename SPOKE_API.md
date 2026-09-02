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

The value must be a `click.Group`.  The key is the spoke name as it appears under `axm <name>`.

When the user runs any `axm` command, `axm-core` calls `importlib.metadata.entry_points(group="axm.spokes")`, loads every registered group, and attaches it to the root CLI.  No code in `axm-core` needs to know a spoke exists before install time.

---

## What a spoke may import from Core

| Module | What it provides | Import path |
|---|---|---|
| Spectra engine | Mount shards, run SQL/NL queries | `from axiom_runtime.engine import SpectraEngine` |
| NL→SQL | Natural language to parameterized SQL: `(sql, params)` for `engine.query_json(sql, params)` | `from axiom_runtime.nlquery import natural_language_to_query` |
| NL→SQL (string form) | SQL text only (still contains `?` placeholders — execute with the params from `natural_language_to_query`) | `from axiom_runtime.nlquery import natural_language_to_sql` |
| Forge extractors | Parse documents into topology-preserving blocks / tier-0 candidates | `from axm_forge.ingestion.extractors import extract, extract_chat_json, DocumentBlock, ExtractedDocument` |
| Forge emission | Write claim candidates to a shard via Genesis | `from axm_forge.emission.genesis_emission import emit_genesis_shard, EmissionConfig` |
| Forge model runner | Provider-neutral deterministic generation and route description | `from axm_forge.model_runner import generate_text, describe_route, ModelRunnerError` |
| Forge model cache scopes | Exact opaque-scope inspection and invalidation | `from axm_forge.model_runner import inspect_cache_scope, invalidate_cache_scope, CacheScopeError` |

These are the stable surfaces. Anything not listed here is internal to Core and subject to change without notice.

---

## Provider-neutral model runner and scoped cache

The model runner is a standard-library Core boundary. A spoke owns its prompts,
schemas, purposes, and the meaning of an optional cache scope. Core owns
transport selection, request identity, cache placement, fencing epochs,
deterministic controls, and body-free receipts.

### Generation

The stable spoke call is:

```python
from axm_forge.model_runner import generate_text

result = generate_text(
    system,
    user,
    model="exact-model-or-auto-for-native-ollama",
    profile="luna.semantic@1",
    purpose="spoke/task@1",
    response_schema="spoke/result@1",
    base_url=None,
    timeout=180,
    max_output_tokens=2048,
    num_ctx=None,
    temperature=0.0,
    seed=0,
    cache_namespace="",
    cache_scope="",
)
```

`purpose` and `response_schema` are required keyword arguments.
`cache_namespace` and `cache_scope` obey an all-or-neither rule. Empty values
preserve the unscoped cache contract. When present, they are local placement
metadata and never enter an HTTP payload or command-adapter envelope.

The result is an object with at least:

```json
{
  "text": "provider output",
  "model": "actual model identity",
  "transport": "ollama-native | openai-compatible | command",
  "cache_key": "physical cache placement identity",
  "receipt": {}
}
```

The route can be resolved without generation:

```python
from axm_forge.model_runner import describe_route

route = describe_route(
    model="auto",
    profile="luna.semantic@1",
    base_url=None,
    timeout=30,
    num_ctx=None,
)
```

The route mapping contains at least `schema`, `profile`, `transport`,
`endpoint_sha256`, `route_identity`, `model`, and `num_ctx`.

### Invocation receipt

The invocation receipt schema is
`axm-core/model-invocation-receipt@1`. Its stable minimum fields are:

```text
schema
request_digest
cache_key
cache_namespace
cache_scope
cache_epoch
cache_store_sha256
response_sha256
profile
purpose
response_schema
transport
endpoint_sha256
route_identity
model_requested
model_actual
model_identity_match
temperature
seed
max_output_tokens
num_ctx
cache_hit
cacheable
cache_write_outcome
```

`cache_write_reason`, `started_at`, `duration_ms`, and numeric `usage` may be
present. Receipts contain no system prompt, user prompt, response body, API key,
credentialed endpoint, or command body. Consumers must ignore additive unknown
fields.

`request_digest` is semantic request identity. `cache_key` is physical
placement identity. For unscoped calls they are equal. For scoped calls,
`cache_key` additionally binds namespace, scope, and the current fencing epoch.

### Exact cache-scope operations

A spoke may inspect and invalidate one exact opaque scope:

```python
from axm_forge.model_runner import (
    inspect_cache_scope,
    invalidate_cache_scope,
)

inspection = inspect_cache_scope(namespace, scope)

invalidation = invalidate_cache_scope(
    namespace,
    scope,
    reason="required operator reason",
    dry_run=False,
)
```

Empty values, all-or-neither violations, wildcard behavior, and an empty
invalidation reason are refused. Ordinary spokes omit the implementation-level
`root` override.

The stable scope schemas are:

```text
axm-core/model-cache-scope-state@1
axm-core/model-cache-scope-inspection@1
axm-core/model-cache-scope-invalidation@2
axm-core/model-cache-scope-cleanup@1
```

Inspection contains at least:

```text
schema
cache_store_sha256
cache_namespace
cache_scope
current_epoch
entry_count
stored_bytes
verified_count
refused_count
last_invalidation_receipt_sha256
state_sha256
state_persisted
```

Logical invalidation advances the scope epoch and seals the authoritative
invalidation receipt. Physical deletion is separately sealed in
`cleanup_receipt`. A cleanup failure does not reactivate the retired epoch.
The returned invalidation mapping contains the logical receipt fields plus
`cleanup_receipt`, `cleanup_receipt_sha256`, `cleanup_receipt_persisted`,
`physically_deleted`, `deleted_bytes`, and `inaccessible_residue`.

Core may add fields to these mappings but must preserve the named fields and
their meanings throughout the current schema major versions.

---

## What a spoke must not reimplement

| Concern | Canonical location | Why |
|---|---|---|
| Shard compilation | `axm-genesis` — `axm_build.compiler_generic` (`CompilerConfig`, `compile_generic_shard`) | The protocol guarantee is that every shard was compiled by the same kernel.  Spoke-level compilation bypasses the signature contract. |
| Shard verification | `axm-genesis` — `axm_verify.logic.verify_shard` / CLI `axm-verify shard PATH --trusted-key KEY` | Same reason.  Verification must be kernel-level. |
| Merkle tree construction | `axm-genesis` — `axm_build.merkle.compute_merkle_root` | The root commits to every sealed byte.  Spoke-level Merkle breaks cross-shard reference integrity. |
| DuckDB schema for shard tables | `axiom_runtime.engine` | Spoke-level schema changes break union views across spokes. |
| Signing keys and crypto suite | `axm-genesis` — `axm_build.sign` (`hybrid1_keygen`, `hybrid1_sign`, `hybrid1_verify`, `hybrid1_public_key`, `SUITE_HYBRID1`, `HYBRID1_SK_LEN`) | One suite (`axm-hybrid1`: Ed25519 ‖ ML-DSA-44, both must verify); one publisher identity per key pool.  Keys via `axm-build keygen` (3904-byte secret blob, 1344-byte public key). |
| Entity/claim identity | `axm-genesis` — `axm_verify.identity` (`recompute_entity_id`, `recompute_claim_id`) | Sealed IDs must match what the compiler recomputes. |

---

## What a spoke is responsible for

- **Domain extraction**: turning domain-specific input (chat exports, sensor data, CAD files) into raw claim candidates that Forge can compile.
- **Domain CLI**: the `click.Group` exposed as the entry point, with commands relevant to the spoke's domain.
- **Domain server/UI** (optional): spoke-owned HTTP endpoints or UI components.  The Glass Onion pattern (auto-detect local server, fall back to demo mode) is the recommended UI shape.
- **Dependency declaration**: if the spoke needs Spectra for query, it must declare `axm-core` as a dependency in `pyproject.toml`.  Import/distill that works without Core is encouraged as a `minimal` optional extra.

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

Spokes declare a minimum `axm-genesis` version.  They declare `axm-core` if they use Spectra or Forge.  They do not pin exact versions of either — that is the user's environment's job.

The Genesis protocol version is the long-term stability guarantee: the v1
kernel (RFC 0002) is frozen, so a shard compiled under any 1.x verifies
under every other 1.x. Everything shipped before v1 is the v0.x prototype
lineage — archived in git history and **not** accepted by v1 verifiers.

Every genesis-facing name in this document is re-proven importable against
the pinned kernel by
`tests/test_v1_mount.py::test_spoke_api_import_surface`.
