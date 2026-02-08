#!/usr/bin/env python3
"""
AXM Nodal Flow: Article in, signed shard out.

Usage:
    python nodal_run.py "Tranexamic acid"
    python nodal_run.py "https://en.wikipedia.org/wiki/Aspirin"
    python nodal_run.py --source my_document.txt --out-dir out/my_doc

Pipeline:
    Fetch -> Segment -> Extract (Ollama) -> Bind -> Validate -> Compile (Genesis) -> Verify -> Shard

Output: out/<slug>/shard/ containing a signed, verifiable AXM Genesis shard.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup: make the monorepo importable
# ---------------------------------------------------------------------------
_root = Path(__file__).resolve().parent
sys.path.insert(0, str(_root / "genesis" / "src"))
sys.path.insert(0, str(_root / "forge"))
sys.path.insert(0, str(_root))


def log(msg: str, level: str = "info") -> None:
    colors = {"info": "37", "ok": "32", "warn": "33", "err": "31", "stage": "36", "dim": "90"}
    code = colors.get(level, "37")
    print(f"\033[{code}m{msg}\033[0m")


# ---------------------------------------------------------------------------
# Wikipedia Fetcher
# ---------------------------------------------------------------------------

def fetch_wikipedia(title_or_url: str) -> str:
    if title_or_url.startswith("http"):
        parsed = urllib.parse.urlparse(title_or_url)
        if "/wiki/" in parsed.path:
            title = urllib.parse.unquote(parsed.path.split("/wiki/")[-1])
        else:
            title = title_or_url
    else:
        title = title_or_url.replace(" ", "_")

    api_url = (
        f"https://en.wikipedia.org/w/api.php?"
        f"action=query&titles={urllib.parse.quote(title)}"
        f"&prop=extracts&explaintext=1&format=json"
    )
    req = urllib.request.Request(
        api_url,
        headers={"User-Agent": "AXM-Forge/0.1 (research; contact@sandhu.consulting)"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    pages = data.get("query", {}).get("pages", {})
    for page_id, page in pages.items():
        if page_id == "-1":
            raise ValueError(f"Wikipedia article not found: {title}")
        text = page.get("extract", "")
        if text:
            return text.strip()

    raise ValueError(f"No text returned for: {title}")


def slug_from_input(title_or_url: str) -> str:
    if title_or_url.startswith("http"):
        m = re.search(r"wiki/(.+?)(?:\?|#|$)", title_or_url)
        if m:
            return m.group(1).replace("/", "_")[:80]
    return re.sub(r"[^a-zA-Z0-9_-]", "_", title_or_url)[:80]


# ---------------------------------------------------------------------------
# Review HTML Generator
# ---------------------------------------------------------------------------

def generate_review_html(
    source_path: Path,
    sentences_path: Path,
    candidates_path: Path,
    out_html: Path,
) -> None:
    from axm_forge.extraction.schemas import read_jsonl

    source_text = source_path.read_text(encoding="utf-8")
    sentences = read_jsonl(sentences_path)
    candidates = read_jsonl(candidates_path)

    def esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    parts = ["""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>AXM Claim Review</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,system-ui,sans-serif;background:#0d1117;color:#c9d1d9;padding:24px;max-width:960px;margin:0 auto}
h1{font-size:20px;color:#58a6ff;margin-bottom:4px;font-family:monospace}
.sub{color:#8b949e;font-size:13px;margin-bottom:24px}
.stats{display:flex;gap:16px;margin-bottom:24px}
.stat{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:12px 16px;flex:1}
.sv{font-size:24px;font-weight:700;color:#58a6ff;font-family:monospace}
.sl{font-size:11px;color:#8b949e;text-transform:uppercase;letter-spacing:.5px;margin-top:2px}
.c{background:#161b22;border:1px solid #30363d;border-radius:8px;margin-bottom:12px;overflow:hidden}
.ch{padding:12px 16px;border-bottom:1px solid #30363d;display:flex;justify-content:space-between;align-items:center}
.ci{font-family:monospace;font-size:12px;color:#8b949e}
.cs{font-size:11px;color:#8b949e;font-family:monospace}
.ct{padding:12px 16px;font-size:14px;line-height:1.5;color:#e6edf3}
.ev{padding:12px 16px;background:#0d1117;border-top:1px solid #30363d}
.el{font-size:11px;color:#8b949e;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}
.et{font-size:13px;line-height:1.6;color:#c9d1d9}
.bi{font-family:monospace;font-size:11px;color:#484f58;margin-top:6px}
.fb{margin-bottom:16px}
.fb input{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:8px 12px;color:#c9d1d9;font-size:14px;width:100%;outline:none}
.fb input:focus{border-color:#58a6ff}
.hidden{display:none}
</style></head><body>
"""]

    title = source_path.stem
    parts.append(f'<h1>AXM Claim Review</h1>')
    parts.append(f'<div class="sub">{esc(title)} &middot; {len(candidates)} claims from {len(sentences)} sentences</div>')

    total_ev = sum(c.get("byte_end", 0) - c.get("byte_start", 0) for c in candidates)
    parts.append(f"""<div class="stats">
<div class="stat"><div class="sv">{len(candidates)}</div><div class="sl">Claims</div></div>
<div class="stat"><div class="sv">{len(sentences)}</div><div class="sl">Sentences</div></div>
<div class="stat"><div class="sv">{total_ev:,}</div><div class="sl">Evidence Bytes</div></div>
</div>""")

    parts.append('<div class="fb"><input type="text" id="q" placeholder="Filter claims..." oninput="f()"></div>')

    for i, c in enumerate(candidates):
        meta = c.get("meta", {})
        ct = esc(meta.get("claim_text", c.get("object", "")))
        subj = esc(c.get("subject", ""))
        pred = esc(c.get("predicate", ""))
        obj = esc(c.get("object", ""))
        ev = esc(c.get("evidence", ""))
        bs, be = c.get("byte_start", 0), c.get("byte_end", 0)
        sids = meta.get("sentence_ids", [])
        sid_html = " ".join(f'<span style="background:#1f2937;border:1px solid #30363d;border-radius:4px;padding:2px 6px;font-family:monospace;font-size:11px;color:#58a6ff">{s}</span>' for s in sids)

        parts.append(f"""<div class="c" data-t="{esc((ct+subj+pred+obj).lower())}">
<div class="ch"><span class="ci">#{i}</span><span class="cs">{subj} &rarr; {pred} &rarr; {obj}</span></div>
<div class="ct">{ct}</div>
<div class="ev"><div class="el">Evidence {sid_html}</div><div class="et">{ev}</div><div class="bi">bytes {bs:,}&ndash;{be:,} ({be-bs:,} bytes)</div></div>
</div>""")

    parts.append('<script>function f(){const q=document.getElementById("q").value.toLowerCase();document.querySelectorAll(".c").forEach(e=>{e.classList.toggle("hidden",q&&!e.dataset.t.includes(q))})}</script></body></html>')
    out_html.write_text("\n".join(parts), encoding="utf-8")


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def run_pipeline(
    title_or_url: str | None = None,
    source_path: Path | None = None,
    out_dir: Path = Path("out"),
    model: str | None = None,
    host: str | None = None,
    skip_stage1: bool = False,
    skip_genesis: bool = False,
    namespace: str = "generic/import",
) -> dict:
    from axm_forge.extraction.tiers.tier3_segmenter import run_segmentation
    from axm_forge.extraction.tiers.tier3_stage2 import run_stage2
    from axm_forge.extraction.schemas import read_jsonl

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    src = out_dir / "source.txt"
    sent = out_dir / "sentences.jsonl"
    raw = out_dir / "raw_claims.jsonl"
    cand = out_dir / "candidates.jsonl"
    html = out_dir / "review.html"
    shard_dir = out_dir / "shard"

    report = {"stages": {}}
    t0 = time.time()

    # -- Fetch --
    if source_path and Path(source_path).exists():
        import shutil
        if Path(source_path).resolve() != src.resolve():
            shutil.copy2(source_path, src)
        log(f"Using existing source: {src}")
    elif title_or_url:
        log(f"Fetching: {title_or_url}", "stage")
        text = fetch_wikipedia(title_or_url)
        src.write_text(text, encoding="utf-8")
        log(f"  {len(text.encode('utf-8')):,} bytes", "ok")
    else:
        raise ValueError("Provide either title_or_url or source_path")

    # -- Normalize source text --
    # Genesis compiler runs normalize_source_text() before matching evidence spans.
    # We normalize here so that segmenter byte offsets align with compiler bytes.
    # After this step, source.txt contains the canonical byte sequence that all
    # downstream stages (segmenter, binder, doctor, compiler) will reference.
    try:
        from axm_build.common import normalize_source_text
        raw_text = src.read_text(encoding="utf-8")
        norm_text = normalize_source_text(raw_text)
        if raw_text != norm_text:
            src.write_text(norm_text, encoding="utf-8")
            raw_bytes = len(raw_text.encode("utf-8"))
            norm_bytes = len(norm_text.encode("utf-8"))
            log(f"  Normalized: {raw_bytes:,} -> {norm_bytes:,} bytes "
                f"(delta {norm_bytes - raw_bytes:+d})", "dim")
        else:
            log("  Source already normalized", "dim")
    except ImportError:
        log("  WARNING: normalize_source_text not available, skipping normalization", "err")

    # -- Stage 0: Segment --
    log("\n[Stage 0] Segmenting...", "stage")
    t_seg = time.time()
    n_sent = run_segmentation(src, sent)
    log(f"  {n_sent} sentences in {time.time() - t_seg:.1f}s", "ok")

    # -- Stage 1: Extract --
    if skip_stage1:
        log("\n[Stage 1] Skipped (--skip-stage1)", "dim")
    else:
        from axm_forge.extraction.tiers.tier3_stage1 import run_stage1

        model = model or os.environ.get("AXM_OLLAMA_MODEL", "qwen2.5:7b-instruct")
        host = host or os.environ.get("AXM_OLLAMA_HOST", "http://127.0.0.1:11434")

        log(f"\n[Stage 1] Extracting claims via {model}...", "stage")
        log(f"  Safe to Ctrl+C and resume.", "dim")
        t_ext = time.time()
        s1 = run_stage1(sentences_path=sent, out_path=raw, model=model, host=host)
        log(f"  {s1.get('claims', 0)} claims in {time.time() - t_ext:.1f}s", "ok")

    # -- Stage 2: Bind --
    if not raw.exists():
        log("\n[Stage 2] Skipped (no raw_claims.jsonl)", "dim")
        return report

    log("\n[Stage 2] Binding claims to byte spans...", "stage")
    t_bind = time.time()
    s2 = run_stage2(source_path=src, sentences_path=sent,
                    raw_claims_path=raw, out_path=cand)
    log(f"  {s2.get('emitted', 0)} candidates in {time.time() - t_bind:.2f}s", "ok")

    # -- Doctor: Validate byte-exactness --
    if cand.exists():
        from scripts.doctor_tier3 import validate_candidates_against_source

        log("\n[Doctor] Validating byte-exactness...", "stage")
        vr = validate_candidates_against_source(src, cand)
        status = "PASS" if vr.ok else "FAIL"
        log(f"  {status}: {vr.validated}/{vr.emitted} validated", "ok" if vr.ok else "err")
        if not vr.ok:
            for e in vr.errors[:5]:
                log(f"    {e}", "err")
            log("\nStopping. Fix errors before compiling.", "err")
            return report

    # -- Candidates are now Genesis-compatible (binder emits tier + object_type directly) --
    adapted = cand  # No adapter needed; binder output is Genesis-native

    # -- Genesis: Compile shard --
    if skip_genesis:
        log("\n[Genesis] Skipped (--skip-genesis)", "dim")
    else:
        log("\n[Genesis] Compiling shard...", "stage")
        t_gen = time.time()

        try:
            from axm_build.compiler_generic import CompilerConfig, compile_generic_shard

            # Use canonical test key for now (user can override with AXM_PRIVATE_KEY)
            key_hex = os.environ.get("AXM_PRIVATE_KEY")
            if key_hex:
                priv_bytes = bytes.fromhex(key_hex)
            else:
                priv_bytes = bytes.fromhex(
                    "a665a45920422f9d417e4867efdc4fb8a04a1f3fff1fa07e998e86f7f7a27ae3"
                )
                log("  Using canonical test key (set AXM_PRIVATE_KEY for production)", "dim")

            cfg = CompilerConfig(
                source_path=src,
                candidates_path=adapted,
                out_dir=shard_dir,
                private_key=priv_bytes,
                publisher_id=os.environ.get("AXM_PUBLISHER_ID", "@nodal-flow"),
                publisher_name=os.environ.get("AXM_PUBLISHER_NAME", "Nodal Flow"),
                namespace=namespace,
                created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )

            ok = compile_generic_shard(cfg)
            dt_gen = time.time() - t_gen

            if ok:
                log(f"  Shard compiled and verified in {dt_gen:.1f}s", "ok")

                # Read manifest for summary
                manifest = json.loads((shard_dir / "manifest.json").read_text())
                shard_id = manifest.get("shard_id", "unknown")
                merkle = manifest.get("integrity", {}).get("merkle_root", "unknown")
                stats = manifest.get("statistics", {})

                log(f"  Shard ID:    {shard_id[:60]}...", "dim")
                log(f"  Merkle root: {merkle[:60]}...", "dim")
                log(f"  Entities:    {stats.get('entities', 0)}", "dim")
                log(f"  Claims:      {stats.get('claims', 0)}", "dim")
            else:
                log(f"  Genesis compilation FAILED", "err")
                return report

        except ImportError as e:
            log(f"  Genesis not importable: {e}", "err")
            log(f"  Make sure you're running from the axm-stack-v1 directory", "err")
            return report

    # -- Review HTML --
    if cand.exists():
        generate_review_html(src, sent, cand, html)
        log(f"\n  Review:  file://{html.resolve()}", "ok")

    # -- Summary --
    dt_total = time.time() - t0
    log(f"\n{'='*60}", "dim")
    log(f"Output: {out_dir}/", "ok")
    for f_name in ["source.txt", "sentences.jsonl", "raw_claims.jsonl",
                    "candidates.jsonl", "review.html"]:
        fp = out_dir / f_name
        if fp.exists():
            log(f"  {f_name:25s} {fp.stat().st_size:>8,} bytes", "dim")

    if shard_dir.exists() and (shard_dir / "manifest.json").exists():
        shard_size = sum(f.stat().st_size for f in shard_dir.rglob("*") if f.is_file())
        log(f"  shard/                    {shard_size:>8,} bytes", "dim")

    log(f"{'='*60}", "dim")
    log(f"Total: {dt_total:.1f}s", "ok")

    if shard_dir.exists() and (shard_dir / "manifest.json").exists():
        log("\nShard ready. Mount in Spectra or Nodal Flow.", "ok")
    elif cand.exists():
        log("\nCandidates ready. Run without --skip-genesis to compile shard.", "ok")

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(
        description="AXM Nodal Flow: article in, signed shard out",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python nodal_run.py "Tranexamic acid"
  python nodal_run.py "https://en.wikipedia.org/wiki/Aspirin"
  python nodal_run.py --source my_document.txt --out-dir out/my_doc
  python nodal_run.py "Tranexamic acid" --skip-stage1   # segment only
  python nodal_run.py "Tranexamic acid" --skip-genesis   # candidates only
""",
    )
    p.add_argument("article", nargs="?", help="Wikipedia article title or URL")
    p.add_argument("--source", help="Path to existing source.txt (skip fetch)")
    p.add_argument("--out-dir", help="Output directory (default: out/<slug>)")
    p.add_argument("--model", help="Ollama model name")
    p.add_argument("--host", help="Ollama host URL")
    p.add_argument("--namespace", default="generic/import", help="Shard namespace")
    p.add_argument("--skip-stage1", action="store_true", help="Skip LLM extraction")
    p.add_argument("--skip-genesis", action="store_true", help="Skip shard compilation")
    args = p.parse_args()

    if not args.article and not args.source:
        p.error("Provide either an article title/URL or --source path")

    slug = slug_from_input(args.article) if args.article else Path(args.source).stem
    out_dir = Path(args.out_dir) if args.out_dir else Path("out") / slug

    run_pipeline(
        title_or_url=args.article,
        source_path=Path(args.source) if args.source else None,
        out_dir=out_dir,
        model=args.model,
        host=args.host,
        skip_stage1=args.skip_stage1,
        skip_genesis=args.skip_genesis,
        namespace=args.namespace,
    )


if __name__ == "__main__":
    main()
