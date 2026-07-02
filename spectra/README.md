# AXM Spectra v1.0

**Genesis-Compliant Knowledge Runtime**

Spectra mounts verified Genesis v1 shards and provides SQL/vector/chat queries.

## The Hard Gate

```
Shard/Envelope → Decrypt (if Clarion) → axm-verify → Mount → Query
                                              ↑
                                         MUST PASS
                                    No exceptions in production
```

## Mounting (Genesis v1)

Shards carry canonical JSONL tables (`graph/*.jsonl`, `evidence/spans.jsonl`,
`ext/*.jsonl`) — no Parquet. At mount time Spectra parses each table with the
stdlib JSON reader and loads it into DuckDB tables plus cross-shard union
views (`claims`, `entities`, `provenance`, `spans`, `temporal`, `lineage`,
`refs`). The loaded tables are a rebuildable cache living in the engine's
DuckDB — outside the sealed shard directory, which is never written to.
Unknown or binary `ext/` formats are skipped with a log line (`ext/` is
opaque to the kernel). The mount id is keyed by the derived shard identity
`sh1_ + BLAKE3(manifest bytes)`; manifests carry no `shard_id` field.

## Installation

```bash
pip install -e ./spectra
pip install -e ../axm-genesis  # kernel checkout (sibling repo) — required for axm-verify
```

## Usage

### Start Server
```bash
uvicorn axiom_runtime.server:app --port 8080
```

### Mount Shard
```bash
curl -X POST http://localhost:8080/mount \
  -H "Content-Type: application/json" \
  -d '{"path": "/path/to/shard"}'
```

### Mount Encrypted Shard
```bash
curl -X POST http://localhost:8080/mount \
  -H "Content-Type: application/json" \
  -d '{"path": "/path/to/shard.clarion", "secret": "<base64>"}'
```

### Query
```bash
curl -X POST http://localhost:8080/query \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT * FROM claims__abc123"}'
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | System health |
| `/catalog` | GET | List mounted shards |
| `/mount` | POST | Mount a shard |
| `/unmount/{id}` | POST | Unmount a shard |
| `/query` | POST | Execute SQL query |
| `/index` | POST | Build vector index |
| `/chat` | POST | Chat with indexed claims |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SPECTRA_TRUSTED_PUBKEY` | shard's embedded key | Path to the pinned 1344-byte trusted publisher key (set this in real deployments) |
| `SPECTRA_DEV_MODE` | `0` | Enable dev mode (dev vault key; layout-only fallback when no verifier is installed) |
| `SPECTRA_DB_PATH` | `spectra.db` | SQLite catalog path |
| `SPECTRA_TEMP_ROOT` | system temp | Temp directory for decryption |
| `SPECTRA_EMBED_PROVIDER` | `mock` | Embedding provider |
| `SPECTRA_CHAT_PROVIDER` | `openai` | Chat provider |

## Clarion Support

Spectra decrypts Clarion envelopes (`envelope.json` + `blobs/`) to a byte-perfect
Genesis shard before running `axm-verify`. The canonical format is **v2.0**
(topology-bound GraphKDF keys). Legacy v1.0/v1.1 envelopes still decrypt for
backward compatibility:
- **v2.0**: GraphKDF topology binding; `plaintext_hash` in AAD (canonical)
- **v1.1**: legacy — `plaintext_hash` in AAD, no topology binding
- **v1.0**: legacy — `blob_hash` in AAD

## What Changed

- Genesis v1 (RFC 0002): shards mount from canonical JSONL (no Parquet in
  shards); shard identity derived from manifest bytes; one `axm-hybrid1`
  suite behind the verify gate
- Hard gate on axm-verify (no production bypass)
- Clarion v2.0 decryption support (v1.x still accepted for backward compatibility)
- Removed `SPECTRA_ALLOW_LAYOUT_FALLBACK`

## License

MIT
