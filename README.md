# AXM Core

> **New here?** Read **[START_HERE.md](START_HERE.md)** — the whole ecosystem in
> one page, and which repo to clone for your goal. Exiting Foundry? Jump to
> **[foundry_exit/FIRST_HOUR.md](foundry_exit/FIRST_HOUR.md)**.

The orchestration hub of the AXM ecosystem. Sits between the cryptographic kernel (axm-genesis) and domain spokes (axm-embodied and others).

```
axm-genesis  ←  axm-core  ←  spokes
  kernel          hub
```

## The AXM ecosystem

| Repository | Role | Explainer |
|---|---|---|
| [axm-genesis](https://github.com/BigBirdReturns/axm-genesis) | The frozen cryptographic kernel — compiles and verifies signed knowledge shards | [site](https://bigbirdreturns.github.io/axm-genesis/) |
| [axm-core](https://github.com/BigBirdReturns/axm-core) | The runtime — Spectra query engine, Forge extraction, spoke host | [site](https://bigbirdreturns.github.io/axm-core/) |
| [axm-chat](https://github.com/BigBirdReturns/axm-chat) | The first spoke — turns conversation exports into verified memory | [site](https://bigbirdreturns.github.io/axm-chat/) |
| [axm-embodied](https://github.com/BigBirdReturns/axm-embodied) | The physical liability spoke — turns frame capture into verified `physical_capture` memory | — |

Genesis compiles and signs; everything else reads. That boundary is the
invariant that makes long-term verification possible.

## Components

| Component | Location | Purpose |
|-----------|----------|---------|
| **axm-genesis** | declared dependency | Shard spec, compiler, verifier, post-quantum crypto |
| **Forge** | `forge/` | Document extraction pipeline (tier 0/1 regex + tier 3 LLM) |
| **Spectra** | `spectra/` | Runtime query engine (DuckDB + SQL gate) |
| **Clarion** | `clarion/` | Topology-bound encryption (GraphKDF) |
| **Foundry Exit** | `foundry_exit/` | Palantir Foundry export — sealed, detached-verifiable exit bundles. **Ontology Exit** (`axm-exit`) and **Pipeline Exit** (`axm-pipeline-exit`: dataset schemas + dependency DAG). See [FIRST_HOUR.md](foundry_exit/FIRST_HOUR.md), [ONTOLOGY_EXIT.md](foundry_exit/ONTOLOGY_EXIT.md), [PIPELINE_EXIT.md](foundry_exit/PIPELINE_EXIT.md), [WORKFLOW_EXIT_MAP.md](foundry_exit/WORKFLOW_EXIT_MAP.md) |
| **Nodal Flow** | [separate repo](https://github.com/BigBirdReturns/nodalflow) | Desktop UI (Tauri + Svelte + DuckDB) |

Sub-packages (`forge/`, `spectra/`, `clarion/`) are versioned **independently** of the
`axm-core` root and of each other — each carries its own `version` in its own
`pyproject.toml`. The root `axm-core` version does not track them, and the only
version they all agree on is the pinned `axm-genesis` dependency.

## Dependency graph

```
axm-genesis (cryptographic kernel — immutable)
  axm_build.*    compiler, Merkle, signing
  axm_verify.*   verifier, error codes, schemas
       ↑
axm-core (this repo — orchestration hub)
  pyproject.toml declares a pinned axm-genesis commit
  forge/         document ingestion
  spectra/       runtime query
  clarion/       encryption transport
       ↑
axm-embodied (physical liability spoke)
axm-<other>     (future spokes)
```

`axm-core` does not vendor `axm-genesis`. The genesis kernel is a declared dependency pinned to an exact commit.

> Genesis v1 (RFC 0002) has landed and this repo pins it; all shards built
> before v1 are v0.x prototypes and cannot be verified by v1 (their history
> lives in git).

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate

# Install axm-core root (pulls the pinned axm-genesis automatically)
pip install -e .

# Developing against a local genesis checkout? The pin above fetches the
# pinned commit from GitHub and will replace an editable install — put
# your sibling checkout back afterwards:
#   pip install --no-deps -e ../axm-genesis

# Install forge separately
pip install -e ./forge

# Verify the gold shard. The installed axm-genesis wheel does not ship shard
# data; the gold shard lives in the axm-genesis repo checkout, assumed here to
# be a sibling of this repo.
axm-verify shard ../axm-genesis/shards/gold/fm21-11-hemorrhage-v2/ \
  --trusted-key ../axm-genesis/keys/gold-v2-provisional.pub

# Health check (verifies layout, deps, and gold shard) and tests
python scripts/doctor.py       # → "status": "PASS"
python -m pytest tests/ -q     # → 37 passed
```

## Creating a Shard

### From structured documents (no LLM needed)

```bash
python forge_run.py --input ./my_docs/ --output ./out/my_shard/ --skip-llm
```

### With LLM extraction (requires Ollama)

```bash
ollama serve &
ollama pull llama3:8b
python forge_run.py --input ./my_docs/ --output ./out/my_shard/
# Checkpoints automatically. Rerun same command to resume.
```

### Single article

`--input` accepts a single `.md`/`.txt` file as well as a directory:

```bash
python forge_run.py --input ./my_article.md --output ./out/my_doc/ --skip-llm
```

Note: with `--skip-llm` only the deterministic tier 0/1 extractors run, so the
input needs some structure (markdown tables/headings, statutory numbering,
cross-references). Fully unstructured prose yields no tier 0/1 candidates and
the compile step fails; use the LLM path (Ollama) for such documents.

## Installing sub-components separately

Forge, Spectra, and Clarion each have their own `pyproject.toml`. Install them as needed:

```bash
pip install -e ./forge     # axm-forge CLI
pip install -e ./clarion   # topology-bound encryption (see note)
```

> **Note:** Clarion's encrypt/decrypt functions additionally require the
> `graphkdf` package, which is **not published on PyPI** (`pip install -e
> "./clarion[kdf]"` will fail without a local graphkdf source). Without it the
> clarion package installs and imports, but `clarion.core.encrypt_shard`
> raises `ImportError`. Tools in this repo (e.g. `integration_test.py`) skip
> the Clarion leg cleanly when graphkdf is absent.

## Nodal Flow (Desktop UI)

Nodal Flow is a native desktop interface (Tauri + Svelte + Rust) that embeds the vault and query engine locally. It lives in a separate repository.

Mount a shard, query in natural language, click any citation to verify source bytes.

## What's frozen (from axm-genesis)

The shard layout, canonical-JSONL table encoding, Merkle computation, the
`axm-hybrid1` signature suite, identifier generation, and the gold shard
(`fm21-11-hemorrhage-v2`) are frozen in the Genesis v1 spec
(`spec/v1/SPECIFICATION.md`). The conformance vectors and the gold shard are
the definition of correctness. Shards carry no Parquet: Spectra loads the
JSONL tables into DuckDB at mount time (a rebuildable cache that lives
outside the shard directory).

See `INVARIANTS.md` for absolute constraints on all changes.

## Documentation

| Document | What it is |
|---|---|
| [INVARIANTS.md](INVARIANTS.md) | Absolute constraints on all changes, with their real enforcement status |
| [SPOKE_API.md](SPOKE_API.md) | The import surface spokes may rely on — every name verified importable |
| [EXTENSIONS_REGISTRY.md](EXTENSIONS_REGISTRY.md) | Extension table schemas |
| [IDENTITY.md](IDENTITY.md) | Identity and canonicalization notes |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How the hub is put together |
| [forge/README.md](forge/README.md) | Document extraction pipeline |
| [spectra/README.md](spectra/README.md) | Runtime query engine |
| [foundry_exit/README.md](foundry_exit/README.md) | Foundry exit + Ontology Exit — intake, seal, verify |
| `forge_run.py` | Documents → signed shard pipeline (run it: see "Creating a Shard") |
| `integration_test.py` | End-to-end proof: forge → genesis → verify → spectra (clarion leg skips without graphkdf) |

## Cryptographic suites

| Suite | Algorithm | Status |
|-------|-----------|--------|
| `axm-hybrid1` | Ed25519 ‖ ML-DSA-44 (FIPS 204) — both must verify | The one and only v1 suite |

Hashing is BLAKE3 (Merkle tree, shard identity) with RFC 6962 odd-leaf
promotion and domain separation. Keys: 3904-byte secret blob, 1344-byte
public key, 2484-byte signature (`axm-build keygen` generates pairs). The
old `ed25519` and `axm-blake3-mldsa44` suites are gone: shards built with <!-- drift-ok: accurate v1 statement that the pre-reset suites are retired -->
them are v0.x prototypes and cannot be verified by v1.

> **Roadmap note — done.** Genesis [RFC 0002](https://github.com/BigBirdReturns/axm-genesis/blob/main/rfcs/0002-v1-reset.md)
> (accepted 2026-07-02) landed: one hybrid suite (`axm-hybrid1`), canonical
> JSONL core tables, derived shard identity. This repo is re-pinned and
> adapted: Spectra loads the JSONL tables into DuckDB at mount time
> (rebuildable cache outside the shard directory).

## License

Apache-2.0
