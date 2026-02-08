# Nodal Flow v0.2: Sovereign Graph Engine

Local-first AI with verified knowledge retrieval from AXM Genesis shards.

## What This Is

**NOT a chatbot.** This is a Sovereign Graph Engine.

```
Traditional RAG:
  Query → Embed → Vector Search → Top K chunks → Stuff into LLM → Hope it's right

Nodal Flow:
  Query → SQL Graph Traversal → Exact Verified Claims → LLM Formats with Citations → Provable Truth
```

The LLM is a **formatter**, not an **author**. The shard is the source of truth.

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                           NODAL FLOW v0.2                            │
├──────────────────────────────────────────────────────────────────────┤
│  Frontend (Svelte)                                                   │
│  ├── Chronicle Model     → Infinite scroll, all nodes accumulate    │
│  ├── Node Types          → intent, chat, citation, viz, code, etc.  │
│  ├── Source Viewer       → Green Padlock verification panel         │
│  ├── Stats Panel         → Shard statistics and tier breakdown      │
│  └── Browse Mode         → Direct graph exploration                 │
├──────────────────────────────────────────────────────────────────────┤
│  Backend (Rust/Tauri)                                                │
│  ├── main.rs             → Command routing, context injection       │
│  └── vault.rs            → DuckDB engine, graph traversal, verify   │
├──────────────────────────────────────────────────────────────────────┤
│  Knowledge Layer (AXM Genesis Shards)                                │
│  ├── graph/claims.parquet      → Subject-Predicate-Object triples   │
│  ├── graph/entities.parquet    → Entity labels and metadata         │
│  ├── graph/provenance.parquet  → Byte-range source links            │
│  ├── evidence/spans.parquet    → Extracted text evidence            │
│  ├── content/*.txt             → Original source documents          │
│  └── manifest.json             → Merkle root, signatures, metadata  │
├──────────────────────────────────────────────────────────────────────┤
│  Inference Layer (Ollama)                                            │
│  └── Local LLM (llama3, mistral, etc.)                              │
└──────────────────────────────────────────────────────────────────────┘
```

## Key Features

### Verified Claims with Provenance
Every claim returned from the graph includes:
- **claim_id**: Unique identifier for graph traversal
- **subject/predicate/object**: The triple
- **object_type**: "entity" or "literal:string"
- **tier**: Extraction confidence (0=high, 1=medium, 2=LLM-extracted)
- **evidence**: The exact text from the source
- **source_hash**: SHA-256 of the content file
- **byte_start/byte_end**: Exact location in source

### Green Padlock Verification
Click any citation to:
1. See the byte range in the original source
2. Verify the evidence text matches exactly
3. Confirm cryptographic provenance chain

### Tier Filtering
Control which claims you trust:
- **Tier 0**: Rule-based extraction, highest confidence
- **Tier 1**: Medium confidence
- **Tier 2**: LLM-extracted, may need review

### Direct Graph Access
- Browse all claims without LLM
- Search the vault directly (Ctrl+Shift+Enter)
- Execute arbitrary SQL on the graph
- View shard statistics

## Prerequisites

1. **Rust** (latest stable): https://rustup.rs
2. **Node.js** (18+): https://nodejs.org
3. **Ollama**: https://ollama.ai

```bash
# Install Ollama and pull a model
curl -fsSL https://ollama.ai/install.sh | sh
ollama pull llama3
ollama serve  # Run in background
```

## Build & Run

```bash
cd nodalflow-v2

# Install dependencies
npm install

# Development (hot reload)
npm run tauri dev

# Production build
npm run tauri build
```

**First build takes 5-10 minutes** (compiles DuckDB from source).

## Usage

### Basic Workflow

1. **Start Ollama**: `ollama serve`
2. **Launch Nodal Flow**: `npm run tauri dev`
3. **Mount a Shard**: Click "Mount Shard" → Select shard directory
4. **Query**: Ask questions about the shard content

### Example Queries (with gold shard)

```
"What treats severe bleeding?"
→ Returns citation with provenance: bytes 360-433

"How do I control hemorrhage?"
→ Multiple claims with tier indicators

"When should I use a tourniquet?"
→ Returns precondition claim with literal object
```

### Keyboard Shortcuts

- **Enter**: Submit query to LLM
- **Ctrl+Shift+Enter**: Search vault only (no LLM)
- **Escape**: Close panels

## API Reference

### Tauri Commands

**Cortex (LLM)**
- `check_cortex_status()` → String
- `list_models()` → Vec<String>
- `query_ollama(prompt, model?, maxTier?)` → EnrichedResponse

**Vault (Graph)**
- `mount_vault(path)` → ShardMetadata
- `unmount_vault()` → ()
- `get_shard_info()` → Option<ShardMetadata>
- `query_vault(searchTerm, maxTier?, limit?)` → Vec<VerifiedClaim>
- `get_all_claims(maxTier?, limit?)` → Vec<VerifiedClaim>
- `get_claims_for_entity(entityId)` → Vec<VerifiedClaim>
- `get_content_slice(sourceHash, byteStart, byteEnd)` → String
- `verify_claim(claim)` → bool
- `execute_sql(sql)` → Vec<Value>
- `get_statistics()` → Value
- `verify_shard()` → String

### Data Structures

```typescript
interface VerifiedClaim {
  claim_id: string;       // "c_..."
  subject: string;        // Entity label
  subject_id: string;     // "e_..."
  predicate: string;      // Relationship
  object: string;         // Entity label or literal value
  object_id: string;      // "e_..." or ""
  object_type: string;    // "entity" | "literal:string"
  tier: number;           // 0, 1, or 2
  evidence: string;       // Extracted text
  source_hash: string;    // SHA-256 hex
  byte_start: number;     // Offset in content file
  byte_end: number;       // End offset
}

interface ShardMetadata {
  spec_version: string;
  shard_id: string;
  title: string;
  namespace: string;
  created_at: string;
  publisher_id: string;
  publisher_name: string;
  license: string;
  merkle_root: string;
  entity_count: number;
  claim_count: number;
  sources: SourceFile[];
  trust_level: TrustLevel;
}

type TrustLevel = "Verified" | "SignatureOnly" | "Unverified" | "Failed";
```

## File Structure

```
nodalflow-v2/
├── src/
│   ├── App.svelte          # Main UI (2000+ lines)
│   └── main.js             # Entry point
├── src-tauri/
│   ├── src/
│   │   ├── main.rs         # Tauri commands (~400 lines)
│   │   └── vault.rs        # Graph engine (~700 lines)
│   ├── Cargo.toml          # Dependencies (duckdb bundled, blake3, ed25519)
│   └── tauri.conf.json     # Tauri config
├── index.html
├── package.json
├── vite.config.js
└── svelte.config.js
```

## Verification Module

The vault includes a foundational verification module:

```rust
// Compute Merkle root per AXM Spec Section 4
vault::verify::compute_merkle_root(shard_path) → Result<String>

// Verify shard integrity
vault::verify::verify_shard(shard_path) → Result<TrustLevel>
```

Currently verifies Merkle root. Signature verification (Ed25519) is stubbed for Phase 2.

## Known Limitations

1. **Signature verification not yet implemented** - Merkle root verification works, Ed25519 signature checking is next
2. **Single shard at a time** - Multi-shard federation planned for v0.3
3. **No shard hot-reload** - Must unmount/remount to refresh
4. **Limited FTS** - Uses ILIKE; DuckDB FTS planned for large shards

## Roadmap

- **v0.2** (current): Complete graph engine with verification foundation
- **v0.3**: Ed25519 signature verification, multi-shard federation
- **v0.4**: Clarion integration, trust propagation
- **v0.5**: Forge compilation pipeline integration

## Creating Shards

Use AXM Genesis tooling:

```bash
# From axm-production-v5/genesis
pip install -e .
axm-compile input.pdf --output my-shard/
```

## License

Sandhu Consulting Group - Proprietary

---

*"The LLM is a formatter, not an author. The shard is the source of truth."*
