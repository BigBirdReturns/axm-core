"""
Forge Shard Emission

Emits AXM shards with optional Clarion encryption.
Uses the clarion package for topology-bound encryption.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Dict, Optional
import json
import base64
import secrets

from axm_forge.models.claims import Claim

# Import from external packages (not internal modules)
from graphkdf import Edge, derive_keys


def _write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _claims_to_edges(claims: List[Claim]) -> List[Edge]:
    """Convert Forge Claim objects to GraphKDF Edge objects."""
    edges = []
    for c in claims:
        subj = None
        obj = None
        for a in c.args:
            if a.role == "subject":
                subj = a.entity_id
            elif a.role == "object":
                obj = a.entity_id
        if subj is not None and obj is not None:
            edges.append(Edge(subject=subj, predicate=c.predicate, object=obj))
    return edges


def emit_shard(
    out_dir: Path,
    doc_id: str,
    claims_green: List[Claim],
    claims_red: List[Claim],
    epoch: str,
    root_secret_b64: str,
    salt_b64: Optional[str] = None,
    encrypt: bool = False,
) -> Path:
    """Emit an AXM shard with optional Clarion encryption.
    
    Args:
        out_dir: Output directory
        doc_id: Document identifier
        claims_green: Green (public) claims
        claims_red: Red (classified) claims
        epoch: Key rotation epoch
        root_secret_b64: Base64-encoded user secret
        salt_b64: Optional base64-encoded salt (random if not provided)
        encrypt: Whether to encrypt the shard
    
    Returns:
        Path to the created shard directory
    """
    shard_dir = out_dir / f"{doc_id}.axm"
    graph_dir = shard_dir / "graph"
    shard_dir.mkdir(parents=True, exist_ok=True)
    graph_dir.mkdir(parents=True, exist_ok=True)

    # Convert claims to edges for topology computation
    all_claims = claims_green + claims_red
    edges = _claims_to_edges(all_claims)
    
    # Derive topology hash using GraphKDF
    user_secret = base64.b64decode(root_secret_b64)
    salt = base64.b64decode(salt_b64) if salt_b64 else secrets.token_bytes(32)
    
    result = derive_keys(
        edges,
        user_secret,
        salt=salt,
        epoch=epoch,
        partition_labels=["Green", "Red"],
        domain=b"axm-clarion",
    )
    
    topo_b64 = base64.b64encode(result.topology_hash).decode("ascii")
    salt_b64_out = base64.b64encode(salt).decode("ascii")

    manifest: Dict = {
        "doc_id": doc_id,
        "format": "axm_forge_shard_v2",
        "integrity": {"topology_hash_b64": topo_b64},
    }

    if encrypt:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        
        k_green = result.partition_keys["Green"]
        k_red = result.partition_keys["Red"]
        
        aad = json.dumps({
            "doc_id": doc_id,
            "epoch": epoch,
            "topology_hash_b64": topo_b64
        }, sort_keys=True).encode("utf-8")

        green_bytes = ("\n".join(
            json.dumps(c.to_dict(), ensure_ascii=False) for c in claims_green
        ) + "\n").encode("utf-8")
        red_bytes = ("\n".join(
            json.dumps(c.to_dict(), ensure_ascii=False) for c in claims_red
        ) + "\n").encode("utf-8")

        # Encrypt with AES-256-GCM
        def _encrypt(key: bytes, plaintext: bytes, aad: bytes) -> dict:
            nonce = secrets.token_bytes(12)
            aesgcm = AESGCM(key)
            ciphertext = aesgcm.encrypt(nonce, plaintext, aad)
            return {
                "nonce_b64": base64.b64encode(nonce).decode("ascii"),
                "ciphertext_b64": base64.b64encode(ciphertext).decode("ascii"),
            }
        
        green_enc = _encrypt(k_green, green_bytes, aad)
        red_enc = _encrypt(k_red, red_bytes, aad)
        
        (graph_dir / "claims_green.jsonl.enc").write_text(
            json.dumps(green_enc), encoding="utf-8"
        )
        (graph_dir / "claims_red.jsonl.enc").write_text(
            json.dumps(red_enc), encoding="utf-8"
        )

        manifest["clarion"] = {
            "version": "2.0",
            "kdf": {
                "name": "GraphKDF",
                "version": "1.0.0",
                "domain": "axm-clarion",
                "topology_hash_version": "v3",
            },
            "algorithm": "AES-256-GCM",
            "epoch": epoch,
            "salt_b64": salt_b64_out,
            "topology_hash_b64": topo_b64,
            "partitions": [
                {"color": "Green", "file": "graph/claims_green.jsonl.enc"},
                {"color": "Red", "file": "graph/claims_red.jsonl.enc"},
            ],
        }
    else:
        _write_jsonl(graph_dir / "claims_green.jsonl", (c.to_dict() for c in claims_green))
        _write_jsonl(graph_dir / "claims_red.jsonl", (c.to_dict() for c in claims_red))
        manifest["clarion"] = {
            "version": "2.0",
            "algorithm": "none",
            "epoch": epoch,
            "salt_b64": salt_b64_out,
            "topology_hash_b64": topo_b64,
            "partitions": [
                {"color": "Green", "file": "graph/claims_green.jsonl"},
                {"color": "Red", "file": "graph/claims_red.jsonl"},
            ],
        }

    (shard_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return shard_dir
