"""
Clarion v2.0 - Topology-Bound Cryptographic Transport

Clarion encrypts AXM Genesis shards with keys derived from graph topology.
Change the graph structure, keys change, decryption fails.

Basic usage:
    >>> from clarion import encrypt_shard, decrypt_envelope
    >>> 
    >>> # Encrypt a Genesis shard (returns the envelope dir and object)
    >>> envelope_path, envelope = encrypt_shard(
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

The shard's public integrity metadata (top-level manifest.json and the sig/
directory) travels in the envelope as plaintext "passthrough" blobs and is
restored byte-identical on decrypt, so the decrypted shard can be verified
against its Genesis seal and mounted.
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

# Re-export Edge from graphkdf for convenience. graphkdf is an optional
# dependency (install clarion[kdf]); resolve it lazily so that bare
# `import clarion` works without it.
def __getattr__(name):
    if name == "Edge":
        from .core import _require_graphkdf
        return _require_graphkdf().Edge
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

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
