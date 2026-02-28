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
| **Nodal Flow** | `nodalflow/` | Desktop UI (Tauri + Svelte + DuckDB) |

## Dependency graph

```
axm-genesis (cryptographic kernel — immutable)
  axm_build.*    compiler, Merkle, signing
  axm_verify.*   verifier, error codes, schemas
       ↑
axm-core (this repo — orchestration hub)
  pyproject.toml declares axm-genesis@v1.2.0
  forge/         document ingestion
  spectra/       runtime query
  clarion/       encryption transport
  nodalflow/     desktop UI
       ↑
axm-embodied (physical liability spoke)
axm-<other>     (future spokes)
```

`axm-core` does not vendor `axm-genesis`. The genesis kernel is a declared dependency pinned to a release tag.

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate

# Install axm-core root (pulls axm-genesis@v1.2.0 automatically)
pip install -e .

# Install forge separately
pip install -e ./forge

# Verify the gold shard (comes from axm-genesis)
axm-verify shard $(pip show axm-genesis | grep Location | awk '{print $2}')/axm_genesis_data/shards/gold/fm21-11-hemorrhage-v1/ \
  --trusted-key <path-to>/keys/canonical_test_publisher.pub
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

### Single article from Wikipedia

```bash
python nodal_run.py "Tranexamic acid"
python nodal_run.py --source my_document.txt --out-dir out/my_doc
```

## Installing sub-components separately

Forge, Spectra, and Clarion each have their own `pyproject.toml`. Install them as needed:

```bash
pip install -e ./forge     # axm-forge CLI
pip install -e ./clarion   # topology-bound encryption
```

## Nodal Flow (Desktop UI)

```bash
cd nodalflow
npm install
npm run tauri dev
```

Mount a shard → query in natural language → click any citation to verify source bytes.

## What's frozen (from axm-genesis)

The shard layout, Merkle computation, Parquet schemas, identifier generation, and the gold shard (`fm21-11-hemorrhage-v1`) are frozen in the Genesis spec. The gold shard is the definition of correctness.

See `INVARIANTS.md` for absolute constraints on all changes.

## Key files

| File | Purpose |
|------|---------|
| `pyproject.toml` | Root package — declares axm-genesis dependency |
| `forge_run.py` | Documents → signed shard pipeline |
| `nodal_run.py` | Single-article pipeline (Wikipedia → shard) |
| `integration_test.py` | End-to-end test: forge → genesis → verify → clarion → spectra |
| `INVARIANTS.md` | Absolute constraints |
| `EXTENSIONS_REGISTRY.md` | Extension Parquet schemas |

## Cryptographic suites

| Suite | Algorithm | Status |
|-------|-----------|--------|
| Ed25519 | Ed25519 | Legacy, backward compatible |
| `axm-blake3-mldsa44` | ML-DSA-44 (FIPS 204) | Default for new shards |

Both use Blake3 for hashing. Merkle construction differs by suite: Ed25519 uses duplicate odd-leaf; axm-blake3-mldsa44 uses RFC 6962 odd-leaf promotion with domain separation. Old shards verify under new verifiers.

## License

MIT
