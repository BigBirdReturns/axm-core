# AXM Core

The orchestration hub of the AXM ecosystem. Sits between the cryptographic kernel (axm-genesis) and domain spokes (axm-embodied and others).

```
axm-genesis  ←  axm-core  ←  spokes
  kernel          hub
```

## Components

| Component | Location | Purpose |
|-----------|----------|---------|
| **axm-genesis** | declared dependency | Shard spec, compiler, verifier, post-quantum crypto |
| **Forge** | `forge/` | Document extraction pipeline (tier 0/1 regex + tier 3 LLM) |
| **Spectra** | `spectra/` | Runtime query engine (DuckDB + SQL gate) |
| **Clarion** | `clarion/` | Topology-bound encryption (GraphKDF) |
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

> **Known issue (v1.2.0 tag):** the `v1.2.0` compiler writes `created_at` at the
> manifest top level; the strict verifier introduced after v1.2.0 requires
> `metadata.created_at` and rejects such shards with `E_MANIFEST_SCHEMA`. This
> repo therefore pins the fix commit on branch
> `claude/durability-report-test-fixes-4hbf2v` rather than the `v1.2.0` tag.
> Shards built with the old v1.2.0 snapshot fail strict verification until
> rebuilt.

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate

# Install axm-core root (pulls the pinned axm-genesis automatically)
pip install -e .

# Install forge separately
pip install -e ./forge

# Verify the gold shard. The installed axm-genesis wheel does not ship shard
# data; the gold shard lives in the axm-genesis repo checkout, assumed here to
# be a sibling of this repo.
axm-verify shard ../axm-genesis/shards/gold/fm21-11-hemorrhage-v1/ \
  --trusted-key ../axm-genesis/keys/canonical_test_publisher.pub
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

The shard layout, Merkle computation, Parquet schemas, identifier generation, and the gold shard (`fm21-11-hemorrhage-v1`) are frozen in the Genesis spec. The gold shard is the definition of correctness.

See `INVARIANTS.md` for absolute constraints on all changes.

## Key files

| File | Purpose |
|------|---------|
| `pyproject.toml` | Root package — declares axm-genesis dependency |
| `forge_run.py` | Documents → signed shard pipeline |
| `integration_test.py` | End-to-end test: forge → genesis → verify → clarion (optional, skipped without graphkdf) → spectra |
| `INVARIANTS.md` | Absolute constraints |
| `EXTENSIONS_REGISTRY.md` | Extension Parquet schemas |

## Cryptographic suites

| Suite | Algorithm | Status |
|-------|-----------|--------|
| Ed25519 | Ed25519 | Legacy, backward compatible |
| `axm-blake3-mldsa44` | ML-DSA-44 (FIPS 204) | Default for new shards |

Both use Blake3 for hashing. Merkle construction differs by suite: Ed25519 uses duplicate odd-leaf; axm-blake3-mldsa44 uses RFC 6962 odd-leaf promotion with domain separation. Old shards verify under new verifiers, with one known exception: shards built by the v1.2.0 compiler snapshot carry a top-level `created_at` and fail the post-v1.2.0 strict manifest check (see the known-issue note above).

## License

Apache-2.0
