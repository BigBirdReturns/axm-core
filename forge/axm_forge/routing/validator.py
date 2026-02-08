"""Validator: Fail-closed contract enforcement.

Validates that emitted files follow the routing contract:
- source.txt is valid UTF-8 with trailing newline
- provenance.jsonl entries are well-formed, contiguous, non-overlapping, and cover all bytes
- candidates.jsonl (if present) references valid byte ranges

v2: Provenance coverage enforcement, overlap detection, fail-closed contiguity.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Dict, Any

from .models import ValidationResult, ProvenanceEntry


def validate_source_txt(path: Path) -> ValidationResult:
    """Validate source.txt file.

    Checks:
    - File exists
    - Valid UTF-8 encoding
    - Ends with newline (error, not warning)
    """
    result = ValidationResult(valid=True)

    if not path.exists():
        result.add_error(f"source.txt not found: {path}")
        return result

    try:
        content = path.read_bytes()
        text = content.decode('utf-8')
    except UnicodeDecodeError as e:
        result.add_error(f"source.txt is not valid UTF-8: {e}")
        return result

    if len(content) == 0:
        result.add_warning("source.txt is empty")
        return result

    if not text.endswith('\n'):
        result.add_error("source.txt does not end with newline (contract requires trailing newline)")

    return result


def validate_provenance_jsonl(path: Path, source_txt_size: int) -> ValidationResult:
    """Validate provenance.jsonl file.

    Checks:
    - File exists
    - All lines are valid JSON
    - Required fields present on every entry
    - No negative or zero-width byte ranges
    - Entries are strictly ordered (ascending byte_start)
    - No overlaps (each entry starts after the previous ends + separator)
    - No gaps (each entry starts exactly at prev_end + 1)
    - Full coverage (last entry's byte_end + 1 == source_txt_size)
    """
    result = ValidationResult(valid=True)

    if not path.exists():
        result.add_error(f"provenance.jsonl not found: {path}")
        return result

    try:
        raw = path.read_text(encoding='utf-8').strip()
    except UnicodeDecodeError as e:
        result.add_error(f"provenance.jsonl is not valid UTF-8: {e}")
        return result

    if not raw:
        result.add_error("provenance.jsonl is empty")
        return result

    lines = raw.split('\n')
    entries: List[Dict[str, Any]] = []

    # Parse all entries
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            entries.append(entry)
        except json.JSONDecodeError as e:
            result.add_error(f"Line {i+1} is not valid JSON: {e}")
            return result

    if not entries:
        result.add_error("No valid provenance entries found")
        return result

    # Validate structure, ordering, contiguity, and coverage
    required_fields = ["byte_start", "byte_end", "page_num", "bbox", "tier", "confidence"]

    for i, entry in enumerate(entries):
        # Check required fields
        for field in required_fields:
            if field not in entry:
                result.add_error(f"Entry {i} missing required field: {field}")

        if not result.valid:
            return result

        byte_start = entry["byte_start"]
        byte_end = entry["byte_end"]

        # Check byte range validity
        if byte_start < 0:
            result.add_error(f"Entry {i} has negative byte_start: {byte_start}")

        if byte_end <= byte_start:
            result.add_error(f"Entry {i} has zero-width or inverted byte range: [{byte_start}, {byte_end})")

        if byte_end > source_txt_size:
            result.add_error(
                f"Entry {i} byte_end ({byte_end}) exceeds source.txt size ({source_txt_size})"
            )

        if not result.valid:
            return result

        # Check ordering and contiguity against previous entry
        if i > 0:
            prev = entries[i - 1]
            prev_end = prev["byte_end"]
            expected_start = prev_end + 1  # +1 for the \n separator

            if byte_start < prev_end:
                # Overlap: this entry claims bytes already claimed by the previous entry
                result.add_error(
                    f"Entry {i} overlaps with entry {i-1}: "
                    f"starts at {byte_start}, but previous ends at {prev_end}"
                )
            elif byte_start < expected_start:
                # Overlap into the separator byte
                result.add_error(
                    f"Entry {i} overlaps separator: "
                    f"starts at {byte_start}, expected {expected_start}"
                )
            elif byte_start > expected_start:
                # Gap: bytes between entries are unaccounted for
                result.add_error(
                    f"Entry {i} has gap: starts at {byte_start}, expected {expected_start}. "
                    f"Bytes [{expected_start}, {byte_start}) have no provenance."
                )
        else:
            # First entry must start at byte 0
            if byte_start != 0:
                result.add_error(
                    f"First entry starts at {byte_start}, expected 0. "
                    f"Bytes [0, {byte_start}) have no provenance."
                )

    if not result.valid:
        return result

    # Coverage check: provenance must account for all bytes in source.txt
    last_entry = entries[-1]
    last_byte_end = last_entry["byte_end"]
    expected_file_end = last_byte_end + 1  # +1 for trailing newline of last segment

    if expected_file_end != source_txt_size:
        result.add_error(
            f"Provenance does not cover entire source.txt: "
            f"last entry ends at byte {last_byte_end}, "
            f"expected file size {expected_file_end}, "
            f"actual file size {source_txt_size}. "
            f"Bytes [{expected_file_end}, {source_txt_size}) have no provenance."
        )

    return result


def validate_candidates_jsonl(path: Path, source_txt_size: int) -> ValidationResult:
    """Validate candidates.jsonl file.

    Checks:
    - File exists (optional, can be empty)
    - All lines are valid JSON
    - Span byte ranges are within source.txt bounds
    """
    result = ValidationResult(valid=True)

    if not path.exists():
        # Candidates file is optional at this stage
        return result

    content = path.read_text(encoding='utf-8').strip()
    if not content:
        # Empty candidates file is allowed
        return result

    lines = content.split('\n')

    for i, line in enumerate(lines):
        if not line.strip():
            continue

        try:
            candidate = json.loads(line)
        except json.JSONDecodeError as e:
            result.add_error(f"Candidate line {i+1} is not valid JSON: {e}")
            continue

        # Check if evidence spans are present and valid
        if "evidence_spans" in candidate:
            for j, span in enumerate(candidate["evidence_spans"]):
                if "byte_start" in span and "byte_end" in span:
                    byte_start = span["byte_start"]
                    byte_end = span["byte_end"]

                    if byte_start < 0:
                        result.add_error(
                            f"Candidate {i+1} span {j} has negative byte_start: {byte_start}"
                        )

                    if byte_end > source_txt_size:
                        result.add_error(
                            f"Candidate {i+1} span {j} byte_end ({byte_end}) "
                            f"exceeds source.txt size ({source_txt_size})"
                        )

                    if byte_end <= byte_start:
                        result.add_error(
                            f"Candidate {i+1} span {j} has zero-width or inverted range: "
                            f"[{byte_start}, {byte_end})"
                        )

    return result


def validate_emission(output_dir: Path) -> ValidationResult:
    """Validate all emitted files.

    Fail-closed: any error in any file makes the entire result invalid.
    """
    result = ValidationResult(valid=True)

    source_txt_path = output_dir / "source.txt"
    provenance_jsonl_path = output_dir / "provenance.jsonl"
    candidates_jsonl_path = output_dir / "candidates.jsonl"

    # Validate source.txt
    source_result = validate_source_txt(source_txt_path)
    result.errors.extend(source_result.errors)
    result.warnings.extend(source_result.warnings)
    if not source_result.valid:
        result.valid = False
        return result

    # Get source.txt size
    source_txt_size = source_txt_path.stat().st_size

    # Validate provenance.jsonl
    provenance_result = validate_provenance_jsonl(provenance_jsonl_path, source_txt_size)
    result.errors.extend(provenance_result.errors)
    result.warnings.extend(provenance_result.warnings)
    if not provenance_result.valid:
        result.valid = False

    # Validate candidates.jsonl (optional)
    candidates_result = validate_candidates_jsonl(candidates_jsonl_path, source_txt_size)
    result.errors.extend(candidates_result.errors)
    result.warnings.extend(candidates_result.warnings)
    if not candidates_result.valid:
        result.valid = False

    return result
