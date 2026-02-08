"""
AXM Forge v5.0 CLI - Genesis-Compliant

Commands:
- extract: Extract candidates from document (outputs candidates.jsonl)
- build: Full pipeline (extract → Genesis compile → verify → optionally encrypt)
- verify: Verify a shard using axm-verify
- mount: Mount a shard (delegates to Spectra)

THE CONTRACT:
- Forge extracts → candidates.jsonl + source.txt
- Genesis (axm-build) compiles → verified shard
- axm-verify is THE authority
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from axm_forge.ingestion.universal import ingest_paths
from axm_forge.chunking.simple import chunk_text
from axm_forge.models.claims import ClaimGenContext
from axm_forge.extraction.registry import run_generators

# Import tiers to register generators
from axm_forge.extraction.tiers import tier1_regex  # noqa:F401
from axm_forge.extraction.tiers import tier3_llm    # noqa:F401

from axm_forge.coloring.policy import load_policy, classify_text
from axm_forge.emission.genesis_emission import (
    EmissionConfig,
    emit_genesis_shard,
    write_candidates_jsonl,
    write_source_txt,
    Candidate,
    call_axm_verify,
)


def _collect_input_files(input_path: Path) -> List[Path]:
    """Collect input files from path."""
    if input_path.is_file():
        return [input_path]
    files: List[Path] = []
    for p in input_path.rglob("*"):
        if p.is_file() and p.suffix.lower() in (".txt", ".md", ".pdf", ".docx"):
            files.append(p)
    return sorted(files)


def cmd_extract(args: argparse.Namespace) -> int:
    """Extract candidates from document without building shard.
    
    Outputs:
    - source.txt (normalized source text)
    - candidates.jsonl (extracted claims for Genesis)
    """
    in_path = Path(args.input).resolve()
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    
    files = _collect_input_files(in_path)
    if not files:
        print(f"No files found under {in_path}", file=sys.stderr)
        return 1
    
    # Resolve API key
    api_key: Optional[str] = args.llm_key
    if not api_key and args.enable_llm:
        env_map = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
        api_key = os.getenv(env_map.get(args.llm_provider, ""))
    
    docs = ingest_paths(files)
    
    enabled = ["tier1_regex"]
    if args.enable_llm:
        enabled.append("tier3_llm")
    
    for doc in docs:
        print(f"Processing: {doc.path}")
        
        chunks = chunk_text(doc.doc_id, doc.extracted_text, str(doc.path))
        ctx = ClaimGenContext(
            doc_id=doc.doc_id,
            extracted_text=doc.extracted_text,
            chunks=chunks,
            entities={},
            metrics={
                "enable_llm": bool(args.enable_llm),
                "llm_backend": args.llm_provider,
                "llm_model": args.llm_model,
                "llm_api_key": api_key,
            },
        )
        
        claims = run_generators(ctx, enabled)
        
        # Convert to candidates
        candidates = []
        for claim in claims:
            evidence = ""
            if claim.source_spans:
                evidence = claim.source_spans[0].snippet
            
            if evidence and claim.predicate:
                candidates.append(Candidate(
                    subject=claim.entity_id or claim.subject_label or "",
                    predicate=claim.predicate,
                    object=claim.value,
                    object_type="literal:string",
                    evidence=evidence,
                    tier=claim.tier,
                ))
        
        # Write outputs
        doc_out = out_dir / doc.doc_id
        doc_out.mkdir(parents=True, exist_ok=True)
        
        source_path = doc_out / "source.txt"
        candidates_path = doc_out / "candidates.jsonl"
        
        write_source_txt(source_path, doc.extracted_text)
        write_candidates_jsonl(candidates_path, candidates)
        
        print(f"  Extracted {len(candidates)} candidates")
        print(f"  Source: {source_path}")
        print(f"  Candidates: {candidates_path}")
    
    print("\nTo build shards, run:")
    print(f"  axm-build compile <source.txt> --candidates <candidates.jsonl> --out <shard_dir> --namespace <ns> --created-at $(date -Iseconds)")
    
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    """Full pipeline: extract → compile → verify → optionally encrypt."""
    
    # Check for axm-build availability
    try:
        subprocess.run(["axm-build", "--help"], capture_output=True, check=True)
    except FileNotFoundError:
        print("ERROR: axm-build not found. Install axm-genesis package.", file=sys.stderr)
        print("       pip install axm-genesis", file=sys.stderr)
        return 1
    
    in_path = Path(args.input).resolve()
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    
    files = _collect_input_files(in_path)
    if not files:
        print(f"No files found under {in_path}", file=sys.stderr)
        return 1
    
    # Resolve API key
    api_key: Optional[str] = args.llm_key
    if not api_key and args.enable_llm:
        env_map = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
        api_key = os.getenv(env_map.get(args.llm_provider, ""))
    
    docs = ingest_paths(files)
    policy = load_policy(Path(args.policy).resolve()) if args.policy else load_policy(None)
    
    enabled = ["tier1_regex"]
    if args.enable_llm:
        enabled.append("tier3_llm")
    
    results = []
    
    for doc in docs:
        print(f"\nProcessing: {doc.path}")
        
        chunks = chunk_text(doc.doc_id, doc.extracted_text, str(doc.path))
        ctx = ClaimGenContext(
            doc_id=doc.doc_id,
            extracted_text=doc.extracted_text,
            chunks=chunks,
            entities={},
            metrics={
                "enable_llm": bool(args.enable_llm),
                "llm_backend": args.llm_provider,
                "llm_model": args.llm_model,
                "llm_api_key": api_key,
            },
        )
        
        claims = run_generators(ctx, enabled)
        print(f"  Extracted {len(claims)} claims")
        
        if not claims:
            print("  WARNING: No claims extracted, skipping")
            continue
        
        # Emit Genesis shard
        config = EmissionConfig(
            namespace=args.namespace,
            publisher_id=args.publisher_id,
            publisher_name=args.publisher_name,
            private_key_hex=args.signing_key,
            encrypt=args.encrypt,
            user_secret_b64=args.secret,
        )
        
        result = emit_genesis_shard(
            source_text=doc.extracted_text,
            claims=claims,
            out_dir=out_dir,
            doc_id=doc.doc_id,
            config=config,
        )
        
        if result.success:
            print(f"  Shard: {result.shard_path}")
            print(f"  Shard ID: {result.shard_id}")
            if result.envelope_path:
                print(f"  Envelope: {result.envelope_path}")
                print(f"  Secret: {result.secret_b64}")
            results.append(result)
        else:
            print(f"  ERROR: {result.message}", file=sys.stderr)
    
    # Summary
    print(f"\n{'='*60}")
    print(f"Built {len(results)} shard(s)")
    
    if args.encrypt and results:
        secrets_file = out_dir / "secrets.json"
        secrets_data = {r.shard_id: r.secret_b64 for r in results if r.secret_b64}
        secrets_file.write_text(json.dumps(secrets_data, indent=2))
        print(f"Secrets saved to: {secrets_file}")
    
    return 0 if results else 1


def cmd_verify(args: argparse.Namespace) -> int:
    """Verify a shard using axm-verify."""
    shard_dir = Path(args.shard_dir).resolve()
    
    if not shard_dir.exists():
        print(f"Shard not found: {shard_dir}", file=sys.stderr)
        return 1
    
    trusted_key = None
    if hasattr(args, 'trusted_key') and args.trusted_key:
        trusted_key = Path(args.trusted_key).resolve()
    
    passed, result = call_axm_verify(shard_dir, trusted_key)
    
    print(json.dumps(result, indent=2))
    return 0 if passed else 1


def cmd_mount(args: argparse.Namespace) -> int:
    """Mount a shard using Spectra."""
    print("Mount command delegates to Spectra.")
    print("Start Spectra server and use:")
    print(f"  curl -X POST http://localhost:8080/mount -d '{{\"path\": \"{args.shard_dir}\"}}'")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(
        prog="axm-forge",
        description="AXM Forge v5.0 - Genesis-Compliant Knowledge Extraction",
        epilog="Forge extracts. Genesis builds. axm-verify is the law.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    
    # EXTRACT command (new - outputs candidates.jsonl)
    ext = sub.add_parser("extract", help="Extract candidates (does not build shard)")
    ext.add_argument("input", help="Input file or directory")
    ext.add_argument("--out", required=True, help="Output directory")
    ext.add_argument("--enable-llm", action="store_true", help="Enable Tier 3 LLM extraction")
    ext.add_argument("--llm-provider", default="ollama", choices=["ollama", "openai", "anthropic", "mock"])
    ext.add_argument("--llm-model", default="llama3")
    ext.add_argument("--llm-key", help="API key")
    ext.set_defaults(func=cmd_extract)
    
    # BUILD command (now uses Genesis)
    bld = sub.add_parser("build", help="Full pipeline: extract → compile → verify")
    bld.add_argument("input", help="Input file or directory")
    bld.add_argument("--out", required=True, help="Output directory")
    bld.add_argument("--namespace", required=True, help="Shard namespace (e.g., medical/protocols)")
    bld.add_argument("--publisher-id", default="@forge", help="Publisher ID")
    bld.add_argument("--publisher-name", default="AXM Forge", help="Publisher name")
    bld.add_argument("--signing-key", help="Ed25519 private key (64 hex chars)")
    bld.add_argument("--policy", help="YAML coloring policy")
    bld.add_argument("--enable-llm", action="store_true", help="Enable Tier 3 LLM extraction")
    bld.add_argument("--llm-provider", default="ollama", choices=["ollama", "openai", "anthropic", "mock"])
    bld.add_argument("--llm-model", default="llama3")
    bld.add_argument("--llm-key", help="API key")
    bld.add_argument("--encrypt", action="store_true", help="Create Clarion v1.1 envelope")
    bld.add_argument("--secret", help="Base64 secret for encryption (generated if not set)")
    bld.set_defaults(func=cmd_build)
    
    # VERIFY command
    ver = sub.add_parser("verify", help="Verify shard with axm-verify")
    ver.add_argument("shard_dir", help="Shard directory")
    ver.add_argument("--trusted-key", help="Trusted publisher key file")
    ver.set_defaults(func=cmd_verify)
    
    # MOUNT command (delegates to Spectra)
    mnt = sub.add_parser("mount", help="Mount shard (delegates to Spectra)")
    mnt.add_argument("shard_dir", help="Shard directory")
    mnt.add_argument("--secret", help="Secret for encrypted shards")
    mnt.set_defaults(func=cmd_mount)
    
    args = p.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
