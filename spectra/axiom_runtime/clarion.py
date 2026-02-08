import base64
import hashlib
import hmac
from dataclasses import dataclass
from typing import Optional

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except Exception:
    AESGCM = None  # type: ignore

@dataclass(frozen=True)
class EncryptedBlob:
    nonce: bytes
    ciphertext: bytes

def b64decode_padded(s: str) -> bytes:
    s = str(s)
    pad = "=" * ((4 - (len(s) % 4)) % 4)
    return base64.b64decode(s + pad)

def derive_root_secret(user_secret: bytes, salt: bytes, epoch: str) -> bytes:
    msg = salt + epoch.encode("utf-8")
    return hmac.new(user_secret, msg, hashlib.sha256).digest()

def derive_partition_key(root_secret: bytes, color: str, topo_hash: bytes) -> bytes:
    msg = color.encode("utf-8") + b"|" + topo_hash
    return hmac.new(root_secret, msg, hashlib.sha256).digest()

def decrypt_bytes(key: bytes, blob: EncryptedBlob, *, aad: Optional[bytes] = None) -> bytes:
    if AESGCM is None:
        raise ImportError("cryptography is required for Clarion decrypt")
    k = key[:32]
    aesgcm = AESGCM(k)
    return aesgcm.decrypt(blob.nonce, blob.ciphertext, aad)
