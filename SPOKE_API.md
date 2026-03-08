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
| NL→SQL | Natural language to query pattern | `from axiom_runtime.nlquery import natural_language_to_sql` |
| Forge extractors | Parse documents into raw claim candidates | `from axm_forge.ingestion.extractors import ChatExtractor, UniversalExtractor` |
| Forge emission | Write claim candidates to shard via Genesis | `from axm_forge.emission.genesis_emission import emit_shard` |

These are the stable surfaces.  Anything not listed here is internal to Core and subject to change without notice.

---

## What a spoke must not reimplement

| Concern | Canonical location | Why |
|---|---|---|
| Shard compilation | `axm-genesis` — `axm_build` | The protocol guarantee is that every shard was compiled by the same kernel.  Spoke-level compilation bypasses the signature contract. |
| Shard verification | `axm-genesis` — `axm_verify` | Same reason.  Verification must be kernel-level. |
| Merkle tree construction | `axm-genesis` — `axm_merkle` | The root hash is the shard's identity.  Spoke-level Merkle breaks cross-shard reference integrity. |
| DuckDB schema for shard tables | `axiom_runtime.engine` | Spoke-level schema changes break union views across spokes. |
| Signing keys and crypto suite selection | `axm-genesis` — `axm_keys` | One key per publisher, one suite per ecosystem version. |

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

The Genesis protocol version is the long-term stability guarantee.  Spokes that compile shards at `axm-genesis>=1.2.0` will be verifiable by any future `axm-genesis>=1.2.0` installation.
