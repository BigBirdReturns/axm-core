# Clarion v2.0

**Topology-Bound Cryptographic Transport for AXM Knowledge Shards**

Clarion encrypts AXM Genesis shards with keys derived from graph topology. Change the graph structure, keys change, decryption fails.

## Installation

```bash
pip install clarion

# With parquet support
pip install clarion[full]
```

## Quick Start

```python
from pathlib import Path
from clarion import encrypt_shard, decrypt_envelope

# Encrypt a Genesis shard
envelope_path, envelope = encrypt_shard(
    shard_path=Path("./my-shard"),
    user_secret=b"your_32_byte_secret_here_xxxxxx",
    epoch="epoch_001",
    colors=["Green", "Red"],
)

# Decrypt an envelope
shard_path, colors = decrypt_envelope(
    envelope_path=envelope_path,
    user_secret=b"your_32_byte_secret_here_xxxxxx",
)
```

## How It Works

```
Genesis Shard ──▶ Extract Edges ──▶ GraphKDF ──▶ Partition Keys ──▶ Encrypt
      │                                 │
  claims.parquet              topology_hash (in envelope)
```

1. Clarion extracts edges from the shard's `claims.parquet`
2. GraphKDF derives keys bound to the topology hash
3. Files are encrypted with AES-256-GCM
4. Envelope stores encrypted blobs + metadata

**Security property:** If an attacker modifies any claim, the topology hash changes, derived keys change, and decryption fails.

## Color Model

Clarion supports multi-level partitions:

| Color | Classification | Typical Use |
|-------|----------------|-------------|
| Green | UNCLASSIFIED | Public doctrine |
| Yellow | CUI | Partner-accessible |
| Red | SECRET | Role-restricted |
| Black | TOP SECRET | Hardware-bound |

Each color gets an independent encryption key.

## API Reference

### `encrypt_shard()`

```python
def encrypt_shard(
    shard_path: Path,
    user_secret: bytes,
    epoch: str,
    out_dir: Optional[Path] = None,
    colors: Optional[List[str]] = None,
    file_color_map: Optional[Dict[str, str]] = None,
    topology_hash_version: str = "v3",
) -> Tuple[Path, ClarionEnvelope]
```

### `decrypt_envelope()`

```python
def decrypt_envelope(
    envelope_path: Path,
    user_secret: bytes,
    out_dir: Optional[Path] = None,
    colors_to_decrypt: Optional[List[str]] = None,
    verify_topology: bool = True,
) -> Tuple[Path, List[str]]
```

## Envelope Format

```json
{
  "clarion_version": "2.0",
  "envelope_id": "<uuid>",
  "shard_id": "<genesis_shard_id>",
  "kdf": {
    "name": "GraphKDF",
    "version": "1.0.0",
    "topology_hash_version": "v3",
    "domain": "axm-clarion",
    "salt_b64": "<base64>",
    "epoch": "<epoch_string>",
    "topology_hash_b64": "<base64>"
  },
  "partitions": [
    {
      "color": "Green",
      "files": [...]
    }
  ]
}
```

## Backward Compatibility

Clarion v2.0 can decrypt v1.0 and v1.1 envelopes (which lack topology binding).

## Dependencies

- **graphkdf**: Topology-bound key derivation (frozen primitive)
- **cryptography**: AES-256-GCM encryption
- **duckdb** (optional): For reading parquet files

## License

MIT
