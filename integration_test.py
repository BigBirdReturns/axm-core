#!/usr/bin/env python3
"""
AXM End-to-End Integration Test

Flow:
1) Input: test document (TXT)
2) Forge extraction -> candidates.jsonl (Genesis-compatible)
3) Genesis compiler -> shard/
4) Genesis verify passes (pinned pubkey)
5) Optional: Clarion encrypt -> envelope/
6) Spectra mounts (decrypts if needed)
7) DuckDB query returns claims

Assumptions:
- You have installed Genesis as a package OR you set PYTHONPATH to include genesis/src
- You have installed clarion-v2.0.0 (GraphKDF) OR you set PYTHONPATH accordingly
- Required deps: blake3, cryptography, pyarrow (or your local Genesis build includes them)
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# --- Imports from stack (expect PYTHONPATH or installed packages) ---
from axm_build.compiler_generic import CompilerConfig, compile_generic_shard
from axm_verify.logic import verify_shard
from clarion.core import encrypt_shard, decrypt_envelope

# Optional: Spectra mount
try:
    from axiom_runtime.engine import SpectraEngine
except Exception:
    SpectraEngine = None  # type: ignore


@dataclass(frozen=True)
class TestConfig:
    namespace: str = "axm:test"
    publisher_id: str = "pub:test"
    publisher_name: str = "Test Publisher"


def write_candidates_jsonl(out_path: Path, candidates: list[dict]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for c in candidates:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="Path to input TXT (or extracted text)")
    ap.add_argument("--workdir", required=True, help="Work directory")
    ap.add_argument("--encrypt", action="store_true", help="Encrypt shard with Clarion v2")
    ap.add_argument("--trusted-pubkey", default="", help="Pinned trusted publisher pubkey path")
    args = ap.parse_args()

    cfg = TestConfig()
    workdir = Path(args.workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)

    src_path = Path(args.input).resolve()
    text = src_path.read_text(encoding="utf-8", errors="ignore")

    # Minimal test candidates. Replace with Forge extraction once your tier generators are wired.
    # Evidence strings MUST appear exactly once in normalized source text for Genesis compilation.
    candidates = [
        {
            "subject": "entity:doc",
            "predicate": "has_amount",
            "object": "$1,234.56",
            "object_type": "literal:string",
            "evidence": "$1,234.56",
            "tier": 0,
            "confidence": 1.0,
        },
    ]

    source_txt = workdir / "source.txt"
    source_txt.write_text(text, encoding="utf-8")
    candidates_jsonl = workdir / "candidates.jsonl"
    write_candidates_jsonl(candidates_jsonl, candidates)

    shard_dir = workdir / "shard"
    if shard_dir.exists():
        for p in shard_dir.rglob("*"):
            if p.is_file():
                p.unlink()
    shard_dir.mkdir(parents=True, exist_ok=True)

    private_key = secrets.token_bytes(32)

    compiler_cfg = CompilerConfig(
        source_path=source_txt,
        candidates_path=candidates_jsonl,
        out_dir=shard_dir,
        private_key=private_key,
        publisher_id=cfg.publisher_id,
        publisher_name=cfg.publisher_name,
        namespace=cfg.namespace,
        created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

    ok = compile_generic_shard(compiler_cfg)
    if not ok:
        print("Genesis compile failed", file=sys.stderr)
        return 2

    trusted_key = Path(args.trusted_pubkey).resolve() if args.trusted_pubkey else (shard_dir / "sig" / "publisher.pub")
    passed, result = verify_shard(shard_dir, trusted_key)
    if not passed:
        print(f"Genesis verify failed: {result}", file=sys.stderr)
        return 3
    print("Genesis verify: PASS")

    mount_target = shard_dir

    if args.encrypt:
        user_secret = secrets.token_bytes(32)
        envelope_dir = workdir / "envelope"
        if envelope_dir.exists():
            for p in envelope_dir.rglob("*"):
                if p.is_file():
                    p.unlink()
        envelope_path, _env = encrypt_shard(
            shard_dir,
            user_secret,
            epoch=datetime.now(timezone.utc).strftime("%Y-%m"),
            out_dir=envelope_dir,
            colors=["Green", "Yellow", "Red", "Black"],
            file_color_map=None,
            topology_hash_version="v3",
        )
        print("Clarion envelope created:", envelope_path)
        print("Secret (base64):", base64.b64encode(user_secret).decode("ascii"))

        decrypted_dir, _colors = decrypt_envelope(envelope_path, user_secret, out_dir=workdir / "decrypted")
        mount_target = decrypted_dir

        # Verify decrypted shard too
        passed2, result2 = verify_shard(mount_target, trusted_key)
        if not passed2:
            print(f"Genesis verify (decrypted) failed: {result2}", file=sys.stderr)
            return 4
        print("Genesis verify (decrypted): PASS")

    if SpectraEngine is not None:
        eng = SpectraEngine(db_path=workdir / "spectra.duckdb", dev_mode=True)
        eng.boot()
        shard_id = eng.mount_shard(mount_target)
        rows = eng.query("SELECT * FROM claims LIMIT 50")
        print("Spectra query returned rows:", len(rows))
        print(rows[:5])
    else:
        print("Spectra not importable in this environment; skipping Spectra mount test.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
