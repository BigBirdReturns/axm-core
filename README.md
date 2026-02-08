# AXM Stack v1 - Complete

Assembled: 2026-02-03

## What This Is

A complete, working stack for creating and consuming verified knowledge shards.

**The Pipeline:**
```
Document → Forge → Genesis → Shard → Nodal Flow
           extract   compile   mount    query
                     verify            verify
```

**The Contract:**
- Genesis 1.0 is **FROZEN** - the specification does not change
- Forge produces Genesis-compliant shards
- Nodal Flow only mounts Genesis-verified shards
- Every claim has cryptographic provenance

## Components

| Component | Version | Status | Purpose |
|-----------|---------|--------|---------|
| Genesis | 1.0 | FROZEN | Specification + compiler + verifier |
| Forge | 2.0 | Patched | Document extraction pipeline |
| Clarion | 2.0 | Complete | Optional encryption (GraphKDF) |
| Spectra | 0.3 | Patched | Runtime query engine |
| Nodal Flow | 2.0 | Patched | Desktop UI (Tauri + Svelte) |

## Quick Start

### 1. Set up Python environment

```bash
cd axm-stack-v1

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows

# Install dependencies
pip install blake3 cryptography pyarrow duckdb click

# Set PYTHONPATH
export PYTHONPATH="$PWD/genesis/src:$PWD/forge:$PWD/clarion:$PWD/spectra"
```

### 2. Verify the gold shard

```bash
python -m axm_verify.cli shard genesis/shards/gold/fm21-11-hemorrhage-v1 \
  --trusted-key genesis/shards/gold/fm21-11-hemorrhage-v1/sig/publisher.pub
```

Expected output: `PASS`

### 3. Run Nodal Flow (Desktop UI)

```bash
cd nodalflow

# Install Node dependencies
npm install

# Run in development mode
npm run tauri dev
```

### 4. Use the app

1. **Mount a shard**: Click "📁 Mount Shard" → select `genesis/shards/gold/fm21-11-hemorrhage-v1`
2. **Query**: Type "What treats bleeding?"
3. **Verify**: Click any citation → see source bytes → green checkmark
4. **Create new shard**: Drag a .txt or .pdf file into the window

## End-to-End Flow

### Creating a shard from a document

**Via CLI:**
```bash
python -m axm_forge.cli.main build document.txt \
  --out ./shards/my-shard \
  --namespace my-domain \
  --publisher-id @me \
  --publisher-name "My Name"
```

**Via Nodal Flow:**
1. Drag document into the app window
2. Wait for "✓ Shard created" message
3. Shard auto-mounts
4. Claims appear in chronicle

### What happens under the hood

```
1. Forge reads document.txt
2. Forge extracts claims (tier1_regex patterns)
3. Forge writes source.txt + candidates.jsonl
4. Forge calls: python -m axm_build.cli compile ...
5. Genesis compiles parquet files + manifest
6. Genesis signs with Ed25519
7. Forge calls: python -m axm_verify.cli shard ...
8. Genesis verifies Merkle root + signature
9. Shard directory is ready to mount
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Nodal Flow (Tauri + Svelte)                                │
│  - Mount/unmount shards                                     │
│  - Query with vault-first strategy                          │
│  - Verify provenance (green padlock)                        │
│  - Create shards via document drop                          │
└─────────────────────────────┬───────────────────────────────┘
                              │ invoke()
┌─────────────────────────────┴───────────────────────────────┐
│  Vault (Rust + DuckDB)                                      │
│  - mount_vault, query_vault, verify_claim                   │
│  - create_shard_from_document → calls Forge                 │
└─────────────────────────────┬───────────────────────────────┘
                              │ subprocess
┌─────────────────────────────┴───────────────────────────────┐
│  Forge (Python)                                             │
│  - Extraction: tier1_regex, tier3_llm (optional)            │
│  - Emission: candidates.jsonl → Genesis compile             │
└─────────────────────────────┬───────────────────────────────┘
                              │ subprocess
┌─────────────────────────────┴───────────────────────────────┐
│  Genesis (Python)                                           │
│  - axm_build: compile source + claims → shard               │
│  - axm_verify: verify Merkle root + signature               │
│  - SPECIFICATION: frozen, authoritative                     │
└─────────────────────────────────────────────────────────────┘
```

## Key Files Changed

### Forge: `forge/axm_forge/emission/genesis_emission.py`
- Added `EmissionConfig` and `EmissionResult` dataclasses
- Added `call_axm_build()` - shells out to Genesis with correct CLI flags
- Added `call_axm_verify()` - verifies shard after creation
- Fixed CLI invocation: `--candidates` and `--out` as flags, not positional

### Nodal Flow: `nodalflow/src-tauri/src/main.rs`
- Added `create_shard_from_document` command - calls Forge build
- Added `doctor` command - checks Python/Forge/Genesis availability

### Nodal Flow: `nodalflow/src/App.svelte`
- Added document drop zone with drag-and-drop handling
- Added `handleDocumentDrop()` - creates shard, auto-mounts
- Added `runDoctor()` - health check on startup
- Added drop overlay UI

## Shard Layout (Genesis 1.0 Spec)

```
my-shard/
├── manifest.json          # Metadata + Merkle root
├── graph/
│   ├── claims.parquet     # Subject-predicate-object triples
│   ├── entities.parquet   # Entity definitions
│   └── provenance.parquet # Claim-to-evidence links
├── evidence/
│   └── spans.parquet      # Byte ranges into source
├── content/
│   └── source.txt         # Original document
└── sig/
    ├── publisher.pub      # Ed25519 public key
    └── manifest.sig       # Signature over manifest
```

## Plugin Points

### Adding domain extractors

Create `forge/axm_forge/extraction/tiers/tier1_mydomain.py`:

```python
import re
from typing import List
from axm_forge.models.claims import Claim, Arg, Span

PATTERNS = [
    (r'pattern', 'predicate_name'),
]

def extract(text: str) -> List[Claim]:
    claims = []
    for pattern, predicate in PATTERNS:
        for m in re.finditer(pattern, text):
            claims.append(Claim(
                predicate=predicate,
                args=[Arg(role="subject", entity_id="entity:doc")],
                value=m.group(1),
                source_spans=[Span(start=m.start(), end=m.end(), snippet=m.group(0))],
                tier=1,
            ))
    return claims
```

Register in `forge/axm_forge/extraction/registry.py`.

## Testing

### Verify stack health

```bash
# In Nodal Flow, check console for:
# "AXM Stack Health: {forge_importable: true, genesis_build_importable: true, ...}"

# Or manually:
python -c "import axm_forge; print('Forge OK')"
python -c "import axm_build; print('Genesis build OK')"
python -c "import axm_verify; print('Genesis verify OK')"
```

### Run integration test

```bash
python integration_test.py --input forge/inputs/doc1.txt --workdir ./test_output
```

## Troubleshooting

### "axm_forge module not found"
Set PYTHONPATH:
```bash
export PYTHONPATH="$PWD/genesis/src:$PWD/forge:$PWD/clarion:$PWD/spectra"
```

### "Forge failed: axm-build failed"
Check that Genesis CLI is accessible:
```bash
python -m axm_build.cli --help
```

### "Verification failed"
The shard may be corrupted or tampered. Re-create it from source.

## What's Frozen vs. What Can Change

**FROZEN (do not modify):**
- `genesis/spec/v1.0/SPECIFICATION.md`
- Shard layout structure
- Merkle root computation
- Ed25519 signature scheme
- Parquet schemas

**CAN CHANGE:**
- Forge extractors (add new patterns, new tiers)
- Nodal Flow UI (new features, new panels)
- Clarion encryption parameters
- Documentation

## License

MIT (or as specified in component directories)
