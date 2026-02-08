"""
AXM Forge Tier 3 -- Stage 1: LLM Claim Extractor.

Reads sentences.jsonl (from Stage 0).
Sends overlapping batches to Ollama.
Writes raw_claims.jsonl (append-only, resumable).

Features:
  - Overlapping batches (OVERLAP=5) to capture cross-sentence claims
  - Exponential backoff retry on Ollama failures (3 retries)
  - Batch ID filtering: hallucinated sentence IDs are dropped before write
  - Progress markers: empty batches still advance the cursor on resume
  - Incremental flush: safe to kill mid-run, resume picks up correctly

The LLM produces:
  - claim text (natural language, pronouns resolved)
  - sentence IDs (which sentences support the claim)
  - SPO triple (for Genesis indexing)

This stage does NOT do byte binding.  That is Stage 2.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You extract verifiable claims from numbered sentences.

Rules:
- Extract atomic, factual claims.
- Resolve pronouns in the claim text (replace "it", "this", "they" with concrete nouns).
- You may cite multiple sentence IDs when the claim depends on multi-sentence logic.
- Do not invent facts.
- Output strict JSON only, no extra keys.

Return JSON exactly like:
{
  "claims": [
    {
      "text": "...",
      "sentence_ids": [0, 1],
      "subject": "...",
      "predicate": "...",
      "object": "...",
      "confidence": "high"
    }
  ]
}"""

EXTRACT_PROMPT = """\
SENTENCES:
{sentences}

Extract claims. Reference sentence IDs only. Output strict JSON."""


# ---------------------------------------------------------------------------
# Ollama client with retry
# ---------------------------------------------------------------------------

def _retry_ollama_chat(
    messages: List[Dict[str, str]],
    model: str,
    host: str,
    retries: int = 3,
    backoff_base: float = 2.0,
    timeout_s: int = 120,
) -> str:
    """Call Ollama /api/chat with exponential backoff.  Returns "" on total failure."""
    url = host.rstrip("/") + "/api/chat"
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.0,
            "num_ctx": 4096,
            "num_predict": 1024,
        },
    }
    data = json.dumps(payload).encode("utf-8")

    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                url=url, data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                raw = resp.read()
            obj = json.loads(raw.decode("utf-8"))
            content = obj.get("message", {}).get("content", "")
            if isinstance(content, str):
                return content
            return ""
        except (urllib.error.URLError, urllib.error.HTTPError,
                json.JSONDecodeError, OSError, TimeoutError) as exc:
            if attempt == retries:
                print(f"WARN: Ollama failed after {retries+1} attempts: {exc}")
                return ""
            time.sleep(backoff_base ** attempt)

    return ""


def _safe_parse_json(text: str) -> Optional[Dict[str, Any]]:
    """Parse JSON strictly.  Fallback: find outermost braces containing 'claims' key."""
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fallback: find { ... } containing "claims"
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        candidate = text[start : end + 1]
        try:
            obj = json.loads(candidate)
            if "claims" in obj:
                return obj
        except json.JSONDecodeError:
            pass
    return None


# ---------------------------------------------------------------------------
# Resume logic
# ---------------------------------------------------------------------------

def _get_resume_point(out_path: Path, overlap: int) -> int:
    """Scan raw_claims.jsonl for last progress marker.  Returns sentence index to resume from."""
    if not out_path.exists():
        return 0
    last_sent_idx = -1
    with out_path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                obj = json.loads(line)
                meta = obj.get("meta", {})
                lsi = meta.get("last_sent_idx", -1)
                if lsi > last_sent_idx:
                    last_sent_idx = lsi
            except (json.JSONDecodeError, AttributeError):
                pass
    if last_sent_idx < 0:
        return 0
    # Next batch starts at (last_sent_idx - overlap + 1) to maintain overlap continuity.
    # But never go negative.
    return max(0, last_sent_idx - overlap + 1)


# ---------------------------------------------------------------------------
# Main extraction loop
# ---------------------------------------------------------------------------

def run_stage1(
    sentences_path: Path,
    out_path: Path,
    model: str,
    host: str,
    batch_size: int = 20,
    overlap: int = 5,
    resume: bool = True,
) -> Dict[str, Any]:
    """
    Run Stage 1 extraction.  Returns summary dict.

    Reads sentences.jsonl, sends overlapping batches to Ollama,
    writes raw_claims.jsonl incrementally.
    """
    # Load all sentences
    segments: List[Dict[str, Any]] = []
    with sentences_path.open("r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if s:
                segments.append(json.loads(s))

    if not segments:
        return {"status": "EMPTY", "reason": "no sentences"}

    # Resume
    start_idx = 0
    if resume:
        start_idx = _get_resume_point(out_path, overlap)
        if start_idx > 0:
            print(f"Resuming Stage 1 from sentence index {start_idx}")

    step = max(1, batch_size - overlap)
    mode = "a" if start_idx > 0 else "w"

    out_path.parent.mkdir(parents=True, exist_ok=True)

    total_claims = 0
    total_batches = 0
    dropped_hallucinated = 0
    dropped_no_valid_ids = 0

    with out_path.open(mode, encoding="utf-8") as f_out:
        curr = start_idx
        while curr < len(segments):
            batch = segments[curr : curr + batch_size]
            if not batch:
                break

            valid_batch_ids = {s["index"] for s in batch}
            last_sent_idx = batch[-1]["index"]
            total_batches += 1

            # Build prompt
            prompt_lines = "\n".join(
                f"[{s['index']}] {s['text']}" for s in batch
            )
            msgs = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": EXTRACT_PROMPT.format(sentences=prompt_lines)},
            ]

            # Call LLM
            raw_resp = _retry_ollama_chat(msgs, model, host)
            parsed = _safe_parse_json(raw_resp)
            claims = []
            if parsed and isinstance(parsed.get("claims"), list):
                claims = parsed["claims"]

            # Filter + write
            batch_claims = 0
            for c in claims:
                if not isinstance(c, dict):
                    continue
                ids = c.get("sentence_ids")
                if not isinstance(ids, list) or not ids:
                    continue

                # Filter hallucinated IDs to batch range only
                clean_ids = [i for i in ids if isinstance(i, int) and i in valid_batch_ids]
                hallucinated = [i for i in ids if isinstance(i, int) and i not in valid_batch_ids]
                if hallucinated:
                    dropped_hallucinated += len(hallucinated)
                if not clean_ids:
                    dropped_no_valid_ids += 1
                    continue

                claim_text = c.get("text", "")
                if not isinstance(claim_text, str) or not claim_text.strip():
                    continue

                record = {
                    "claim_text": claim_text.strip(),
                    "subject": (c.get("subject") or "").strip() or None,
                    "predicate": (c.get("predicate") or "").strip() or None,
                    "object": (c.get("object") or "").strip() or None,
                    "sentence_ids": clean_ids,
                    "meta": {
                        "batch_start": curr,
                        "last_sent_idx": last_sent_idx,
                    },
                }
                f_out.write(json.dumps(record, ensure_ascii=False) + "\n")
                batch_claims += 1

            total_claims += batch_claims

            # Progress marker (written even for empty batches so resume advances)
            progress = {
                "meta": {
                    "type": "progress",
                    "last_sent_idx": last_sent_idx,
                    "batch_claims": batch_claims,
                    "timestamp": time.time(),
                }
            }
            f_out.write(json.dumps(progress) + "\n")
            f_out.flush()

            print(f"  batch {curr}-{last_sent_idx}: {batch_claims} claims")
            curr += step

    return {
        "status": "PASS",
        "model": model,
        "batches": total_batches,
        "claims": total_claims,
        "dropped_hallucinated_ids": dropped_hallucinated,
        "dropped_no_valid_ids": dropped_no_valid_ids,
        "out": str(out_path),
    }


def main() -> None:
    import argparse
    p = argparse.ArgumentParser(description="AXM Tier 3 Stage 1: LLM claim extraction")
    p.add_argument("--sentences", required=True, help="Path to sentences.jsonl")
    p.add_argument("--out", required=True, help="Path to raw_claims.jsonl")
    p.add_argument("--model", default=None, help="Ollama model name")
    p.add_argument("--host", default=None, help="Ollama host URL")
    p.add_argument("--batch-size", type=int, default=20)
    p.add_argument("--overlap", type=int, default=5)
    p.add_argument("--no-resume", action="store_true")
    args = p.parse_args()

    model = args.model or os.environ.get("AXM_OLLAMA_MODEL", "qwen2.5:7b-instruct")
    host = args.host or os.environ.get("AXM_OLLAMA_HOST", "http://127.0.0.1:11434")

    report = run_stage1(
        sentences_path=Path(args.sentences),
        out_path=Path(args.out),
        model=model,
        host=host,
        batch_size=args.batch_size,
        overlap=args.overlap,
        resume=(not args.no_resume),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
