# AXM Core

The complete stack for creating and consuming verified knowledge shards.

```
Document → Forge → Genesis → Shard → Nodal Flow
           extract   compile   mount    query + verify
```

## Components

| Component | Purpose | Status |
|-----------|---------|--------|
| **Genesis** | Shard specification, compiler, verifier | v1.1.0 — PQ signing (ML-DSA-44) + Ed25519 backward compat |
| **Forge** | Document extraction pipeline | Tier 0/1 regex + Tier 3 LLM (Ollama) |
| **Spectra** | Runtime query engine (DuckDB + SQL gate) | Operational |
| **Clarion** | Topology-bound encryption (GraphKDF) | Complete |
| **Nodal Flow** | Desktop UI (Tauri + Svelte + DuckDB) | v2.0 — Vault, citations, verification |

## Quick Start

```bash
# Install
python -m venv .venv && source .venv/bin/activate
pip install blake3 cryptography pyarrow duckdb click pysbd pynacl dilithium-py

export PYTHONPATH="$PWD/genesis/src:$PWD/forge:$PWD/spectra"

# Verify the gold shard
python -m axm_verify.cli shard genesis/shards/gold/fm21-11-hemorrhage-v1/ \
  --trusted-key genesis/shards/gold/fm21-11-hemorrhage-v1/sig/publisher.pub

# Run tests (34 pass, 10 skip without gold shard binaries)
cd genesis && python -m pytest tests/ -v
```

## Creating a Shard

### From structured documents (tier 0/1, no LLM needed)

```bash
python forge_run.py --input ./my_docs/ --output ./out/my_shard/ --skip-llm
```

### With LLM extraction (requires Ollama)

```bash
ollama serve &
ollama pull llama3:8b

python forge_run.py --input ./my_docs/ --output ./out/my_shard/

# Checkpoints automatically. If it crashes, rerun the same command.
```

### Plan before running

```bash
python forge_run.py --input ./my_docs/ --plan-only
```

## Using Nodal Flow

```bash
cd nodalflow
npm install
npm run tauri dev
```

1. Mount a shard directory
2. Query in natural language
3. Click any citation to verify source bytes

## Architecture

```
Nodal Flow (Tauri + Svelte)
    │
    ▼
Vault (Rust + DuckDB)         ← mounts shard, queries claims, verifies spans
    │
    ▼
Cortex (Ollama)               ← formats responses with citation nodes
    │
    ▼
AXM Shard                     ← graph/ + evidence/ + content/ + sig/
    │
    ▼
Genesis Compiler               ← candidates.jsonl → signed shard
    │
    ▼
Forge Extractors               ← tier 0 regex, tier 1 rules, tier 3 LLM
```

## Cryptographic Suites

New shards default to post-quantum signing:

| Suite | Algorithm | Key Size | Status |
|-------|-----------|----------|--------|
| Ed25519 | Ed25519 | 32 B | Legacy, backward compatible |
| `axm-blake3-mldsa44` | ML-DSA-44 (FIPS 204) | 1312 B | Default for new shards |

Both use Blake3 Merkle trees and SHA-256 content hashing.

## Key Files

| File | Purpose |
|------|---------|
| `forge_run.py` | Set-and-forget ingestion: documents → signed shard |
| `nodal_run.py` | Single-article pipeline (Wikipedia → shard) |
| `genesis/spec/v1.0/SPECIFICATION.md` | Frozen protocol definition |
| `INVARIANTS.md` | Absolute constraints on all code changes |
| `IDENTITY.md` | How IDs are generated and what survives rebuilds |
| `EXTENSIONS_REGISTRY.md` | Extension parquet schemas |

## What's Frozen

The Genesis v1.0 specification (Sections 1-10), shard layout, Merkle computation, parquet schemas, and identifier generation are frozen. The gold shard is the definition of correctness.

Section 11 (cryptographic suites) was added in v1.1.0 as a backward-compatible extension.

Everything else — extractors, UI, query engine, encryption — can change freely.

## License

MIT
