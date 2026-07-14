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
- You have installed axm-genesis v1 (the commit pinned in pyproject.toml)
  with an ML-DSA-44 backend (extras: [mldsa] for liboqs, [mldsa-compat]
  for dilithium-py)
- You have installed clarion-v2.0.0 (GraphKDF) OR you set PYTHONPATH accordingly
- Required deps: blake3, cryptography
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# --- Imports from stack (expect PYTHONPATH or installed packages) ---
from axm_build.compiler_generic import CompilerConfig, compile_generic_shard
from axm_build.sign import hybrid1_keygen
from axm_verify.logic import verify_shard

# Clarion is optional: it is a separate install (`pip install -e ./clarion`)
# and its encryption requires the graphkdf package, which is not published
# on PyPI. The --encrypt leg is skipped cleanly when either is missing.

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


def forge_extract_candidates(input_path: Path) -> tuple[str, list[dict]]:
    """Run the real Forge tier-1 extraction on the input document.

    Returns (source_text, candidates) where source_text is Forge's extracted
    text — the exact bytes the candidates' evidence spans were found in, so
    it MUST be what gets written as the shard's source.txt.
    """
    from axm_forge.ingestion.universal import ingest_paths
    from axm_forge.chunking.simple import chunk_text
    from axm_forge.models.claims import ClaimGenContext
    from axm_forge.extraction.registry import run_generators
    from axm_forge.extraction.tiers import tier1_regex  # noqa: F401 (registers the generator)
    from axm_forge.emission.genesis_emission import Candidate

    docs = ingest_paths([input_path])
    if not docs:
        raise SystemExit(f"Forge could not ingest {input_path}")
    doc = docs[0]

    chunks = chunk_text(doc.doc_id, doc.extracted_text, str(doc.path))
    ctx = ClaimGenContext(
        doc_id=doc.doc_id,
        extracted_text=doc.extracted_text,
        chunks=chunks,
        entities={},
        metrics={},
    )
    claims = run_generators(ctx, ["tier1_regex"])

    candidates: list[dict] = []
    for claim in claims:
        candidate = Candidate.from_legacy_claim(claim)
        if candidate.evidence and candidate.subject and candidate.predicate:
            candidates.append(candidate.to_jsonl_dict())
    return doc.extracted_text, candidates


def fallback_candidate(text: str) -> dict | None:
    """Derive one candidate from the source itself when extraction finds none.

    Uses the first non-empty line that occurs exactly once, so the Genesis
    compiler's strict evidence-span check is satisfied by construction.
    """
    for line in text.splitlines():
        line = line.strip()
        if len(line) >= 10 and text.count(line) == 1:
            return {
                "subject": "entity:doc",
                "predicate": "has_excerpt",
                "object": line,
                "object_type": "literal:string",
                "evidence": line,
                "tier": 0,
                "confidence": 1.0,
            }
    return None


def preflight_evidence(text: str, candidates: list[dict]) -> tuple[list[dict], list[str]]:
    """Split candidates into compilable and doomed, with reasons.

    Mirrors the Genesis compiler's strict rule: evidence must appear exactly
    once in the source bytes. The compiler silently drops not-found evidence
    and hard-fails on ambiguity; checking here turns a bare 'compile failed'
    into an actionable report.
    """
    ok: list[dict] = []
    problems: list[str] = []
    for c in candidates:
        evidence = str(c.get("evidence", ""))
        n = text.count(evidence) if evidence else 0
        if not evidence:
            problems.append(f"candidate {c.get('predicate')!r}: empty evidence")
        elif n == 0:
            problems.append(
                f"candidate {c.get('predicate')!r}: evidence {evidence[:60]!r} not found in source"
            )
        elif n > 1:
            problems.append(
                f"candidate {c.get('predicate')!r}: evidence {evidence[:60]!r} ambiguous ({n} matches)"
            )
        else:
            ok.append(c)
    return ok, problems


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

    # Real Forge tier-1 extraction (keyless, deterministic). The extracted
    # text — not the raw file bytes — is the source the evidence spans were
    # found in, so it is what the shard seals.
    text, candidates = forge_extract_candidates(src_path)
    print(f"Forge extraction: {len(candidates)} candidate(s)")

    candidates, problems = preflight_evidence(text, candidates)
    for p in problems:
        print(f"  dropped: {p}", file=sys.stderr)

    if not candidates:
        fb = fallback_candidate(text)
        if fb is None:
            print(
                "No compilable candidates: tier-1 extraction found nothing and "
                "no unique excerpt line exists to fall back on.",
                file=sys.stderr,
            )
            return 2
        print("Extraction found no candidates; using a has_excerpt fallback derived from the source.")
        candidates = [fb]

    source_txt = workdir / "source.txt"
    source_txt.write_text(text, encoding="utf-8")
    candidates_jsonl = workdir / "candidates.jsonl"
    write_candidates_jsonl(candidates_jsonl, candidates)

    # A fresh directory every run: the Genesis compiler refuses to wipe a
    # non-empty out_dir that is not a previously compiled shard, and a
    # half-written tree from an earlier failure trips that guard.
    shard_dir = workdir / "shard"
    if shard_dir.exists():
        shutil.rmtree(shard_dir)
    shard_dir.mkdir(parents=True, exist_ok=True)

    # Throwaway axm-hybrid1 keypair (Ed25519 || ML-DSA-44). The secret is
    # the 3904-byte blob the v1 compiler requires; the public key is 1344 B.
    _public_key, private_key = hybrid1_keygen()

    compiler_cfg = CompilerConfig(
        source_path=source_txt,
        candidates_path=candidates_jsonl,
        out_dir=shard_dir,
        private_key=private_key,
        publisher_id=cfg.publisher_id,
        publisher_name=cfg.publisher_name,
        namespace=cfg.namespace,
        created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        title="AXM Integration Test Shard",
        license_spdx="CC0-1.0",
    )

    ok = compile_generic_shard(compiler_cfg)
    if not ok:
        print(
            "Genesis compile failed: the compiler produced no claim rows from "
            f"{len(candidates)} candidate(s). Preflight passed, so this is a "
            "compiler-side rejection — check candidates.jsonl in the workdir.",
            file=sys.stderr,
        )
        return 2

    trusted_key = Path(args.trusted_pubkey).resolve() if args.trusted_pubkey else (shard_dir / "sig" / "publisher.pub")
    result = verify_shard(shard_dir, trusted_key)
    if result["status"] != "PASS":
        print(f"Genesis verify failed: {result['errors']}", file=sys.stderr)
        return 3
    print("Genesis verify: PASS")

    mount_target = shard_dir

    if args.encrypt:
        try:
            from clarion.core import encrypt_shard, decrypt_envelope
        except ImportError as exc:
            print(
                "Clarion leg SKIPPED: clarion (or its graphkdf dependency) is not "
                f"installed ({exc}).\n"
                "  Install clarion with `pip install -e ./clarion`; note its "
                "encryption additionally requires the unpublished graphkdf package.\n"
                "  Continuing with the unencrypted shard.",
            )
            args.encrypt = False

    if args.encrypt:
        user_secret = secrets.token_bytes(32)
        envelope_dir = workdir / "envelope"
        if envelope_dir.exists():
            shutil.rmtree(envelope_dir)
        try:
            # clarion.core.encrypt_shard raises ImportError at call time when
            # graphkdf is absent, so guard the call as well as the import.
            envelope_path, _env = encrypt_shard(
                shard_dir,
                user_secret,
                epoch=datetime.now(timezone.utc).strftime("%Y-%m"),
                out_dir=envelope_dir,
                colors=["Green", "Yellow", "Red", "Black"],
                file_color_map=None,
                topology_hash_version="v3",
            )
        except ImportError as exc:
            print(
                "Clarion leg SKIPPED: encryption unavailable "
                f"(graphkdf not installed: {exc}). Continuing with the "
                "unencrypted shard.",
            )
        else:
            print("Clarion envelope created:", envelope_path)
            print("Secret (base64):", base64.b64encode(user_secret).decode("ascii"))

            decrypted_dir, _colors = decrypt_envelope(envelope_path, user_secret, out_dir=workdir / "decrypted")
            mount_target = decrypted_dir

            # Verify decrypted shard too
            result2 = verify_shard(mount_target, trusted_key)
            if result2["status"] != "PASS":
                print(f"Genesis verify (decrypted) failed: {result2['errors']}", file=sys.stderr)
                return 4
            print("Genesis verify (decrypted): PASS")

    if SpectraEngine is not None:
        # SpectraEngine requires SPECTRA_SYSTEM_KEY in production; for this
        # integration test we opt into the documented dev override instead.
        os.environ.setdefault("SPECTRA_DEV_MODE", "1")
        eng = SpectraEngine(
            db_path=str(workdir / "spectra.db"),
            audit_path=str(workdir / "spectra_audit.jsonl"),
            cache_path=str(workdir / "spectra_cache.jsonl"),
        )
        eng.boot()
        spec = eng.mount_shard(str(mount_target))
        print("Spectra mounted shard:", spec.shard_id)
        result_json = eng.query_json("SELECT * FROM claims LIMIT 50")
        rows = result_json["rows"]
        print("Spectra query returned rows:", len(rows))
        print(rows[:5])
    else:
        print("Spectra not importable in this environment; skipping Spectra mount test.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
