# AXM Forge v5.0

**Genesis-Compliant Knowledge Extraction Engine**

Forge extracts structured claims from documents and emits `candidates.jsonl`.
Genesis (`axm-build`) compiles those candidates into verified shards.

## The Contract

```
Document → Forge → candidates.jsonl + source.txt
                          ↓
                   Genesis (axm-build)
                          ↓
                   Genesis (axm-verify) ← THE HARD GATE
                          ↓
                   Verified Shard
```

## Installation

```bash
pip install -e ./forge
pip install -e ./genesis  # Required for axm-build
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
  --llm-provider openai
```

## Commands

| Command | Description |
|---------|-------------|
| `extract` | Extract candidates only |
| `build` | Full pipeline: extract → compile → verify |
| `verify` | Verify shard using axm-verify |

## What Changed from v1.x

- Now emits `candidates.jsonl` instead of custom format
- Calls Genesis `axm-build` and `axm-verify`
- Clarion v1.1 with fixed AAD

## License

MIT
