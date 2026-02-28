#!/usr/bin/env python3
"""
AXM Forge Runner: Document directory → signed shard.

Set-and-forget ingestion with:
  - Pass-based architecture (tier 0/1 instant, tier 2/3 LLM batched)
  - Checkpointing per chunk (resume on crash)
  - Progress bar with ETA
  - Job manifest with time estimates
  - Auto-compile at end

Usage:
    python forge_run.py --input ./legal_docs/ --output ./shards/legal/
    python forge_run.py --input ./legal_docs/ --plan-only
    python forge_run.py --input ./legal_docs/ --resume
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import shutil
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_root = Path(__file__).resolve().parent
sys.path.insert(0, str(_root / "forge"))
sys.path.insert(0, str(_root))


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Candidate:
    """Genesis-compatible candidate claim."""
    subject: str
    predicate: str
    object: str
    object_type: str  # "entity" or "literal:string"
    evidence: str
    tier: int
    extraction_method: str
    meta: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "subject": self.subject,
            "predicate": self.predicate,
            "object": self.object,
            "object_type": self.object_type,
            "evidence": self.evidence,
            "tier": self.tier,
        }
        if self.meta:
            d["meta"] = self.meta
        return d


@dataclass
class PassResult:
    """Result of a single extraction pass."""
    pass_id: str
    extractor: str
    tier: int
    candidates: List[Candidate]
    elapsed_s: float
    status: str  # "ok", "skipped", "failed"
    error: Optional[str] = None


@dataclass
class JobPlan:
    """Pre-computed plan for the entire ingestion job."""
    input_dir: Path
    output_dir: Path
    source_files: List[Path]
    total_bytes: int
    total_lines: int
    passes: List[Dict[str, Any]]
    estimated_seconds: float
    chunk_count_estimate: int


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log(msg: str, level: str = "info") -> None:
    colors = {"info": "37", "ok": "32", "warn": "33", "err": "31",
              "stage": "36", "dim": "90", "progress": "35"}
    code = colors.get(level, "37")
    print(f"\033[{code}m{msg}\033[0m")


def progress_bar(current: int, total: int, start_time: float, prefix: str = "") -> None:
    if total == 0:
        return
    pct = current / total
    elapsed = time.time() - start_time
    eta = (elapsed / pct - elapsed) if pct > 0.05 else 0
    bar_len = 30
    filled = int(bar_len * pct)
    bar = "█" * filled + "░" * (bar_len - filled)
    eta_str = f"{int(eta)}s" if eta > 0 else "..."
    print(f"\r\033[35m  {prefix}|{bar}| {current}/{total} ({pct:.0%}) ETA: {eta_str}\033[0m", end="", flush=True)


# ---------------------------------------------------------------------------
# Source normalization
# ---------------------------------------------------------------------------

def normalize_source(text: str) -> str:
    """NFC normalize + clean whitespace. Matches Genesis compiler behavior."""
    text = unicodedata.normalize("NFC", text)
    # Normalize line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def merge_sources(files: List[Path]) -> Tuple[str, Dict[str, Tuple[int, int]]]:
    """Merge multiple files into single source text with file offset tracking.

    Returns (merged_text, {filename: (byte_start, byte_end)}).
    """
    parts = []
    offsets = {}
    current_offset = 0

    for f in sorted(files):
        raw = f.read_text(encoding="utf-8")
        normalized = normalize_source(raw)
        # Add file separator comment
        header = f"# === SOURCE: {f.name} ===\n\n"
        chunk = header + normalized
        if not chunk.endswith("\n"):
            chunk += "\n"
        chunk += "\n"

        chunk_bytes = chunk.encode("utf-8")
        offsets[f.name] = (current_offset, current_offset + len(chunk_bytes))
        current_offset += len(chunk_bytes)
        parts.append(chunk)

    return "".join(parts), offsets


# ---------------------------------------------------------------------------
# TIER 0 EXTRACTORS
# ---------------------------------------------------------------------------

class Tier0Markdown:
    """Extract structured claims from markdown: headings, tables, blockquotes."""

    name = "tier0_markdown"
    tier = 0

    @staticmethod
    def extract(source_text: str, source_bytes: bytes) -> List[Candidate]:
        candidates = []

        # Extract table rows as claims
        # Pattern: | col1 | col2 | col3 | col4 |
        lines = source_text.split("\n")
        in_table = False
        headers = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("|") and "|" in stripped[1:]:
                cells = [c.strip().strip("*") for c in stripped.split("|")[1:-1]]
                if all(c in ("-", ":-", "-:", ":-:") or set(c) <= {"-", ":", " "} for c in cells):
                    continue  # separator row
                if not in_table:
                    headers = cells
                    in_table = True
                    continue
                if len(cells) >= 2 and headers:
                    # Each row becomes a claim
                    subj = cells[0].strip()
                    if not subj:
                        continue
                    evidence = stripped
                    # Find byte position
                    byte_pos = source_bytes.find(evidence.encode("utf-8"))
                    if byte_pos < 0:
                        continue
                    for i, cell in enumerate(cells[1:], 1):
                        if cell.strip() and i < len(headers):
                            candidates.append(Candidate(
                                subject=subj,
                                predicate=headers[i].lower().replace(" ", "_"),
                                object=cell.strip(),
                                object_type="literal:string",
                                evidence=evidence,
                                tier=0,
                                extraction_method="tier0_markdown_table",
                                meta={"header": headers[i]},
                            ))
            else:
                in_table = False
                headers = []

        return candidates


class Tier0Statutory:
    """Extract statutory section references and their properties."""

    name = "tier0_statutory"
    tier = 0

    # Statutory section pattern
    SECTION_RE = re.compile(
        r'(?:California\s+)?(?:Family\s+Code|Fam\.?\s*Code)\s*§\s*(\d+(?:\([a-z]\))?)',
        re.IGNORECASE
    )

    # Case citation pattern
    CASE_RE = re.compile(
        r'\*([^*]{5,80})\*\s*\((\d{1,4}\s*Cal\.(?:App\.)?(?:3d|4th|5th|2d)?\s*\d+[^)]*)\)'
    )

    # Blockquote pattern (for verbatim sources doc)
    QUOTE_RE = re.compile(r'^>\s*(.+)$', re.MULTILINE)

    @staticmethod
    def extract(source_text: str, source_bytes: bytes) -> List[Candidate]:
        candidates = []

        # 1. Statutory section mentions with context
        for m in Tier0Statutory.SECTION_RE.finditer(source_text):
            section = m.group(1)
            # Get surrounding sentence as evidence
            start = max(0, source_text.rfind(".", 0, m.start()) + 1)
            end = source_text.find(".", m.end())
            if end < 0:
                end = min(len(source_text), m.end() + 200)
            else:
                end += 1
            evidence = source_text[start:end].strip()
            if len(evidence) < 10:
                continue

            # Verify byte alignment
            ev_bytes = evidence.encode("utf-8")
            byte_pos = source_bytes.find(ev_bytes)
            if byte_pos < 0:
                continue

            candidates.append(Candidate(
                subject=f"Family Code § {section}",
                predicate="referenced_in_context",
                object=evidence[:100].strip(),
                object_type="literal:string",
                evidence=evidence,
                tier=0,
                extraction_method="tier0_statutory_section",
                meta={"section": section},
            ))

        # 2. Case citations
        for m in Tier0Statutory.CASE_RE.finditer(source_text):
            case_name = m.group(1).strip()
            citation = m.group(2).strip()
            # Get surrounding context
            start = max(0, source_text.rfind(".", 0, m.start()) + 1)
            end = source_text.find(".", m.end())
            if end < 0:
                end = min(len(source_text), m.end() + 200)
            else:
                end += 1
            evidence = source_text[start:end].strip()

            ev_bytes = evidence.encode("utf-8")
            byte_pos = source_bytes.find(ev_bytes)
            if byte_pos < 0:
                continue

            candidates.append(Candidate(
                subject=case_name,
                predicate="cited_as",
                object=citation,
                object_type="literal:string",
                evidence=evidence,
                tier=0,
                extraction_method="tier0_case_citation",
                meta={"case_name": case_name, "citation": citation},
            ))

        # 3. Verbatim quoted holdings (blockquotes)
        for m in Tier0Statutory.QUOTE_RE.finditer(source_text):
            quote_text = m.group(1).strip()
            if len(quote_text) < 30:
                continue
            # Skip formatting-only lines
            if quote_text.startswith("**") and quote_text.endswith("**"):
                continue

            evidence = quote_text
            ev_bytes = evidence.encode("utf-8")
            byte_pos = source_bytes.find(ev_bytes)
            if byte_pos < 0:
                continue

            candidates.append(Candidate(
                subject="verbatim_holding",
                predicate="states",
                object=quote_text[:100],
                object_type="literal:string",
                evidence=evidence,
                tier=0,
                extraction_method="tier0_blockquote",
            ))

        return candidates


class Tier0Headings:
    """Extract document structure from markdown headings."""

    name = "tier0_headings"
    tier = 0

    HEADING_RE = re.compile(r'^(#{1,4})\s+\**(.+?)\**\s*$', re.MULTILINE)

    @staticmethod
    def extract(source_text: str, source_bytes: bytes) -> List[Candidate]:
        candidates = []
        current_node = None

        for m in Tier0Headings.HEADING_RE.finditer(source_text):
            level = len(m.group(1))
            title = m.group(2).strip()
            evidence = m.group(0).strip()

            ev_bytes = evidence.encode("utf-8")
            byte_pos = source_bytes.find(ev_bytes)
            if byte_pos < 0:
                continue

            # NODE-level headings (## NODE N: ...)
            node_match = re.match(r'NODE\s+(\d+):\s*(.+)', title)
            if node_match:
                current_node = node_match.group(2).strip()
                candidates.append(Candidate(
                    subject=f"Node {node_match.group(1)}",
                    predicate="covers_topic",
                    object=current_node,
                    object_type="literal:string",
                    evidence=evidence,
                    tier=0,
                    extraction_method="tier0_heading_node",
                ))
                continue

            # Case law / statute headings under a node
            if level >= 3 and current_node:
                candidates.append(Candidate(
                    subject=title,
                    predicate="analyzed_under",
                    object=current_node,
                    object_type="literal:string",
                    evidence=evidence,
                    tier=0,
                    extraction_method="tier0_heading_structure",
                ))

        return candidates


# ---------------------------------------------------------------------------
# TIER 1 EXTRACTORS
# ---------------------------------------------------------------------------

class Tier1CrossRef:
    """Extract cross-references between statutes and cases."""

    name = "tier1_crossref"
    tier = 1

    # "§ X, Feldman, and Tharp" style synthesis
    SYNTH_RE = re.compile(
        r'(?:synthesis|combination|conjunction|interplay)\s+of\s+(.+?)(?:\.|$)',
        re.IGNORECASE
    )

    # Direct references: "under § X" / "per § X" / "pursuant to § X"
    UNDER_RE = re.compile(
        r'(?:under|pursuant\s+to|per|analyzed\s+under|referenced?\s+(?:in|by))\s+'
        r'(?:(?:Family\s+Code|Fam\.?\s*Code)\s*)?§\s*(\d+)',
        re.IGNORECASE
    )

    # Case interprets statute: "Feldman ... § 271" or "§ 271 ... Feldman"
    CASE_INTERP_RE = re.compile(
        r'(?:\*([^*]+)\*|(?:In re Marriage of|Montenegro v\.|Burchard v\.)\s+(\w+))'
        r'[^.]{0,100}'
        r'§\s*(\d+)',
        re.IGNORECASE
    )

    @staticmethod
    def extract(source_text: str, source_bytes: bytes) -> List[Candidate]:
        candidates = []
        seen = set()

        # Cross-references between statutes
        for m in Tier1CrossRef.UNDER_RE.finditer(source_text):
            section = m.group(1)
            # Get the sentence containing this reference
            start = max(0, source_text.rfind(".", 0, m.start()) + 1)
            end = source_text.find(".", m.end())
            if end < 0:
                end = min(len(source_text), m.end() + 200)
            else:
                end += 1
            evidence = source_text[start:end].strip()

            ev_bytes = evidence.encode("utf-8")
            byte_pos = source_bytes.find(ev_bytes)
            if byte_pos < 0:
                continue

            # Find what references this section
            # Look for the nearest heading or subject
            heading_before = ""
            for hm in Tier0Headings.HEADING_RE.finditer(source_text[:m.start()]):
                heading_before = hm.group(2).strip()

            if heading_before:
                key = (heading_before, "references", f"§ {section}")
                if key not in seen:
                    seen.add(key)
                    candidates.append(Candidate(
                        subject=heading_before,
                        predicate="references",
                        object=f"Family Code § {section}",
                        object_type="entity",
                        evidence=evidence,
                        tier=1,
                        extraction_method="tier1_crossref",
                    ))

        # Case interprets statute
        for m in Tier1CrossRef.CASE_INTERP_RE.finditer(source_text):
            case_name = (m.group(1) or m.group(2) or "").strip()
            section = m.group(3)
            if not case_name:
                continue

            start = max(0, source_text.rfind(".", 0, m.start()) + 1)
            end = source_text.find(".", m.end())
            if end < 0:
                end = min(len(source_text), m.end() + 200)
            else:
                end += 1
            evidence = source_text[start:end].strip()

            ev_bytes = evidence.encode("utf-8")
            byte_pos = source_bytes.find(ev_bytes)
            if byte_pos < 0:
                continue

            key = (case_name, "interprets", f"§ {section}")
            if key not in seen:
                seen.add(key)
                candidates.append(Candidate(
                    subject=case_name,
                    predicate="interprets",
                    object=f"Family Code § {section}",
                    object_type="entity",
                    evidence=evidence,
                    tier=1,
                    extraction_method="tier1_case_interprets",
                ))

        return candidates


# ---------------------------------------------------------------------------
# EXTRACTOR REGISTRY
# ---------------------------------------------------------------------------

TIER0_EXTRACTORS = [Tier0Markdown, Tier0Statutory, Tier0Headings]
TIER1_EXTRACTORS = [Tier1CrossRef]


# ---------------------------------------------------------------------------
# JOB PLANNING
# ---------------------------------------------------------------------------

def plan_job(input_dir: Path, output_dir: Path, llm_model: str = "llama3:8b") -> JobPlan:
    """Scan input directory, estimate work, return plan."""

    # Find all processable files
    source_files = []
    for ext in ("*.md", "*.txt", "*.text"):
        source_files.extend(input_dir.glob(ext))
    source_files = sorted(source_files)

    if not source_files:
        raise FileNotFoundError(f"No .md/.txt files found in {input_dir}")

    total_bytes = sum(f.stat().st_size for f in source_files)
    total_lines = sum(1 for f in source_files for _ in f.open())

    # Estimate chunks for LLM (roughly 1 chunk per 20 sentences, ~3 sentences per line)
    est_sentences = total_lines * 2  # rough: 2 sentences per line avg
    est_chunks = max(1, est_sentences // 20)

    # Time estimates (conservative)
    # Tier 0/1: essentially instant (<1s)
    # Tier 3 on 4060 with 8b model: ~8s per chunk
    tier0_time = 0.5
    tier1_time = 0.5
    tier3_time = est_chunks * 8.0  # 8s per chunk on local Ollama

    passes = [
        {"id": "tier0_markdown",  "extractor": "Tier0Markdown",  "tier": 0, "est_s": 0.1},
        {"id": "tier0_statutory", "extractor": "Tier0Statutory",  "tier": 0, "est_s": 0.1},
        {"id": "tier0_headings",  "extractor": "Tier0Headings",   "tier": 0, "est_s": 0.1},
        {"id": "tier1_crossref",  "extractor": "Tier1CrossRef",   "tier": 1, "est_s": 0.2},
        {"id": "tier3_llm",       "extractor": f"Ollama/{llm_model}", "tier": 3, "est_s": tier3_time},
    ]

    return JobPlan(
        input_dir=input_dir,
        output_dir=output_dir,
        source_files=source_files,
        total_bytes=total_bytes,
        total_lines=total_lines,
        passes=passes,
        estimated_seconds=tier0_time + tier1_time + tier3_time,
        chunk_count_estimate=est_chunks,
    )


def print_plan(plan: JobPlan) -> None:
    """Pretty-print the job plan."""
    log(f"\n{'='*60}", "stage")
    log(f"  AXM FORGE: INGESTION PLAN", "stage")
    log(f"{'='*60}", "stage")
    log(f"  Input:  {plan.input_dir}", "info")
    log(f"  Output: {plan.output_dir}", "info")
    log(f"  Files:  {len(plan.source_files)}", "info")
    log(f"  Size:   {plan.total_bytes:,} bytes ({plan.total_lines} lines)", "info")
    log(f"", "info")

    tier0_count = sum(1 for p in plan.passes if p["tier"] == 0)
    tier1_count = sum(1 for p in plan.passes if p["tier"] == 1)
    tier3_count = sum(1 for p in plan.passes if p["tier"] == 3)

    log(f"  Passes:", "info")
    log(f"    Tier 0: {tier0_count} extractors (instant)", "ok")
    log(f"    Tier 1: {tier1_count} extractors (instant)", "ok")
    if tier3_count:
        log(f"    Tier 3: ~{plan.chunk_count_estimate} LLM chunks", "warn")

    total_min = plan.estimated_seconds / 60
    if total_min < 1:
        log(f"\n  Estimated time: <1 minute", "ok")
    elif total_min < 60:
        log(f"\n  Estimated time: ~{total_min:.0f} minutes", "warn")
    else:
        log(f"\n  Estimated time: ~{total_min/60:.1f} hours", "warn")

    log(f"\n  Resume: enabled (checkpoint per LLM chunk)", "dim")
    log(f"{'='*60}\n", "stage")


# ---------------------------------------------------------------------------
# CHECKPOINT / RESUME
# ---------------------------------------------------------------------------

class CheckpointManager:
    """Manages checkpoint state for resumable extraction."""

    def __init__(self, work_dir: Path):
        self.work_dir = work_dir
        self.state_file = work_dir / "checkpoint.json"
        self.state: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        if self.state_file.exists():
            return json.loads(self.state_file.read_text())
        return {"completed_passes": [], "llm_cursor": 0, "candidates_written": 0}

    def save(self) -> None:
        self.state_file.write_text(json.dumps(self.state, indent=2))

    def is_pass_done(self, pass_id: str) -> bool:
        return pass_id in self.state.get("completed_passes", [])

    def mark_pass_done(self, pass_id: str) -> None:
        if pass_id not in self.state["completed_passes"]:
            self.state["completed_passes"].append(pass_id)
        self.save()

    def get_llm_cursor(self) -> int:
        return self.state.get("llm_cursor", 0)

    def set_llm_cursor(self, cursor: int) -> None:
        self.state["llm_cursor"] = cursor
        self.save()

    def get_candidates_written(self) -> int:
        return self.state.get("candidates_written", 0)

    def add_candidates(self, count: int) -> None:
        self.state["candidates_written"] = self.state.get("candidates_written", 0) + count
        self.save()


# ---------------------------------------------------------------------------
# MAIN PIPELINE
# ---------------------------------------------------------------------------

def run_tier0_tier1(
    source_text: str,
    source_bytes: bytes,
    work_dir: Path,
    ckpt: CheckpointManager,
) -> List[Candidate]:
    """Run all tier 0 and tier 1 extractors."""
    all_candidates = []

    for extractor_cls in TIER0_EXTRACTORS + TIER1_EXTRACTORS:
        pass_id = extractor_cls.name
        if ckpt.is_pass_done(pass_id):
            log(f"  {pass_id}: skipped (checkpoint)", "dim")
            # Load from saved file
            saved = work_dir / f"{pass_id}_candidates.jsonl"
            if saved.exists():
                from axm_forge.extraction.schemas import read_jsonl
                for rec in read_jsonl(saved):
                    all_candidates.append(Candidate(**{
                        k: rec[k] for k in ("subject", "predicate", "object",
                                             "object_type", "evidence", "tier",
                                             "extraction_method")
                        if k in rec
                    }, meta=rec.get("meta")))
            continue

        t0 = time.time()
        try:
            candidates = extractor_cls.extract(source_text, source_bytes)
            dt = time.time() - t0
            log(f"  {pass_id}: {len(candidates)} candidates ({dt:.2f}s)", "ok")

            # Save pass results
            out_file = work_dir / f"{pass_id}_candidates.jsonl"
            with out_file.open("w", encoding="utf-8") as f:
                for c in candidates:
                    f.write(json.dumps(c.to_dict(), ensure_ascii=False) + "\n")

            all_candidates.extend(candidates)
            ckpt.mark_pass_done(pass_id)

        except Exception as e:
            log(f"  {pass_id}: FAILED ({e})", "err")

    return all_candidates


def run_tier3_llm(
    source_path: Path,
    work_dir: Path,
    ckpt: CheckpointManager,
    model: str = "llama3:8b",
    host: str = "http://127.0.0.1:11434",
    batch_size: int = 20,
    overlap: int = 5,
) -> List[Dict[str, Any]]:
    """Run LLM extraction with checkpointing. Returns list of candidate dicts."""

    pass_id = "tier3_llm"
    if ckpt.is_pass_done(pass_id):
        log(f"  {pass_id}: skipped (checkpoint)", "dim")
        candidates_file = work_dir / "tier3_candidates.jsonl"
        if candidates_file.exists():
            from axm_forge.extraction.schemas import read_jsonl
            return read_jsonl(candidates_file)
        return []

    # Stage 0: Segment
    log(f"\n  [Stage 0] Sentence segmentation...", "stage")
    sentences_path = work_dir / "sentences.jsonl"
    if not sentences_path.exists():
        from axm_forge.extraction.tiers.tier3_segmenter import run_segmentation
        n_sent = run_segmentation(source_path, sentences_path)
        log(f"    {n_sent} sentences", "ok")
    else:
        with sentences_path.open() as f:
            n_sent = sum(1 for _ in f)
        log(f"    {n_sent} sentences (cached)", "dim")

    # Stage 1: LLM extraction
    log(f"\n  [Stage 1] LLM claim extraction ({model})...", "stage")
    raw_claims_path = work_dir / "raw_claims.jsonl"

    # Check if Ollama is reachable
    import urllib.request
    try:
        urllib.request.urlopen(f"{host}/api/tags", timeout=5)
    except Exception:
        log(f"    Ollama not reachable at {host}", "err")
        log(f"    Tier 3 extraction requires a running Ollama instance.", "err")
        log(f"    Run: ollama serve && ollama pull {model}", "err")
        log(f"    Skipping tier 3. Tier 0/1 candidates will be compiled.", "warn")
        return []

    from axm_forge.extraction.tiers.tier3_stage1 import run_stage1
    report = run_stage1(
        sentences_path=sentences_path,
        out_path=raw_claims_path,
        model=model,
        host=host,
        batch_size=batch_size,
        overlap=overlap,
        resume=True,
    )
    log(f"    Stage 1: {report.get('claims', 0)} raw claims from {report.get('batches', 0)} batches", "ok")

    # Stage 2: Binder
    log(f"\n  [Stage 2] Deterministic binder...", "stage")
    tier3_candidates_path = work_dir / "tier3_candidates.jsonl"
    from axm_forge.extraction.tiers.tier3_stage2 import run_stage2
    n_bound = run_stage2(
        source_path=source_path,
        sentences_path=sentences_path,
        raw_claims_path=raw_claims_path,
        out_path=tier3_candidates_path,
    )
    log(f"    {n_bound} bound candidates", "ok")

    ckpt.mark_pass_done(pass_id)

    from axm_forge.extraction.schemas import read_jsonl
    return read_jsonl(tier3_candidates_path)


def merge_candidates(
    tier0_1: List[Candidate],
    tier3: List[Dict[str, Any]],
    out_path: Path,
) -> int:
    """Merge all candidates, deduplicate, write final candidates.jsonl."""
    seen = set()
    evidence_seen = set()  # Track evidence strings to avoid compiler ambiguity
    final = []

    # Add tier 0/1 candidates
    for c in tier0_1:
        key = (c.subject, c.predicate, c.object, c.evidence[:80])
        if key not in seen and c.evidence not in evidence_seen:
            seen.add(key)
            evidence_seen.add(c.evidence)
            final.append(c.to_dict())

    # Add tier 3 candidates
    for c in tier3:
        ev = c.get("evidence", "")
        key = (c.get("subject", ""), c.get("predicate", ""), c.get("object", ""), ev[:80])
        if key not in seen and ev not in evidence_seen:
            seen.add(key)
            evidence_seen.add(ev)
            seen.add(key)
            # Ensure required fields
            d = {
                "subject": c.get("subject", ""),
                "predicate": c.get("predicate", ""),
                "object": c.get("object", ""),
                "object_type": c.get("object_type", "entity"),
                "evidence": ev,
                "tier": c.get("tier", c.get("extraction_tier", 3)),
            }
            if c.get("meta"):
                d["meta"] = c["meta"]
            final.append(d)

    # Validate: remove candidates whose evidence is ambiguous in source text
    source_bytes = Path(out_path).parent.joinpath("source.txt").read_bytes() if Path(out_path).parent.joinpath("source.txt").exists() else None
    if source_bytes:
        validated = []
        dropped_ambiguous = 0
        for d in final:
            ev = d.get("evidence", "")
            if not ev:
                continue
            count = source_bytes.count(ev.encode("utf-8"))
            if count == 1:
                validated.append(d)
            elif count == 0:
                pass  # skip — evidence not found
            else:
                dropped_ambiguous += 1
        if dropped_ambiguous:
            log(f"  Dropped {dropped_ambiguous} candidates with ambiguous evidence", "warn")
        final = validated

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for d in final:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

    return len(final)


def compile_shard(
    source_path: Path,
    candidates_path: Path,
    shard_dir: Path,
    namespace: str,
    title: str,
    suite: str = "axm-blake3-mldsa44",
) -> bool:
    """Compile candidates into a signed Genesis shard."""
    from axm_build.compiler_generic import CompilerConfig, compile_generic_shard
    from axm_build.sign import mldsa44_keygen

    # Generate PQ keypair (or load from config)
    key_dir = shard_dir.parent / "keys"
    key_dir.mkdir(parents=True, exist_ok=True)
    sk_path = key_dir / "publisher.sk"
    pk_path = key_dir / "publisher.pub"

    if sk_path.exists() and pk_path.exists():
        sk = sk_path.read_bytes()
        pk = pk_path.read_bytes()
        log(f"  Using existing keypair from {key_dir}", "dim")
    else:
        if suite == "axm-blake3-mldsa44":
            kp = mldsa44_keygen()
            sk = kp.secret_key
            pk = kp.public_key
        else:
            from nacl.signing import SigningKey
            ed_sk = SigningKey.generate()
            sk = bytes(ed_sk)
            pk = bytes(ed_sk.verify_key)
        sk_path.write_bytes(sk)
        pk_path.write_bytes(pk)
        log(f"  Generated new keypair in {key_dir}", "ok")

    # Build key blob
    if suite == "axm-blake3-mldsa44":
        private_key = sk + pk  # 3840 bytes
    else:
        private_key = sk

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    cfg = CompilerConfig(
        source_path=source_path,
        candidates_path=candidates_path,
        out_dir=shard_dir,
        private_key=private_key,
        publisher_id="@jonathan",
        publisher_name="Jonathan",
        namespace=namespace,
        created_at=now,
        suite=suite,
    )

    return compile_generic_shard(cfg)


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def run_pipeline(
    input_dir: Path,
    output_dir: Path,
    namespace: str = "law/ca-family",
    title: str = "California Family Law Corpus",
    suite: str = "axm-blake3-mldsa44",
    llm_model: str = "llama3:8b",
    llm_host: str = "http://127.0.0.1:11434",
    skip_llm: bool = False,
    plan_only: bool = False,
) -> bool:
    """Full ingestion pipeline: documents → signed shard."""

    # Plan
    plan = plan_job(input_dir, output_dir, llm_model)
    print_plan(plan)

    if plan_only:
        return True

    # Setup work directory
    work_dir = output_dir / ".forge_work"
    work_dir.mkdir(parents=True, exist_ok=True)
    ckpt = CheckpointManager(work_dir)

    # Merge sources
    log("[1/4] Merging and normalizing sources...", "stage")
    source_path = work_dir / "source.txt"
    if not source_path.exists():
        merged_text, offsets = merge_sources(plan.source_files)
        source_path.write_text(merged_text, encoding="utf-8")
        (work_dir / "source_offsets.json").write_text(json.dumps(
            {k: list(v) for k, v in offsets.items()}, indent=2
        ))
        log(f"  {len(plan.source_files)} files → {len(merged_text.encode('utf-8')):,} bytes", "ok")
    else:
        merged_text = source_path.read_text(encoding="utf-8")
        log(f"  source.txt exists ({len(merged_text.encode('utf-8')):,} bytes)", "dim")

    source_bytes = merged_text.encode("utf-8")

    # Tier 0/1 extraction
    log("\n[2/4] Tier 0/1 extraction (deterministic)...", "stage")
    t0 = time.time()
    tier0_1_candidates = run_tier0_tier1(merged_text, source_bytes, work_dir, ckpt)
    dt = time.time() - t0
    log(f"\n  Total tier 0/1: {len(tier0_1_candidates)} candidates ({dt:.2f}s)", "ok")

    # Tier 3 LLM extraction
    tier3_candidates = []
    if not skip_llm:
        log("\n[3/4] Tier 3 LLM extraction...", "stage")
        tier3_candidates = run_tier3_llm(
            source_path=source_path,
            work_dir=work_dir,
            ckpt=ckpt,
            model=llm_model,
            host=llm_host,
        )
        log(f"  Total tier 3: {len(tier3_candidates)} candidates", "ok")
    else:
        log("\n[3/4] Tier 3 LLM extraction: SKIPPED (--skip-llm)", "dim")

    # Merge + compile
    log("\n[4/4] Merging candidates and compiling shard...", "stage")
    candidates_path = work_dir / "candidates.jsonl"
    n_total = merge_candidates(tier0_1_candidates, tier3_candidates, candidates_path)
    log(f"  {n_total} total candidates (deduplicated)", "ok")

    shard_dir = output_dir / "shard"
    if shard_dir.exists():
        shutil.rmtree(shard_dir)

    t0 = time.time()
    try:
        ok = compile_shard(
            source_path=source_path,
            candidates_path=candidates_path,
            shard_dir=shard_dir,
            namespace=namespace,
            title=title,
            suite=suite,
        )
        dt = time.time() - t0
        if ok:
            log(f"\n  ✓ Shard compiled and verified ({dt:.1f}s)", "ok")
            log(f"  Location: {shard_dir}", "ok")

            # Show summary
            manifest = json.loads((shard_dir / "manifest.json").read_text())
            stats = manifest.get("statistics", {})
            log(f"\n  Shard: {manifest.get('metadata', {}).get('title', 'unknown')}", "info")
            log(f"  Suite: {manifest.get('suite', 'ed25519')}", "info")
            log(f"  Entities: {stats.get('entities', '?')}", "info")
            log(f"  Claims:   {stats.get('claims', '?')}", "info")
            log(f"  Merkle:   {manifest.get('integrity', {}).get('merkle_root', '?')[:32]}...", "info")
            return True
        else:
            log(f"\n  ✗ Compilation failed", "err")
            return False
    except Exception as e:
        log(f"\n  ✗ Compilation error: {e}", "err")
        import traceback
        traceback.print_exc()
        return False


def main():
    p = argparse.ArgumentParser(
        description="AXM Forge Runner: documents → signed shard",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Plan only (show what would happen):
  python forge_run.py --input ./legal_docs/ --plan-only

  # Run with tier 0/1 only (no LLM needed):
  python forge_run.py --input ./legal_docs/ --output ./out/legal/ --skip-llm

  # Full run with local Ollama:
  python forge_run.py --input ./legal_docs/ --output ./out/legal/

  # Resume after crash:
  python forge_run.py --input ./legal_docs/ --output ./out/legal/
  (checkpoints are automatic)
        """,
    )
    p.add_argument("--input", required=True, help="Directory of .md/.txt source files")
    p.add_argument("--output", default="./out/forge_output", help="Output directory")
    p.add_argument("--namespace", default="law/ca-family", help="Shard namespace")
    p.add_argument("--title", default="California Family Law Corpus", help="Shard title")
    p.add_argument("--suite", default="axm-blake3-mldsa44",
                    choices=["ed25519", "axm-blake3-mldsa44"], help="Crypto suite")
    p.add_argument("--llm-model", default=None, help="Ollama model name")
    p.add_argument("--llm-host", default=None, help="Ollama host URL")
    p.add_argument("--skip-llm", action="store_true", help="Skip tier 3 LLM extraction")
    p.add_argument("--plan-only", action="store_true", help="Show plan without running")

    args = p.parse_args()

    model = args.llm_model or os.environ.get("AXM_OLLAMA_MODEL", "llama3:8b")
    host = args.llm_host or os.environ.get("AXM_OLLAMA_HOST", "http://127.0.0.1:11434")

    ok = run_pipeline(
        input_dir=Path(args.input),
        output_dir=Path(args.output),
        namespace=args.namespace,
        title=args.title,
        suite=args.suite,
        llm_model=model,
        llm_host=host,
        skip_llm=args.skip_llm,
        plan_only=args.plan_only,
    )

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
