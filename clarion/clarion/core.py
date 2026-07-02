"""
Clarion v2.0 - Topology-Bound Cryptographic Transport

This module implements the Clarion envelope format for encrypting AXM Genesis shards.
Keys are derived via GraphKDF, binding encryption to graph structure.

ARCHITECTURE:
                                    
  Genesis Shard ──▶ Extract Edges ──▶ GraphKDF ──▶ Partition Keys ──▶ Encrypt Files
        │                                │                                  │
    claims.jsonl                topology_hash                          envelope/
                                (in envelope)                          ├── envelope.json
                                                                       └── blobs/

KEY INSIGHT: GraphKDF derives keys from topology. Change the graph = different keys.

ENVELOPE CONTENTS: data files are encrypted per-partition into blobs/. The
shard's public integrity metadata — top-level manifest.json and the sig/
directory (publisher.pub + signature) — is NOT a secret: it is exactly what
lets a recipient verify the Genesis seal. Those files travel in the envelope
as PLAINTEXT "passthrough" blobs (listed under envelope.json's "passthrough"
key) and are restored byte-identical on decrypt, so a decrypted shard can be
verified and mounted. Envelopes written before this field existed simply have
no "passthrough" entry and still decrypt as before.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Clarion's domain for GraphKDF
CLARION_DOMAIN = b"axm-clarion"

# graphkdf is an optional dependency (not on PyPI). It is imported lazily so
# that `import clarion` works without it; encryption/decryption paths call
# _require_graphkdf() and fail with a clear error if it is absent.
_GRAPHKDF_HINT = (
    "graphkdf is required for Clarion encryption — install clarion[kdf]"
)


def _require_graphkdf():
    """Import the frozen GraphKDF package, raising a clear error if absent."""
    try:
        import graphkdf
    except ImportError as e:
        raise ImportError(_GRAPHKDF_HINT) from e
    return graphkdf


# ============================================================================
# EDGE EXTRACTION FROM GENESIS SHARDS
# ============================================================================

def extract_edges_from_claims(claims: List[Dict[str, Any]]) -> List[Edge]:
    """Extract edges from Genesis claims format.
    
    Genesis claims have: subject, predicate, object, object_type
    We extract (subject, predicate, object) as edges.
    """
    Edge = _require_graphkdf().Edge
    edges = []
    
    for claim in claims:
        subject = claim.get("subject", "")
        predicate = claim.get("predicate", "")
        obj = claim.get("object", "")
        
        if subject and predicate and obj:
            edges.append(Edge(subject=subject, predicate=predicate, object=obj))
    
    return edges


def extract_edges_from_jsonl(claims_jsonl: Path) -> List[Edge]:
    """Extract edges from a Genesis v1 graph/claims.jsonl table."""
    try:
        claims = [
            json.loads(line)
            for line in claims_jsonl.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return extract_edges_from_claims(claims)
    except Exception:
        return []


def extract_edges_from_parquet(claims_parquet: Path) -> List[Edge]:
    """Extract edges from a legacy (v0.x prototype) claims.parquet file."""
    Edge = _require_graphkdf().Edge
    try:
        import duckdb
        con = duckdb.connect(":memory:")
        rows = con.execute(f"""
            SELECT subject, predicate, object
            FROM read_parquet('{claims_parquet}')
        """).fetchall()
        return [Edge(subject=r[0], predicate=r[1], object=r[2]) for r in rows]
    except Exception:
        return []


# ============================================================================
# CLASSIFICATION COLORS
# ============================================================================

@dataclass
class PartitionColor:
    """A classification color/partition."""
    color: str
    classification: str
    priority: int  # Lower = less sensitive, decrypted first
    
    @classmethod
    def green(cls) -> "PartitionColor":
        return cls(color="Green", classification="UNCLASSIFIED", priority=0)
    
    @classmethod
    def yellow(cls) -> "PartitionColor":
        return cls(color="Yellow", classification="CUI", priority=1)
    
    @classmethod
    def red(cls) -> "PartitionColor":
        return cls(color="Red", classification="SECRET", priority=2)
    
    @classmethod
    def black(cls) -> "PartitionColor":
        return cls(color="Black", classification="TOP SECRET", priority=3)


DEFAULT_COLORS = [
    PartitionColor.green(),
    PartitionColor.yellow(), 
    PartitionColor.red(),
    PartitionColor.black(),
]


# ============================================================================
# ENVELOPE DATA STRUCTURES
# ============================================================================

@dataclass
class FileEntry:
    """An encrypted file entry."""
    path: str
    nonce_b64: str
    blob_hash: str
    plaintext_hash: str


@dataclass
class Partition:
    """A color partition containing encrypted files."""
    color: str
    classification: str
    files: List[FileEntry] = field(default_factory=list)


@dataclass
class PassthroughFile:
    """A plaintext (unencrypted) passthrough file entry.

    Used for the shard's public integrity metadata (top-level manifest.json
    and the sig/ directory). The blob in blobs/ is the plaintext itself;
    blob_hash is the SHA-256 of those bytes.
    """
    path: str
    blob_hash: str


@dataclass
class ClarionEnvelope:
    """Clarion v2.0 envelope with GraphKDF.

    `passthrough` lists plaintext blobs (manifest.json, sig/*) restored
    byte-identical on decrypt. Envelopes written before this field existed
    have no "passthrough" key and decrypt exactly as before.
    """
    envelope_id: str
    shard_id: str
    genesis_merkle_root: str
    kdf_salt_b64: str
    kdf_epoch: str
    topology_hash_b64: str
    topology_hash_version: str  # "v2" or "v3"
    partitions: List[Partition]
    files_digest_b64: str
    created_at: str
    passthrough: List[PassthroughFile] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "clarion_version": "2.0",
            "envelope_id": self.envelope_id,
            "shard_id": self.shard_id,
            "genesis_merkle_root": self.genesis_merkle_root,
            "encryption_algo": "AES-256-GCM",
            "kdf": {
                "name": "GraphKDF",
                "version": "1.0.0",
                "topology_hash_version": self.topology_hash_version,
                "algorithm": "HKDF-SHA256-HMAC-SHA256",
                "domain": CLARION_DOMAIN.decode("utf-8"),
                "salt_b64": self.kdf_salt_b64,
                "epoch": self.kdf_epoch,
                "topology_hash_b64": self.topology_hash_b64,
            },
            "partitions": [
                {
                    "color": p.color,
                    "classification": p.classification,
                    "files": [
                        {
                            "path": f.path,
                            "nonce_b64": f.nonce_b64,
                            "blob_hash": f.blob_hash,
                            "plaintext_hash": f.plaintext_hash,
                        }
                        for f in p.files
                    ],
                }
                for p in self.partitions
            ],
            "files_digest_sha256_b64": self.files_digest_b64,
            "created_at": self.created_at,
        }
        if self.passthrough:
            # Plaintext passthrough files (public integrity metadata).
            # Omitted entirely when empty for backward compatibility.
            d["passthrough"] = [
                {"path": e.path, "blob_hash": e.blob_hash, "encrypted": False}
                for e in self.passthrough
            ]
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ClarionEnvelope":
        kdf = d.get("kdf", {})
        partitions = []
        for p in d.get("partitions", []):
            files = [
                FileEntry(
                    path=f["path"],
                    nonce_b64=f["nonce_b64"],
                    blob_hash=f["blob_hash"],
                    plaintext_hash=f.get("plaintext_hash", ""),
                )
                for f in p.get("files", [])
            ]
            partitions.append(Partition(
                color=p["color"],
                classification=p.get("classification", ""),
                files=files,
            ))
        
        return cls(
            envelope_id=d["envelope_id"],
            shard_id=d["shard_id"],
            genesis_merkle_root=d.get("genesis_merkle_root", ""),
            kdf_salt_b64=kdf.get("salt_b64", ""),
            kdf_epoch=kdf.get("epoch", ""),
            topology_hash_b64=kdf.get("topology_hash_b64", ""),
            topology_hash_version=kdf.get("topology_hash_version", "v2"),
            partitions=partitions,
            files_digest_b64=d.get("files_digest_sha256_b64", ""),
            created_at=d.get("created_at", ""),
            passthrough=[
                PassthroughFile(path=e["path"], blob_hash=e["blob_hash"])
                for e in d.get("passthrough", [])
            ],
        )


# ============================================================================
# ENCRYPTION
# ============================================================================

def _compute_aad(envelope_id: str, shard_id: str, color: str, 
                 path: str, plaintext_hash: str, topology_hash_b64: str) -> bytes:
    """Compute AAD for file encryption.
    
    Binds ciphertext to:
    - Envelope identity
    - Shard identity
    - Partition color
    - File path
    - Plaintext content (via hash)
    - Graph topology (via hash)
    """
    return json.dumps({
        "envelope_id": envelope_id,
        "shard_id": shard_id,
        "color": color,
        "path": path,
        "plaintext_hash": plaintext_hash,
        "topology_hash_b64": topology_hash_b64,
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _compute_files_digest(partitions: List[Partition]) -> str:
    """Compute digest over all files for envelope integrity."""
    all_files = []
    for p in partitions:
        for f in p.files:
            all_files.append({
                "color": p.color,
                "path": f.path,
                "nonce_b64": f.nonce_b64,
                "blob_hash": f.blob_hash,
                "plaintext_hash": f.plaintext_hash,
            })
    
    all_files.sort(key=lambda x: (x["color"], x["path"]))
    canonical = json.dumps(all_files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(hashlib.sha256(canonical).digest()).decode("ascii")


def encrypt_shard(
    shard_path: Path,
    user_secret: bytes,
    epoch: str,
    out_dir: Optional[Path] = None,
    colors: Optional[List[str]] = None,
    file_color_map: Optional[Dict[str, str]] = None,
    topology_hash_version: str = "v3",
) -> Tuple[Path, ClarionEnvelope]:
    """Encrypt a Genesis shard into a Clarion envelope.
    
    Args:
        shard_path: Path to Genesis shard directory
        user_secret: User secret for key derivation (32 bytes recommended)
        epoch: Key rotation identifier
        out_dir: Output directory (auto-generated if not provided)
        colors: List of colors to use (default: ["Green"])
        file_color_map: Map of relative_path -> color (default: all Green)
        topology_hash_version: "v2" or "v3" (default: "v3")
    
    Returns:
        (envelope_path, envelope_object)
    """
    graphkdf = _require_graphkdf()

    # Load manifest
    manifest_path = shard_path / "manifest.json"
    if not manifest_path.exists():
        raise ValueError("Not a valid Genesis shard: missing manifest.json")
    
    manifest = json.loads(manifest_path.read_text())
    merkle_root = manifest.get("integrity", {}).get("merkle_root", "")
    # Genesis v1: shard identity is derived from the manifest bytes, never
    # stored in the manifest (spec section 9).
    try:
        import blake3
        shard_id = "sh1_" + blake3.blake3(manifest_path.read_bytes()).hexdigest()
    except Exception:
        shard_id = manifest.get("shard_id", "unknown")  # v0.x prototype fallback

    # Extract edges from claims (v1 canonical JSONL; legacy parquet fallback)
    claims_jsonl = shard_path / "graph" / "claims.jsonl"
    claims_parquet = shard_path / "graph" / "claims.parquet"
    if claims_jsonl.exists():
        edges = extract_edges_from_jsonl(claims_jsonl)
    elif claims_parquet.exists():
        edges = extract_edges_from_parquet(claims_parquet)
    else:
        edges = []
    
    # Select topology hash function
    if topology_hash_version == "v3":
        hash_fn = graphkdf.v3_length_prefixed
    else:
        hash_fn = graphkdf.v2_legacy_delimited

    # Derive keys using GraphKDF
    colors = colors or ["Green"]
    salt = secrets.token_bytes(32)

    result = graphkdf.derive_keys(
        edges,
        user_secret,
        salt=salt,
        epoch=epoch,
        partition_labels=colors,
        topology_hash_fn=hash_fn,
        domain=CLARION_DOMAIN,
    )
    
    # Create envelope
    envelope_id = secrets.token_hex(16)
    topology_hash_b64 = base64.b64encode(result.topology_hash).decode("ascii")
    
    # Create output directory
    if out_dir:
        envelope_dir = out_dir
    else:
        envelope_dir = shard_path.parent / f"{shard_path.name}-envelope"
    envelope_dir.mkdir(parents=True, exist_ok=True)
    (envelope_dir / "blobs").mkdir(exist_ok=True)

    # Passthrough: the shard's public integrity metadata (top-level
    # manifest.json and the sig/ directory) is not a secret — it is exactly
    # what lets a recipient verify the Genesis seal. It travels in the
    # envelope as plaintext blobs and is restored byte-identical on decrypt.
    passthrough: List[PassthroughFile] = []
    passthrough_sources = [manifest_path]
    sig_dir = shard_path / "sig"
    if sig_dir.is_dir():
        passthrough_sources.extend(
            sorted(p for p in sig_dir.rglob("*") if p.is_file())
        )
    for src in passthrough_sources:
        data = src.read_bytes()
        blob_hash = hashlib.sha256(data).hexdigest()
        (envelope_dir / "blobs" / blob_hash).write_bytes(data)
        passthrough.append(PassthroughFile(
            path=src.relative_to(shard_path).as_posix(),
            blob_hash=blob_hash,
        ))

    # Encrypt files
    partitions = []
    file_color_map = file_color_map or {}

    for color in colors:
        partition_key = result.partition_keys[color]
        aesgcm = AESGCM(partition_key)
        
        partition = Partition(
            color=color,
            classification=_get_classification(color),
            files=[],
        )
        
        # Collect files for this partition
        for root, dirs, files in os.walk(shard_path):
            rel_root = Path(root).relative_to(shard_path)
            # Skip only the top-level sig/ directory (passthrough). Compare
            # path components, not substrings: directories that merely
            # contain "sig" in their name (signals/, design/) are encrypted
            # like any other data.
            if rel_root.parts and rel_root.parts[0] == "sig":
                continue

            for filename in files:
                # Only the top-level manifest.json is passthrough
                if filename == "manifest.json" and not rel_root.parts:
                    continue

                file_path = Path(root) / filename
                rel_path = file_path.relative_to(shard_path).as_posix()
                
                # Determine color for this file
                file_color = file_color_map.get(rel_path, "Green")
                if file_color != color:
                    continue
                
                # Read and encrypt
                plaintext = file_path.read_bytes()
                plaintext_hash = hashlib.sha256(plaintext).hexdigest()
                
                nonce = secrets.token_bytes(12)
                aad = _compute_aad(
                    envelope_id, shard_id, color, rel_path,
                    plaintext_hash, topology_hash_b64
                )
                
                ciphertext = aesgcm.encrypt(nonce, plaintext, aad)
                blob_hash = hashlib.sha256(ciphertext).hexdigest()
                
                # Write blob
                (envelope_dir / "blobs" / blob_hash).write_bytes(ciphertext)
                
                partition.files.append(FileEntry(
                    path=rel_path,
                    nonce_b64=base64.b64encode(nonce).decode("ascii"),
                    blob_hash=blob_hash,
                    plaintext_hash=plaintext_hash,
                ))
        
        if partition.files:
            partitions.append(partition)
    
    # Build envelope
    envelope = ClarionEnvelope(
        envelope_id=envelope_id,
        shard_id=shard_id,
        genesis_merkle_root=merkle_root,
        kdf_salt_b64=base64.b64encode(salt).decode("ascii"),
        kdf_epoch=epoch,
        topology_hash_b64=topology_hash_b64,
        topology_hash_version=topology_hash_version,
        partitions=partitions,
        files_digest_b64=_compute_files_digest(partitions),
        created_at=datetime.now(timezone.utc).isoformat(),
        passthrough=passthrough,
    )
    
    # Write envelope.json
    (envelope_dir / "envelope.json").write_text(
        json.dumps(envelope.to_dict(), indent=2)
    )
    
    return envelope_dir, envelope


def _get_classification(color: str) -> str:
    """Get classification string for color."""
    return {
        "Green": "UNCLASSIFIED",
        "Yellow": "CUI",
        "Red": "SECRET",
        "Black": "TOP SECRET",
    }.get(color, "UNCLASSIFIED")


# ============================================================================
# DECRYPTION
# ============================================================================

class ClarionDecryptionError(Exception):
    """Error during Clarion decryption."""
    pass


def decrypt_envelope(
    envelope_path: Path,
    user_secret: bytes,
    out_dir: Optional[Path] = None,
    colors_to_decrypt: Optional[List[str]] = None,
    temp_root: Optional[str] = None,
    verify_topology: bool = True,
) -> Tuple[Path, List[str]]:
    """Decrypt Clarion envelope.
    
    Args:
        envelope_path: Path to envelope directory
        user_secret: User secret for key derivation
        out_dir: Output directory (uses temp if not provided)
        colors_to_decrypt: List of colors to decrypt (default: all)
        temp_root: Root for temp directories (for tmpfs)
        verify_topology: Verify topology hash matches shard (recommended)
    
    Returns:
        (decrypted_shard_path, decrypted_colors)
    """
    envelope_file = envelope_path / "envelope.json"
    if not envelope_file.exists():
        raise ClarionDecryptionError("Missing envelope.json")
    
    envelope_data = json.loads(envelope_file.read_text())
    version = envelope_data.get("clarion_version", "1.0")
    
    if version == "2.0":
        return _decrypt_v2(
            envelope_path, envelope_data, user_secret, out_dir,
            colors_to_decrypt, temp_root, verify_topology
        )
    elif version in ("1.0", "1.1"):
        return _decrypt_v1(
            envelope_path, envelope_data, user_secret, out_dir, temp_root
        )
    else:
        raise ClarionDecryptionError(f"Unsupported version: {version}")


def _decrypt_v2(
    envelope_path: Path,
    envelope_data: Dict[str, Any],
    user_secret: bytes,
    out_dir: Optional[Path],
    colors_to_decrypt: Optional[List[str]],
    temp_root: Optional[str],
    verify_topology: bool,
) -> Tuple[Path, List[str]]:
    """Decrypt Clarion v2.0 envelope."""
    graphkdf = _require_graphkdf()
    envelope = ClarionEnvelope.from_dict(envelope_data)
    
    # Verify files_digest
    expected_digest = envelope.files_digest_b64
    actual_digest = _compute_files_digest(envelope.partitions)
    if expected_digest and actual_digest != expected_digest:
        raise ClarionDecryptionError("Files digest mismatch - envelope corrupted")
    
    # Select hash function based on version
    if envelope.topology_hash_version == "v3":
        hash_fn = graphkdf.v3_length_prefixed
    else:
        hash_fn = graphkdf.v2_legacy_delimited
    
    # We need edges to derive keys, but we only have topology_hash in envelope
    # So we derive with the stored topology_hash directly
    salt = base64.b64decode(envelope.kdf_salt_b64)
    topology_hash = base64.b64decode(envelope.topology_hash_b64)
    
    # Derive keys for requested colors
    colors = colors_to_decrypt or [p.color for p in envelope.partitions]
    
    # Use GraphKDF with stored topology hash
    kdf_params = graphkdf.GraphKDFParams(
        user_secret=user_secret,
        salt=salt,
        epoch=envelope.kdf_epoch,
        topology_hash=topology_hash,
        domain=CLARION_DOMAIN,
    )
    
    # Create output directory
    if out_dir:
        shard_dir = out_dir
        shard_dir.mkdir(parents=True, exist_ok=True)
    else:
        shard_dir = Path(tempfile.mkdtemp(prefix="clarion_", dir=temp_root))
    
    decrypted_colors = []
    
    try:
        for partition in envelope.partitions:
            if colors_to_decrypt and partition.color not in colors_to_decrypt:
                continue
            
            partition_key = kdf_params.derive_partition_key(partition.color)
            aesgcm = AESGCM(partition_key)
            
            for entry in partition.files:
                blob_path = envelope_path / "blobs" / entry.blob_hash
                if not blob_path.exists():
                    raise ClarionDecryptionError(f"Missing blob: {entry.blob_hash}")
                
                ciphertext = blob_path.read_bytes()
                
                # Verify blob hash
                if hashlib.sha256(ciphertext).hexdigest() != entry.blob_hash:
                    raise ClarionDecryptionError(f"Blob hash mismatch: {entry.path}")
                
                nonce = base64.b64decode(entry.nonce_b64)
                
                aad = _compute_aad(
                    envelope.envelope_id,
                    envelope.shard_id,
                    partition.color,
                    entry.path,
                    entry.plaintext_hash,
                    envelope.topology_hash_b64,
                )
                
                try:
                    plaintext = aesgcm.decrypt(nonce, ciphertext, aad)
                except Exception as e:
                    raise ClarionDecryptionError(
                        f"Decryption failed for {entry.path}: {e}\n"
                        f"This may indicate topology tampering or wrong key."
                    )
                
                # Verify plaintext hash
                if entry.plaintext_hash:
                    if hashlib.sha256(plaintext).hexdigest() != entry.plaintext_hash:
                        raise ClarionDecryptionError(f"Plaintext hash mismatch: {entry.path}")
                
                # Write file
                dest = shard_dir / entry.path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(plaintext)
            
            decrypted_colors.append(partition.color)

        # Restore plaintext passthrough files (top-level manifest.json and
        # sig/) byte-identical, so the decrypted shard can be verified
        # against its Genesis seal and mounted. Envelopes written before
        # the "passthrough" field existed have no entries here and decrypt
        # exactly as before.
        for entry in envelope.passthrough:
            blob_path = envelope_path / "blobs" / entry.blob_hash
            if not blob_path.exists():
                raise ClarionDecryptionError(
                    f"Missing passthrough blob: {entry.blob_hash}"
                )
            data = blob_path.read_bytes()
            if hashlib.sha256(data).hexdigest() != entry.blob_hash:
                raise ClarionDecryptionError(
                    f"Passthrough hash mismatch: {entry.path}"
                )
            dest = shard_dir / entry.path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)

        # Verify topology if requested
        claims_jsonl = shard_dir / "graph" / "claims.jsonl"
        claims_parquet = shard_dir / "graph" / "claims.parquet"
        if verify_topology and (claims_jsonl.exists() or claims_parquet.exists()):
            if claims_jsonl.exists():
                edges = extract_edges_from_jsonl(claims_jsonl)
            else:
                edges = extract_edges_from_parquet(claims_parquet)
            actual_topo = hash_fn(edges)
            if actual_topo != topology_hash:
                raise ClarionDecryptionError(
                    "Topology hash mismatch after decryption!\n"
                    "The graph structure doesn't match the envelope's topology.\n"
                    "This indicates tampering or corruption."
                )
        
        return shard_dir, decrypted_colors
        
    except Exception:
        if not out_dir and shard_dir.exists():
            shutil.rmtree(shard_dir, ignore_errors=True)
        raise


def _decrypt_v1(
    envelope_path: Path,
    envelope_data: Dict[str, Any],
    user_secret: bytes,
    out_dir: Optional[Path],
    temp_root: Optional[str],
) -> Tuple[Path, List[str]]:
    """Decrypt Clarion v1.x envelope (backward compatibility).
    
    v1.x uses simple HKDF without topology binding.
    """
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    
    envelope_id = envelope_data["envelope_id"]
    shard_id = envelope_data["shard_id"]
    files = envelope_data.get("files", [])
    
    kdf_spec = envelope_data.get("kdf", {})
    salt = base64.b64decode(kdf_spec.get("salt_b64", ""))
    info = kdf_spec.get("info", "axm-clarion-v1").encode("utf-8")
    
    hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=info)
    key = hkdf.derive(user_secret)
    aesgcm = AESGCM(key)
    
    if out_dir:
        shard_dir = out_dir
        shard_dir.mkdir(parents=True, exist_ok=True)
    else:
        shard_dir = Path(tempfile.mkdtemp(prefix="clarion_", dir=temp_root))
    
    try:
        for entry in files:
            rel_path = entry["path"]
            nonce = base64.b64decode(entry["nonce_b64"])
            blob_hash = entry["blob_hash"]
            plaintext_hash = entry.get("plaintext_hash", "")
            
            blob_path = envelope_path / "blobs" / blob_hash
            ciphertext = blob_path.read_bytes()
            
            if hashlib.sha256(ciphertext).hexdigest() != blob_hash:
                raise ClarionDecryptionError(f"Blob hash mismatch: {rel_path}")
            
            # v1.1 uses plaintext_hash, v1.0 uses blob_hash
            if plaintext_hash:
                aad = json.dumps({
                    "envelope_id": envelope_id,
                    "shard_id": shard_id,
                    "path": rel_path,
                    "plaintext_hash": plaintext_hash,
                }, sort_keys=True, separators=(",", ":")).encode("utf-8")
            else:
                aad = json.dumps({
                    "envelope_id": envelope_id,
                    "shard_id": shard_id,
                    "path": rel_path,
                    "blob_hash": blob_hash,
                }, sort_keys=True, separators=(",", ":")).encode("utf-8")
            
            plaintext = aesgcm.decrypt(nonce, ciphertext, aad)
            
            dest = shard_dir / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(plaintext)
        
        return shard_dir, ["Default"]
        
    except Exception:
        if not out_dir and shard_dir.exists():
            shutil.rmtree(shard_dir, ignore_errors=True)
        raise
