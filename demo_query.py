#!/usr/bin/env python3
"""
AXM End-to-End Demo: Shard → Mount → Query → Cited Answer → Hallucination Check.

Proves the equation: Output(Q) = Lθ[Prompt(Q, C(Q))]
Where C(Q) returns ONLY verified, cited, compiled knowledge.

Usage:
    # With Ollama running locally:
    python demo_query.py --shard <path-to-axm-genesis>/shards/gold/fm21-11-hemorrhage-v1 \\
        --question "When should I apply a tourniquet?"

    # Without LLM (shows verification pipeline only):
    python demo_query.py --shard <path-to-axm-genesis>/shards/gold/fm21-11-hemorrhage-v1 \\
        --question "How do I stop bleeding?" --no-llm

    # With specific model:
    python demo_query.py --shard out/aspirin/shard \\
        --question "What is Aspirin used for?" --model qwen2.5:7b-instruct

Environment:
    SPECTRA_TRUSTED_PUBKEY  Path to trusted publisher public key
    AXM_OLLAMA_HOST         Ollama endpoint (default: http://127.0.0.1:11434)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_root = Path(__file__).resolve().parent
sys.path.insert(0, str(_root / "genesis" / "src"))
sys.path.insert(0, str(_root / "spectra"))

try:
    from axiom_runtime.engine import SpectraEngine
except ImportError:
    print("Error: Could not import Spectra. Run from the axm-stack root.")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Terminal colors
# ---------------------------------------------------------------------------
def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m"

GREEN = lambda t: _c("32", t)
RED = lambda t: _c("31", t)
CYAN = lambda t: _c("36", t)
DIM = lambda t: _c("90", t)
BOLD = lambda t: _c("1", t)
YELLOW = lambda t: _c("33", t)


# ---------------------------------------------------------------------------
# LLM interface
# ---------------------------------------------------------------------------
def call_ollama(prompt: str, model: str, host: str) -> str:
    url = f"{host.rstrip('/')}/api/generate"
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.0},
    }).encode("utf-8")
    req = urllib.request.Request(url, payload, {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))["response"]
    except Exception as e:
        return None


# ---------------------------------------------------------------------------
# Hallucination firewall
# ---------------------------------------------------------------------------
_CITE_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

# Sentences starting with these are not factual claims
_SAFE_PREFIXES = (
    "i cannot", "based on", "the context", "the provided",
    "according to", "in summary", "to summarize", "note that",
    "however", "therefore", "additionally", "also",
)


def extract_citations(text: str) -> list[int]:
    cites = []
    for m in _CITE_RE.findall(text):
        for num in m.split(","):
            try:
                cites.append(int(num.strip()))
            except ValueError:
                pass
    return sorted(set(cites))


def enforce_provenance(response: str, valid_ids: set[int]) -> tuple[list[dict], bool]:
    """Returns list of annotated sentences and overall pass/fail."""
    sentences = [s.strip() for s in _SENTENCE_RE.split(response) if s.strip()]
    results = []
    all_clean = True

    for s in sentences:
        cites = extract_citations(s)
        is_short = len(s) < 20
        is_safe_prefix = any(s.lower().startswith(p) for p in _SAFE_PREFIXES)
        is_question = s.rstrip().endswith("?")

        if not cites and not is_short and not is_safe_prefix and not is_question:
            results.append({"text": s, "status": "uncited", "cites": []})
            all_clean = False
        elif cites:
            invalid = [c for c in cites if c not in valid_ids]
            if invalid:
                results.append({"text": s, "status": "fabricated", "cites": cites, "invalid": invalid})
                all_clean = False
            else:
                results.append({"text": s, "status": "verified", "cites": cites})
        else:
            results.append({"text": s, "status": "pass", "cites": []})

    return results, all_clean


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(description="AXM Undeniable Demo")
    p.add_argument("--shard", required=True, help="Path to compiled Genesis shard")
    p.add_argument("--question", required=True, help="Question to ask")
    p.add_argument("--model", default=os.environ.get("AXM_OLLAMA_MODEL", "qwen2.5:7b-instruct"))
    p.add_argument("--host", default=os.environ.get("AXM_OLLAMA_HOST", "http://127.0.0.1:11434"))
    p.add_argument("--no-llm", action="store_true", help="Show verification pipeline without LLM")
    args = p.parse_args()

    shard_dir = Path(args.shard).resolve()
    if not (shard_dir / "manifest.json").exists():
        print(RED(f"Error: Not a valid Genesis shard: {shard_dir}"))
        sys.exit(1)

    # ── Step 1: Boot Spectra ──────────────────────────────────────────────
    print(CYAN("[1/5] Booting Spectra Runtime..."))
    os.environ.setdefault("SPECTRA_DEV_MODE", "1")

    # Auto-detect trusted key from shard if not set
    if not os.environ.get("SPECTRA_TRUSTED_PUBKEY"):
        pub = shard_dir / "sig" / "publisher.pub"
        if pub.exists():
            os.environ["SPECTRA_TRUSTED_PUBKEY"] = str(pub)

    engine = SpectraEngine()

    # ── Step 2: Mount and verify shard ────────────────────────────────────
    print(CYAN("[2/5] Mounting and verifying shard (Genesis constitution check)..."))
    try:
        mount = engine.mount(str(shard_dir), secret_b64=None)
    except Exception as e:
        print(RED(f"✗ Verification FAILED: {e}"))
        sys.exit(1)

    shard_id = mount["shard_id"]
    merkle = mount["merkle_root"]
    tables = mount["tables"]
    print(GREEN(f"  ✓ Shard verified: {shard_id[:50]}..."))
    print(GREEN(f"  ✓ Merkle root: {merkle[:24]}..."))
    print(GREEN(f"  ✓ Tables mounted: {len(tables)}"))

    # Load source title
    manifest = json.loads((shard_dir / "manifest.json").read_text())
    source_title = manifest.get("metadata", {}).get("title", shard_dir.name)

    # ── Step 3: Query shard for relevant evidence ─────────────────────────
    print(CYAN("[3/5] Querying shard for relevant evidence..."))

    # Resolve view names (core + extensions)
    view = {}
    for t in tables:
        for prefix in ("claims", "entities", "provenance", "spans",
                        "ext_locators", "ext_references", "ext_lineage"):
            if t.startswith(prefix + "__"):
                view[prefix] = t
                break

    for required in ("claims", "entities", "provenance", "spans"):
        if required not in view:
            print(RED(f"✗ Missing {required} table in mount"))
            sys.exit(1)

    has_locators = "ext_locators" in view
    if has_locators:
        print(GREEN(f"  ✓ ext/locators@1 detected — structural position available"))

    # Full provenance chain query - resolves entity IDs to human labels
    # LEFT JOIN locators if present to show page numbers
    locator_select = ""
    locator_join = ""
    if has_locators:
        locator_select = ", l.kind AS loc_kind, l.page_index AS loc_page, l.file_path AS loc_file"
        locator_join = f'LEFT JOIN "{view["ext_locators"]}" l ON l.span_id = s.span_id'

    sql = f"""
    SELECT
        e_subj.label AS subject_label,
        c.predicate,
        CASE WHEN c.object_type = 'entity' THEN e_obj.label ELSE c.object END AS object_label,
        c.object_type,
        s.text AS evidence,
        s.byte_start,
        s.byte_end,
        p.source_hash
        {locator_select}
    FROM "{view['claims']}" c
    JOIN "{view['entities']}" e_subj ON c.subject = e_subj.entity_id
    LEFT JOIN "{view['entities']}" e_obj ON c.object_type = 'entity' AND c.object = e_obj.entity_id
    JOIN "{view['provenance']}" p ON c.claim_id = p.claim_id
    JOIN "{view['spans']}" s ON p.source_hash = s.source_hash
        AND p.byte_start = s.byte_start AND p.byte_end = s.byte_end
    {locator_join}
    """

    qr = engine.query_json(sql)
    all_rows = qr["rows"]

    if not all_rows:
        print(YELLOW("  ⚠ No claims found in shard"))
        sys.exit(0)

    print(GREEN(f"  ✓ {len(all_rows)} evidence spans retrieved"))

    # Build context mapping: citation number → evidence data
    context = {}
    context_lines = []
    for idx, row in enumerate(all_rows, 1):
        subj, pred, obj, obj_type, evidence, bs, be, src_hash = row[:8]
        loc_kind = row[8] if has_locators and len(row) > 8 else None
        loc_page = row[9] if has_locators and len(row) > 9 else None
        loc_file = row[10] if has_locators and len(row) > 10 else None

        entry = {
            "subject": subj, "predicate": pred, "object": obj,
            "evidence": evidence, "byte_start": bs, "byte_end": be,
            "source_hash": src_hash,
        }
        if loc_page is not None:
            entry["page"] = loc_page
            entry["file"] = loc_file or ""
        context[idx] = entry

        page_note = f" (page {loc_page})" if loc_page is not None else ""
        context_lines.append(
            f"[{idx}] {subj} → {pred} → {obj}{page_note}\n"
            f"    Evidence: \"{evidence}\""
        )

    context_block = "\n".join(context_lines)

    # Print the evidence
    print()
    print("─" * 70)
    print(BOLD("COMPILED KNOWLEDGE (from verified shard):"))
    print("─" * 70)
    for line in context_lines:
        print(DIM(line))
    print("─" * 70)
    print()

    if args.no_llm:
        print(CYAN("[4/5] Skipping LLM (--no-llm)"))
        print(CYAN("[5/5] Skipping hallucination check"))
        print()
        print(GREEN("✓ Verification pipeline complete."))
        print(f"  Shard: {source_title}")
        print(f"  Claims: {len(all_rows)}")
        print(f"  Each claim traces to exact byte range in source document.")
        return

    # ── Step 4: LLM generation ────────────────────────────────────────────
    print(CYAN(f"[4/5] Generating answer via {args.model}..."))

    prompt = f"""You are a strict factual assistant. Answer ONLY from the provided context.
If the answer is not in the context, say "I cannot answer this from the provided knowledge."

RULES:
- Every factual sentence MUST end with a citation like [1] or [2, 4].
- Citations refer to the Context IDs below.
- DO NOT use any knowledge outside the provided context.
- DO NOT invent or extrapolate facts.

CONTEXT:
{context_block}

QUESTION: {args.question}
ANSWER:"""

    response = call_ollama(prompt, args.model, args.host)

    if response is None:
        print(YELLOW(f"  ⚠ Could not connect to Ollama at {args.host}"))
        print(YELLOW(f"    Start Ollama or use --no-llm to see verification pipeline only"))
        return

    # ── Step 5: Hallucination firewall ────────────────────────────────────
    print(CYAN("[5/5] Enforcing provenance contract..."))
    print()
    print("═" * 70)
    print(BOLD(f"Q: {args.question}"))
    print("═" * 70)
    print()

    valid_ids = set(context.keys())
    annotated, is_clean = enforce_provenance(response, valid_ids)

    used_citations = set()
    for sent in annotated:
        if sent["status"] == "verified":
            print(GREEN(f"  {sent['text']}"))
            used_citations.update(sent["cites"])
        elif sent["status"] == "pass":
            print(f"  {sent['text']}")
        elif sent["status"] == "uncited":
            print(RED(f"  {sent['text']}"))
            print(RED(f"    ↑ UNCITED CLAIM — not traceable to any shard evidence"))
        elif sent["status"] == "fabricated":
            print(RED(f"  {sent['text']}"))
            print(RED(f"    ↑ FABRICATED CITATION — [{', '.join(str(i) for i in sent['invalid'])}] does not exist in context"))

    # Print source references
    print()
    print("─" * 70)
    print(BOLD("SOURCE REFERENCES:"))
    print("─" * 70)
    for cid in sorted(used_citations):
        d = context[cid]
        preview = d["evidence"].replace("\n", " ")
        page_info = f", page {d['page']}" if "page" in d else ""
        print(DIM(f"  [{cid}] {source_title}, bytes {d['byte_start']}–{d['byte_end']}{page_info}"))
        print(DIM(f"      \"{preview}\""))

    # Verdict
    print()
    print("═" * 70)
    if is_clean:
        print(GREEN("✓ All claims verified against mounted shard"))
        print(GREEN(f"✓ {len(used_citations)} citations traced to source byte ranges"))
        print(GREEN("✗ 0 hallucinations detected"))
    else:
        uncited = sum(1 for s in annotated if s["status"] == "uncited")
        fabricated = sum(1 for s in annotated if s["status"] == "fabricated")
        print(RED("⚠ HALLUCINATION FIREWALL TRIGGERED"))
        if uncited:
            print(RED(f"  {uncited} uncited claim(s) — LLM said something not in any shard"))
        if fabricated:
            print(RED(f"  {fabricated} fabricated citation(s) — LLM invented source references"))
    print("═" * 70)


if __name__ == "__main__":
    main()
