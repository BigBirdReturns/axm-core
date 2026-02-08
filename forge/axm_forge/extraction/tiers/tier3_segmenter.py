"""
AXM Forge Tier 3 -- Stage 0: Deterministic Sentence Segmenter.

Produces sentences.jsonl with byte-exact spans against source.txt.
Uses pysbd for boundary detection (handles "Dr.", "vol.", "approx.",
decimal numbers, abbreviations common in medical/technical text).

Invariant: for every record emitted,
    source_bytes[byte_start:byte_end].decode("utf-8") == text

pysbd is used ONLY to find where to split.  The actual text in each
record is always sliced from source_bytes, never from pysbd output.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

import pysbd

from axm_forge.extraction.schemas import Segment


def _build_byte_offset_table(text: str) -> List[int]:
    """
    Map character index -> byte offset in UTF-8 encoding.
    Returns list of len(text)+1 entries.  O(n) time, O(n) space.
    """
    table = [0] * (len(text) + 1)
    running = 0
    for i, ch in enumerate(text):
        table[i] = running
        running += len(ch.encode("utf-8"))
    table[len(text)] = running
    return table


def segment_source(source_bytes: bytes) -> List[Segment]:
    """
    Split source bytes into sentence-level Segments with byte-exact spans.

    Returns a list of Segment dataclasses.  Every Segment satisfies:
        source_bytes[seg.byte_start:seg.byte_end].decode("utf-8") == seg.text
    """
    text = source_bytes.decode("utf-8")
    byte_offsets = _build_byte_offset_table(text)

    seg = pysbd.Segmenter(language="en", clean=False)
    sentences = seg.segment(text)

    char_offset = 0
    records: List[Segment] = []

    for sent in sentences:
        # Find this sentence in the original text starting from current position
        start = text.find(sent, char_offset)

        if start == -1:
            # pysbd may have consumed whitespace at boundary; skip past it
            scan = char_offset
            while scan < len(text) and text[scan].isspace():
                scan += 1
            start = text.find(sent, scan)

            if start == -1:
                # Cannot align. Skip this sentence rather than emit wrong spans.
                continue

        end = start + len(sent)

        # O(1) char-to-byte conversion
        b_start = byte_offsets[start]
        b_end = byte_offsets[end]

        # Ground truth: slice from source bytes, not from pysbd output
        actual_text = source_bytes[b_start:b_end].decode("utf-8")

        if actual_text.strip():
            records.append(Segment(
                index=len(records),
                text=actual_text,
                byte_start=b_start,
                byte_end=b_end,
                page=0,
            ))

        char_offset = end

    return records


def run_segmentation(source_path: Path, out_path: Path) -> int:
    """Segment source file and write sentences.jsonl.  Returns count."""
    source_bytes = source_path.read_bytes()
    segments = segment_source(source_bytes)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for s in segments:
            f.write(json.dumps({
                "index": s.index,
                "text": s.text,
                "byte_start": s.byte_start,
                "byte_end": s.byte_end,
                "page": s.page,
            }, ensure_ascii=False) + "\n")

    return len(segments)


def main() -> None:
    import argparse
    p = argparse.ArgumentParser(description="AXM Tier 3 Stage 0: sentence segmentation")
    p.add_argument("--source", required=True, help="Path to source.txt")
    p.add_argument("--out", required=True, help="Path to sentences.jsonl")
    args = p.parse_args()

    n = run_segmentation(Path(args.source), Path(args.out))
    print(json.dumps({"status": "PASS", "sentences": n}))


if __name__ == "__main__":
    main()
