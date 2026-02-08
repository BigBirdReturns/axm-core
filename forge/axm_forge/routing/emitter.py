"""Emitter: Single source of truth for byte emission.

Only the emitter writes bytes to source.txt and provenance.jsonl.
Routers and extractors return Segments. The emitter converts them to bytes.

This enforces the "bytes don't move" invariant.

v2: Context manager support, empty segment guard, proper error propagation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from .models import Segment, ProvenanceEntry, EmissionResult


class EmitterError(Exception):
    """Raised when the emitter encounters an unrecoverable error."""
    pass


class Emitter:
    """Writes segments to source.txt and provenance.jsonl.

    Maintains byte offsets and ensures correct encoding/separators.

    Usage:
        with Emitter(output_dir) as emitter:
            emitter.emit_segment(seg)
            result = emitter.result  # available after __exit__
    """

    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.source_txt_path = self.output_dir / "source.txt"
        self.provenance_jsonl_path = self.output_dir / "provenance.jsonl"

        self.current_byte_offset = 0
        self.provenance_entries: List[ProvenanceEntry] = []
        self.segment_count = 0
        self._closed = False
        self._result: Optional[EmissionResult] = None

        # Open files for writing
        self._source_file = open(self.source_txt_path, 'w', encoding='utf-8')
        self._provenance_file = open(self.provenance_jsonl_path, 'w', encoding='utf-8')

    def __enter__(self) -> "Emitter":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if not self._closed:
            self.close()
        return None  # Do not suppress exceptions

    @property
    def result(self) -> Optional[EmissionResult]:
        """Access the emission result after close()."""
        return self._result

    def emit_segment(self, segment: Segment) -> None:
        """Emit a single segment.

        Writes text to source.txt with trailing newline.
        Records provenance entry with byte range.

        Raises:
            EmitterError: If segment text is empty after stripping.
            RuntimeError: If emitter is already closed.
        """
        if self._closed:
            raise RuntimeError("Cannot emit to a closed Emitter")

        text = segment.text

        # Guard: reject empty segments (they produce zero-width byte ranges)
        if not text.strip():
            raise EmitterError(
                f"Cannot emit empty segment (page {segment.bbox.page_num}). "
                "Empty segments produce invalid provenance entries."
            )

        # Write text + newline to source.txt
        self._source_file.write(text)
        self._source_file.write('\n')

        # Compute byte range (UTF-8)
        text_bytes = text.encode('utf-8')
        newline_bytes = b'\n'

        byte_start = self.current_byte_offset
        byte_end = byte_start + len(text_bytes)  # Excluding trailing newline

        # Create provenance entry
        entry = ProvenanceEntry(
            byte_start=byte_start,
            byte_end=byte_end,
            page_num=segment.bbox.page_num,
            bbox={
                "x0": segment.bbox.x0,
                "y0": segment.bbox.y0,
                "x1": segment.bbox.x1,
                "y1": segment.bbox.y1,
            },
            tier=segment.tier,
            confidence=segment.confidence,
            metadata=segment.metadata,
        )

        # Write provenance entry to JSONL
        self._provenance_file.write(json.dumps(entry.to_dict()))
        self._provenance_file.write('\n')

        # Update state
        self.provenance_entries.append(entry)
        self.current_byte_offset = byte_end + len(newline_bytes)
        self.segment_count += 1

    def emit_segments(self, segments: List[Segment]) -> None:
        """Emit multiple segments in order."""
        for segment in segments:
            self.emit_segment(segment)

    def close(self) -> EmissionResult:
        """Close files and return result.

        Safe to call multiple times; subsequent calls return cached result.
        """
        if self._closed:
            if self._result is not None:
                return self._result
            raise RuntimeError("Emitter closed without result")

        self._source_file.close()
        self._provenance_file.close()
        self._closed = True

        # Placeholder for candidates.jsonl (created by extractors later)
        candidates_jsonl_path = self.output_dir / "candidates.jsonl"
        if not candidates_jsonl_path.exists():
            candidates_jsonl_path.write_text("")

        # Count unique pages
        page_nums = set(entry.page_num for entry in self.provenance_entries)

        self._result = EmissionResult(
            source_txt_path=self.source_txt_path,
            provenance_jsonl_path=self.provenance_jsonl_path,
            candidates_jsonl_path=candidates_jsonl_path,
            total_bytes=self.current_byte_offset,
            segment_count=self.segment_count,
            page_count=len(page_nums),
        )
        return self._result
