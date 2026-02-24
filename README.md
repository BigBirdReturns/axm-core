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
| **Registry** | Artifact naming layer — maps human refs to shard_ids | v0 — file-backed |

## Quick Start

```bash
# Install
python -m venv .venv && source .venv/bin/activate
pip install blake3 cryptography pyarrow duckdb click pysbd pynacl dilithium-py jsonschema

export PYTHONPATH="$PWD/genesis/src:$PWD/forge:$PWD/spectra"

# Verify the gold shard
python -m axm_verify.cli shard genesis/shards/gold/fm21-11-hemorrhage-v1/ \
  --trusted-key genesis/shards/gold/fm21-11-hemorrhage-v1/sig/publisher.pub

# Run tests (34 pass, 10 skip without gold shard binaries)
cd genesis && python -m pytest tests/ -v
```

## Unified CLI

The `axm` CLI is the orchestration layer. It translates human names into
cryptographic shard_ids and routes them to the correct subsystem without
violating their boundaries.

```bash
# Build: ingest a document, compile a shard, register it
axm build ./docs/fm21.pdf --name medical/fm21

# Verify: run Genesis hard verifier against a named artifact
axm verify medical/fm21

# Mount: verify then mount into Spectra runtime
axm mount medical/fm21

# Pin: snapshot current state for reproducible runs
axm pin medical/fm21 legal/lafc-divorce-2024

# Mount from lockfile (policy shifts cannot move these feet)
axm mount --lock axm.lock.json medical/fm21

# Resolve: see what a name points to
axm resolve medical/fm21

# History: see the lineage of an artifact
axm history medical/fm21

# Alias: add a shorthand
axm alias medical/fm21 fm21:latest
```

Registry state lives in `registry/artifacts.json`. See `registry/SCHEMA.md`
for the full schema and `cli/CONTRACT.md` for the complete verb reference.

## Direct Pipeline Runners

For single-document or scripted workflows that bypass the CLI:

```bash
# forge_run.py: directory of documents → signed shard (with checkpointing)
python forge_run.py --input ./legal_docs/ --output ./out/legal/
python forge_run.py --input ./legal_docs/ --plan-only
python forge_run.py --input ./legal_docs/ --resume

# nodal_run.py: single article → signed shard (Wikipedia or local file)
python nodal_run.py "Tranexamic acid"
python nodal_run.py "https://en.wikipedia.org/wiki/Aspirin"
python nodal_run.py --source my_document.txt --out-dir out/my_doc

# demo_query.py: mount a shard and query it with hallucination checking
python demo_query.py --shard genesis/shards/gold/fm21-11-hemorrhage-v1 \
    --question "When should I apply a tourniquet?"
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
axm_cli.py (Registry + orchestration)   ← human names resolved here only
    │
    ├── Registry (artifacts.json)        ← name → shard_id mapping
    │
    ├── Forge (subprocess)               ← ingestion + extraction
    │
    ├── Genesis (subprocess)             ← verification
    │
    └── Spectra (HTTP)                   ← mount + query
         │
         ▼
    Vault (Rust + DuckDB)               ← mounts shard, queries claims
         │
         ▼
    AXM Shard                           ← graph/ + evidence/ + content/ + sig/
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
| `axm_cli.py` | Unified CLI orchestrator: build, verify, mount, pin |
| `registry/` | Artifact naming layer — maps human refs to shard_ids |
| `registry/SCHEMA.md` | Registry JSON schema and lockfile contract |
| `cli/CONTRACT.md` | CLI verb reference, exit codes, machine output |
| `forge_run.py` | Set-and-forget ingestion: documents → signed shard |
| `nodal_run.py` | Single-article pipeline (Wikipedia → shard) |
| `demo_query.py` | End-to-end demo: mount → query → hallucination check |
| `genesis/spec/v1.0/SPECIFICATION.md` | Frozen protocol definition |
| `INVARIANTS.md` | Absolute constraints on all code changes |
| `IDENTITY.md` | How IDs are generated and what survives rebuilds |
| `EXTENSIONS_REGISTRY.md` | Extension parquet schemas |
| `docs/DECISION_LOG.md` | Architecture decisions and rationale |

## What's Frozen

The Genesis v1.0 specification (Sections 1-10), shard layout, Merkle computation,
parquet schemas, and identifier generation are frozen. The gold shard is the
definition of correctness.

Section 11 (cryptographic suites) was added in v1.1.0 as a backward-compatible
extension.

Everything else — extractors, UI, query engine, encryption, registry, CLI —
can change freely.

## License

MIT
