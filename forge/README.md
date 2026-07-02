# AXM Forge v5.0

**Genesis-Compliant Knowledge Extraction Engine**

Forge extracts structured claims from documents and emits `candidates.jsonl`.
The Genesis v1 compiler (`axm_build.compiler_generic.compile_generic_shard`)
compiles those candidates into verified `axm-hybrid1` shards.

## The Contract

```
Document → Forge → candidates.jsonl + source.txt
                          ↓
                   Genesis compiler (CompilerConfig → compile_generic_shard)
                          ↓
                   Genesis (axm-verify) ← THE HARD GATE
                          ↓
                   Verified Shard (canonical JSONL, axm-hybrid1 signature)
```

Signing needs a 3904-byte axm-hybrid1 secret key blob (`axm-build keygen`
generates one); without a configured key, emission generates a throwaway
keypair whose signature proves integrity, never authenticity. Temporal
candidate keys (valid_from / valid_until / temporal_context — added by
`axm_forge.derivation.annotate_temporal_candidates`) become the sealed
`ext/temporal@1.jsonl`; nothing outside the compiler ever writes into a
shard.

## Installation

```bash
pip install -e ./forge
pip install -e ../axm-genesis  # kernel checkout (sibling repo) — required for axm-build
```

## Usage

### Extract Only
```bash
axm-forge extract document.pdf --out ./extraction/
```

### Full Pipeline
```bash
axm-forge build document.pdf \
  --out ./shards/ \
  --namespace medical/protocols
```

### With Encryption
```bash
axm-forge build document.pdf \
  --out ./shards/ \
  --namespace medical/protocols \
  --encrypt
```

### With LLM
```bash
axm-forge build document.pdf \
  --out ./shards/ \
  --namespace medical/protocols \
  --enable-llm \
  --llm-provider ollama
```

`--llm-provider` defaults to `ollama`; `openai`, `anthropic`, and `mock` are also accepted.

## Commands

| Command | Description |
|---------|-------------|
| `extract` | Extract candidates only |
| `build` | Full pipeline: extract → compile → verify |
| `verify` | Verify shard using axm-verify |

## What Changed from v1.x

- Genesis v1 (RFC 0002): emission targets `CompilerConfig` /
  `compile_generic_shard` with the hybrid1 key blob — one suite, canonical
  JSONL tables, no Parquet in shards
- Emits `candidates.jsonl` instead of custom format
- Verification via `axm-verify` (CLI shape frozen: `axm-verify shard PATH --trusted-key KEY`)
- Clarion v2.0 envelope (envelope.json + blobs/) with topology-bound GraphKDF keys

## License

MIT
