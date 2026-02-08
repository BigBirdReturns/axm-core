"""
Clarion v2.0 - Topology-Bound Cryptographic Transport

Clarion encrypts AXM Genesis shards with keys derived from graph topology.
Change the graph structure, keys change, decryption fails.

Basic usage:
    >>> from clarion import encrypt_shard, decrypt_envelope
    >>> 
    >>> # Encrypt a Genesis shard
    >>> envelope = encrypt_shard(
    ...     shard_path=Path("./my-shard"),
    ...     user_secret=secret,
    ...     epoch="epoch_001",
    ...     colors=["Green", "Red"],
    ... )
    >>> 
    >>> # Decrypt an envelope
    >>> shard_path, colors = decrypt_envelope(
    ...     envelope_path=Path("./my-envelope"),
    ...     user_secret=secret,
    ... )

Clarion uses GraphKDF for key derivation with domain=b"axm-clarion".
"""

__version__ = "2.0.0"

from .core import (
    # Encryption/Decryption
    encrypt_shard,
    decrypt_envelope,
    ClarionDecryptionError,
    
    # Envelope types
    ClarionEnvelope,
    Partition,
    FileEntry,
    
    # Color model
    PartitionColor,
    DEFAULT_COLORS,
    
    # Edge extraction (for manual topology computation)
    extract_edges_from_parquet,
    extract_edges_from_claims,
)

# Re-export Edge from graphkdf for convenience
from graphkdf import Edge

__all__ = [
    # Primary API
    "encrypt_shard",
    "decrypt_envelope",
    "ClarionDecryptionError",
    
    # Types
    "ClarionEnvelope",
    "Partition",
    "FileEntry",
    "PartitionColor",
    "DEFAULT_COLORS",
    "Edge",
    
    # Utilities
    "extract_edges_from_parquet",
    "extract_edges_from_claims",
]
