"""
AXM Forge - Genesis-Compliant Emission

This module handles the Forge → Genesis handoff.

THE CONTRACT:
- Forge extracts → candidates.jsonl + source.txt
- Genesis compiles → verified shard (v1: axm-hybrid1 suite, canonical JSONL)
- axm-verify is THE authority

Genesis v1 is FROZEN. This module calls it, does not modify it.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import blake3

from axm_build.compiler_generic import CompilerConfig, compile_generic_shard
from axm_build.sign import HYBRID1_SK_LEN, hybrid1_keygen

from axm_forge.models.claims import Claim

# Clarion v2 (GraphKDF) encryption is optional and imported lazily inside
# emit_genesis_shard() only when config.encrypt is set, so this module (and
# the forge import chain) works without clarion/graphkdf installed.


# ============================================================================
# CONFIGURATION AND RESULT TYPES
# ============================================================================

@dataclass
class EmissionConfig:
    """Configuration for shard emission.

    private_key_hex: hex encoding of the 3904-byte axm-hybrid1 secret key
    blob (generate one with `axm-build keygen` or hybrid1_keygen()). When
    None, a throwaway keypair is generated per emission — such a signature
    proves integrity, never authenticity.
    """
    namespace: str = "forge/default"
    publisher_id: str = "@forge"
    publisher_name: str = "Forge Extraction Pipeline"
    private_key_hex: Optional[str] = None
    title: str = ""
    license_spdx: str = "UNLICENSED"
    encrypt: bool = False
    user_secret_b64: Optional[str] = None


@dataclass
class EmissionResult:
    """Result of shard emission."""
    success: bool
    shard_path: Optional[Path]
    envelope_path: Optional[Path]
    secret_b64: Optional[str]
    shard_id: Optional[str]
    message: str


# ============================================================================
# CANDIDATES FORMAT (Interface between Forge and Genesis)
# ============================================================================

@dataclass
class Candidate:
    """A candidate claim for Genesis compilation.
    
    This is the interface format. Forge produces these.
    Genesis consumes them via candidates.jsonl.
    """
    subject: str
    predicate: str
    object: str
    object_type: str  # "entity" or "literal:string", "literal:integer", "literal:decimal", "literal:boolean"
    evidence: str     # EXACT substring from source
    tier: int = 0
    confidence: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_jsonl_dict(self) -> Dict[str, Any]:
        """Convert to dict for JSONL serialization."""
        d = {
            "subject": self.subject,
            "predicate": self.predicate,
            "object": self.object,
            "object_type": self.object_type,
            "evidence": self.evidence,
            "tier": self.tier,
        }
        if self.confidence != 1.0:
            d["confidence"] = self.confidence
        d.update(self.metadata)
        return d
    
    @classmethod
    def from_legacy_claim(cls, claim: Claim) -> "Candidate":
        """Convert from Forge Claim model to Genesis candidates.jsonl format."""
        evidence = ""
        if claim.source_spans:
            evidence = claim.source_spans[0].snippet

        subject = ""
        for a in claim.args:
            if a.role == "subject":
                subject = a.entity_id
                break

        return cls(
            subject=subject,
            predicate=claim.predicate,
            object=str(claim.value),
            object_type="literal:string",
            evidence=evidence,
            tier=0,
            confidence=1.0,
        )


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def write_source_txt(path: Path, text: str) -> str:
    """Write source text and return its SHA-256 hash."""
    path.write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_candidates_jsonl(path: Path, candidates: List[Candidate]) -> None:
    """Write candidates to JSONL format."""
    with path.open("w", encoding="utf-8") as f:
        for candidate in candidates:
            json.dump(candidate.to_jsonl_dict(), f, ensure_ascii=False)
            f.write("\n")


# ============================================================================
# GENESIS BUILD AND VERIFY INTEGRATION
# ============================================================================

def resolve_private_key(private_key_hex: Optional[str]) -> bytes:
    """Resolve the 3904-byte hybrid1 secret key blob for signing.

    - hex given: decode and validate the length. Key material of an
      unexpected size raises — never silently falls back to a fresh key
      (that would mint shards under an identity nobody controls).
    - None: generate a throwaway keypair for this emission.
    """
    if private_key_hex:
        try:
            blob = bytes.fromhex(private_key_hex.strip())
        except ValueError as e:
            raise ValueError(f"private_key_hex is not valid hex: {e}") from e
        if len(blob) != HYBRID1_SK_LEN:
            raise ValueError(
                f"private_key_hex must decode to a {HYBRID1_SK_LEN}-byte "
                f"axm-hybrid1 secret key blob, got {len(blob)} bytes "
                "(generate one with `axm-build keygen`)"
            )
        return blob
    _pub, secret = hybrid1_keygen()
    return secret


def derive_shard_id(shard_dir: Path) -> str:
    """Shard identity is derived, never stored (Genesis spec section 9)."""
    manifest_bytes = (Path(shard_dir) / "manifest.json").read_bytes()
    return "sh1_" + blake3.blake3(manifest_bytes).hexdigest()


def call_axm_build(
    source_path: Path,
    candidates_path: Path,
    out_dir: Path,
    *,
    namespace: str,
    publisher_id: str,
    publisher_name: str,
    private_key_hex: Optional[str] = None,
    created_at: Optional[str] = None,
    title: str = "",
    license_spdx: str = "UNLICENSED",
) -> Tuple[bool, str, Optional[Path]]:
    """Compile candidates into a signed v1 shard via the Genesis compiler.

    Uses the programmatic surface (axm_build.compiler_generic.CompilerConfig /
    compile_generic_shard) rather than the CLI: the CLI `compile` command
    stamps a fixed publisher identity, while spokes must make theirs explicit.

    Returns:
        (success, message, shard_path)
    """
    if created_at is None:
        created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        private_key = resolve_private_key(private_key_hex)
        cfg = CompilerConfig(
            source_path=Path(source_path),
            candidates_path=Path(candidates_path),
            out_dir=Path(out_dir),
            private_key=private_key,
            publisher_id=publisher_id,
            publisher_name=publisher_name,
            namespace=namespace,
            created_at=created_at,
            title=title,
            license_spdx=license_spdx,
        )
        ok = compile_generic_shard(cfg)
        if ok:
            return True, "compiled and self-verified", Path(out_dir)
        return False, "Genesis compiler rejected its own output (self-verify failed)", None
    except Exception as e:
        return False, f"axm-build error: {e}", None


def call_axm_verify(
    shard_dir: Path, 
    trusted_key: Optional[Path] = None
) -> Tuple[bool, Dict[str, Any]]:
    """Call Genesis axm-verify to validate shard.
    
    Returns:
        (passed, result_dict)
    """
    cmd = ["python", "-m", "axm_verify.cli", "shard", str(shard_dir)]
    
    # Determine trusted key path
    if trusted_key and trusted_key.exists():
        cmd.extend(["--trusted-key", str(trusted_key)])
    else:
        # Use embedded publisher key from the shard
        embedded = shard_dir / "sig" / "publisher.pub"
        if embedded.exists():
            cmd.extend(["--trusted-key", str(embedded)])
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        
        # Try to parse JSON output
        try:
            output = json.loads(result.stdout)
            passed = output.get("status") == "PASS"
            return passed, output
        except json.JSONDecodeError:
            # Fallback: check return code
            if result.returncode == 0:
                return True, {"status": "PASS", "message": "Verification succeeded"}
            else:
                return False, {
                    "status": "FAIL",
                    "error": result.stderr or result.stdout or "Unknown error",
                }
        
    except FileNotFoundError:
        return False, {"status": "ERROR", "error": "python or axm_verify module not found"}
    except subprocess.TimeoutExpired:
        return False, {"status": "ERROR", "error": "axm-verify timed out"}
    except Exception as e:
        return False, {"status": "ERROR", "error": str(e)}


# ============================================================================
# MAIN EMISSION FUNCTION
# ============================================================================

def emit_genesis_shard(
    source_text: str,
    claims: List[Claim],
    out_dir: Path,
    doc_id: str,
    config: EmissionConfig,
) -> EmissionResult:
    """Emit a Genesis-compliant shard from extracted claims.
    
    This is the main entry point for Forge → Genesis compilation.
    
    1. Convert claims to candidates
    2. Write source.txt and candidates.jsonl
    3. Call axm-build
    4. Verify with axm-verify
    5. Optionally encrypt with Clarion
    """
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    
    # Convert legacy claims to candidates
    candidates = []
    for claim in claims:
        try:
            candidate = Candidate.from_legacy_claim(claim)
            if candidate.evidence and candidate.subject and candidate.predicate:
                candidates.append(candidate)
        except Exception:
            continue
    
    if not candidates:
        return EmissionResult(
            success=False,
            shard_path=None,
            envelope_path=None,
            secret_b64=None,
            shard_id=None,
            message="No valid candidates extracted",
        )
    
    # Write to temp directory for axm-build
    with tempfile.TemporaryDirectory(prefix="forge_emit_") as tmp:
        tmp_path = Path(tmp)
        source_path = tmp_path / "source.txt"
        candidates_path = tmp_path / "candidates.jsonl"
        
        write_source_txt(source_path, source_text)
        write_candidates_jsonl(candidates_path, candidates)
        
        # Determine shard output path
        shard_name = f"{config.namespace.replace('/', '-')}-{doc_id}"
        shard_dir = out_dir / shard_name
        # RFC 3339 UTC with the Z designator — the v1 manifest format.
        created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Call the Genesis compiler
        success, message, _ = call_axm_build(
            source_path=source_path,
            candidates_path=candidates_path,
            out_dir=shard_dir,
            namespace=config.namespace,
            publisher_id=config.publisher_id,
            publisher_name=config.publisher_name,
            private_key_hex=config.private_key_hex,
            created_at=created_at,
            title=config.title or doc_id,
            license_spdx=config.license_spdx,
        )
        
        if not success:
            return EmissionResult(
                success=False,
                shard_path=None,
                envelope_path=None,
                secret_b64=None,
                shard_id=None,
                message=f"axm-build failed: {message}",
            )
    
    # Verify the shard
    pub_key = shard_dir / "sig" / "publisher.pub"
    verified, verify_result = call_axm_verify(shard_dir, pub_key)
    
    if not verified:
        return EmissionResult(
            success=False,
            shard_path=shard_dir,
            envelope_path=None,
            secret_b64=None,
            shard_id=None,
            message=f"Verification failed: {verify_result}",
        )
    
    # Shard identity is derived from the manifest bytes, never stored in it.
    shard_id = derive_shard_id(shard_dir)
    
    # Optional: encrypt with Clarion
    envelope_path = None
    secret_b64 = None
    
    if config.encrypt:
        try:
            from clarion.core import encrypt_shard as clarion_encrypt_shard
        except ImportError:
            return EmissionResult(
                success=False,
                shard_path=shard_dir,
                envelope_path=None,
                secret_b64=None,
                shard_id=shard_id,
                message=(
                    "Encryption requested but clarion package not available "
                    "(install clarion[kdf] for GraphKDF support)"
                ),
            )

        if config.user_secret_b64:
            secret_bytes = base64.b64decode(config.user_secret_b64)
        else:
            secret_bytes = secrets.token_bytes(32)

        epoch = datetime.now(timezone.utc).strftime("%Y-%m")
        envelope_path, _ = clarion_encrypt_shard(
            shard_dir,
            secret_bytes,
            epoch=epoch,
            out_dir=out_dir / f"{shard_id}.clarion",
            colors=["Green", "Yellow", "Red", "Black"],
            file_color_map=None,
            topology_hash_version="v3",
        )
        secret_b64 = base64.b64encode(secret_bytes).decode("ascii")
    
    return EmissionResult(
        success=True,
        shard_path=shard_dir,
        envelope_path=envelope_path,
        secret_b64=secret_b64,
        shard_id=shard_id,
        message="Shard created and verified",
    )
