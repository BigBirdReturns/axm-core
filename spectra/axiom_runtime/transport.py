import base64
import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

# Optional Clarion v2 (GraphKDF) support
try:
    from clarion.core import decrypt_envelope as clarion_v2_decrypt_envelope  # type: ignore
except Exception:
    clarion_v2_decrypt_envelope = None  # type: ignore
from typing import Any, Dict, List, Optional

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


class ClarionError(Exception):
    pass


@dataclass(frozen=True)
class ClarionFileEntry:
    path: str
    blob_hash: str  # sha256 hex of ciphertext blob bytes
    nonce_b64: str


class TransportAdapter:
    """Clarion transport adapter.

    Clarion is transport only. It must decrypt to a byte-perfect Genesis shard directory.

    Supported versions:
      - v1.0: AAD uses blob_hash (has circular dependency bug)
      - v1.1: AAD uses plaintext_hash (fixed)

    Clarion Envelope format:
      - envelope.json (plaintext)
      - blobs/<blob_hash> files, where blob_hash is sha256 hex of ciphertext bytes
      - AES-256-GCM for encryption
      - HKDF-SHA256 for key derivation
      - Optional: files_digest_sha256_b64 (digest of canonicalized file table)

    Integrity invariants enforced here:
      1) Strict base64 decoding
      2) blob_hash must match ciphertext bytes read from disk
      3) files_digest, when present, must match canonicalized file table bytes
      4) v1.0: AAD binds to (envelope_id, shard_id, path, blob_hash)
         v1.1: AAD binds to (envelope_id, shard_id, path, plaintext_hash)

    Notes:
      - AESGCM authenticates ciphertext, AAD prevents blob swapping
      - v1.1 is preferred; v1.0 has a circular dependency in AAD computation
    """

    @staticmethod
    def detect_format(path: str) -> str:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Path not found: {path}")

        if p.is_dir() and (p / "envelope.json").exists() and (p / "blobs").is_dir():
            return "clarion"

        # Advisory only: Genesis acceptance requires axm-verify.
        if p.is_dir() and (p / "manifest.json").exists() and (p / "graph").is_dir():
            return "genesis"

        return "unknown"

    @staticmethod
    def _b64d(s: str, *, field: str) -> bytes:
        try:
            return base64.b64decode(s, validate=True)
        except Exception as e:
            raise ClarionError(f"Invalid base64 for {field}: {e}")

    @staticmethod
    def _canonical_files_bytes(files: List[Dict[str, Any]]) -> bytes:
        # Sort by path and dump with canonical JSON settings.
        # Include plaintext_hash when present (v1.1 format)
        normalized = []
        for f in files:
            entry = {
                "path": str(f["path"]),
                "blob_hash": str(f["blob_hash"]),
                "nonce_b64": str(f["nonce_b64"]),
            }
            # v1.1 includes plaintext_hash in digest computation
            if f.get("plaintext_hash"):
                entry["plaintext_hash"] = str(f["plaintext_hash"])
            normalized.append(entry)
        
        normalized.sort(key=lambda x: x["path"].encode("utf-8"))
        return json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    @staticmethod
    def decrypt_envelope(envelope_path: str, secret_b64: str, *, temp_root: Optional[str] = None) -> Path:
        env_path = Path(envelope_path)
        manifest_file = env_path / "envelope.json"
        if not manifest_file.exists():
            raise ClarionError("Missing envelope.json")

        try:
            envelope = json.loads(manifest_file.read_text(encoding="utf-8"))
        except Exception as e:
            raise ClarionError(f"Invalid envelope.json: {e}")

        clarion_version = envelope.get("clarion_version")
        if clarion_version == "2.0":
            if clarion_v2_decrypt_envelope is None:
                raise ClarionError("Clarion v2.0 requires the clarion package")
            user_secret = TransportAdapter._b64d(secret_b64, field="secret_b64")
            out_path, _colors = clarion_v2_decrypt_envelope(Path(envelope_path), user_secret, out_dir=None, temp_root=temp_root)
            return out_path
        if clarion_version not in ("1.0", "1.1"):
            raise ClarionError(f"Unsupported Clarion version: {clarion_version}")
        if envelope.get("encryption_algo") != "AES-256-GCM":
            raise ClarionError(f"Unsupported encryption algo: {envelope.get('encryption_algo')}")

        envelope_id = envelope.get("envelope_id")
        shard_id = envelope.get("shard_id")
        if not envelope_id or not shard_id:
            raise ClarionError("envelope_id and shard_id are required")

        # KDF
        kdf_spec = envelope.get("kdf") or {}
        if kdf_spec.get("name") != "HKDF-SHA256":
            raise ClarionError("Unsupported KDF")

        user_secret = TransportAdapter._b64d(secret_b64, field="secret_b64")
        salt = TransportAdapter._b64d(str(kdf_spec.get("salt_b64", "")), field="kdf.salt_b64")
        info = str(kdf_spec.get("info", "")).encode("utf-8")

        hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=info)
        key = hkdf.derive(user_secret)
        aesgcm = AESGCM(key)

        files = envelope.get("files")
        if not isinstance(files, list) or not files:
            raise ClarionError("Envelope files list is missing or empty")

        # Optional global digest check.
        if envelope.get("files_digest_sha256_b64"):
            expected = TransportAdapter._b64d(str(envelope["files_digest_sha256_b64"]), field="files_digest_sha256_b64")
            canonical = TransportAdapter._canonical_files_bytes(files)
            actual = hashlib.sha256(canonical).digest()
            if actual != expected:
                raise ClarionError("Envelope files_digest mismatch")

        out_dir = Path(tempfile.mkdtemp(prefix="clarion_decrypt_", dir=temp_root))

        try:
            for entry_raw in files:
                if not isinstance(entry_raw, dict):
                    raise ClarionError("Malformed file entry")

                rel_path = str(entry_raw.get("path", ""))
                blob_hash = str(entry_raw.get("blob_hash", ""))
                nonce_b64 = str(entry_raw.get("nonce_b64", ""))

                if not rel_path or not blob_hash or not nonce_b64:
                    raise ClarionError(f"Malformed file entry: {entry_raw}")

                # Prevent path traversal
                rel = Path(rel_path)
                if rel.is_absolute() or ".." in rel.parts:
                    raise ClarionError(f"Invalid rel path: {rel_path}")

                blob_path = env_path / "blobs" / blob_hash
                if not blob_path.exists():
                    raise ClarionError(f"Missing blob: {blob_hash}")

                ciphertext = blob_path.read_bytes()

                # Validate blob_hash against ciphertext bytes
                computed = hashlib.sha256(ciphertext).hexdigest()
                if computed != blob_hash:
                    raise ClarionError(f"Blob hash mismatch for {rel_path}")

                nonce = TransportAdapter._b64d(nonce_b64, field=f"files[{rel_path}].nonce_b64")

                # Clarion v1.1 uses plaintext_hash in AAD, v1.0 used blob_hash
                plaintext_hash = entry_raw.get("plaintext_hash", "")
                
                if plaintext_hash:
                    # v1.1 format (correct)
                    aad = json.dumps(
                        {
                            "envelope_id": envelope_id,
                            "shard_id": shard_id,
                            "path": rel_path,
                            "plaintext_hash": plaintext_hash,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ).encode("utf-8")
                else:
                    # v1.0 format (has circular dependency bug, may fail)
                    aad = json.dumps(
                        {
                            "envelope_id": envelope_id,
                            "shard_id": shard_id,
                            "path": rel_path,
                            "blob_hash": blob_hash,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ).encode("utf-8")

                try:
                    plaintext = aesgcm.decrypt(nonce, ciphertext, aad)
                except Exception:
                    raise ClarionError(f"Decryption failed for {rel_path} (bad key or integrity error)")
                
                # Verify plaintext_hash if present (defense in depth)
                if plaintext_hash:
                    actual = hashlib.sha256(plaintext).hexdigest()
                    if actual != plaintext_hash:
                        raise ClarionError(f"Plaintext hash mismatch for {rel_path}")

                dest = out_dir / rel_path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(plaintext)

        except Exception:
            shutil.rmtree(out_dir, ignore_errors=True)
            raise

        return out_dir
