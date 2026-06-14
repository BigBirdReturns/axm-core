# AXM Spectra v1.0

**Genesis-Compliant Knowledge Runtime**

Spectra mounts verified Genesis shards and provides SQL/vector/chat queries.

## The Hard Gate

```
Shard/Envelope → Decrypt (if Clarion) → axm-verify → Mount → Query
                                              ↑
                                         MUST PASS
                                    No exceptions in production
```

## Installation

```bash
pip install -e ./spectra
pip install -e ./genesis  # Required for axm-verify
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
| `SPECTRA_DEV_MODE` | `0` | Enable dev mode (bypasses axm-verify) |
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

- Hard gate on axm-verify (no production bypass)
- Clarion v2.0 decryption support (v1.x still accepted for backward compatibility)
- Removed `SPECTRA_ALLOW_LAYOUT_FALLBACK`

## License

MIT
