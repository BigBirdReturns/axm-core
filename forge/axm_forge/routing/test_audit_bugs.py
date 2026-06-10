#!/usr/bin/env python3
"""Audit regression tests for Step 2 routing module.

Each test demonstrates a specific bug found during the audit.
Tests are designed to:
  - FAIL on the original code (proving the bug exists)
  - PASS on the v2 fixed code

Run:
    PYTHONPATH=forge python tests/test_audit_bugs.py
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "forge"))

from axm_forge.routing import Emitter, Segment, BBox, validate_emission
from axm_forge.routing.validator import validate_provenance_jsonl

try:
    from axm_forge.routing.emitter import EmitterError
except ImportError:
    EmitterError = None  # Original code lacks this


PASS = 0
FAIL = 0


def report(name: str, passed: bool, detail: str = ""):
    global PASS, FAIL
    if passed:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f" -- {detail}" if detail else ""))


# =========================================================================
# C1: Provenance coverage gap passes validation
# =========================================================================
def test_c1_coverage_gap():
    """Provenance that covers only part of source.txt must fail validation."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "c1"
        out.mkdir()

        # 10-byte source
        (out / "source.txt").write_bytes(b"AAAA\nBBBB\n")

        # Provenance covers only first 4 bytes
        entries = [
            {
                "byte_start": 0, "byte_end": 4,
                "page_num": 1,
                "bbox": {"x0": 0, "y0": 0, "x1": 1, "y1": 1},
                "tier": 0, "confidence": 1.0,
            },
        ]
        with open(out / "provenance.jsonl", "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
        (out / "candidates.jsonl").write_text("")

        v = validate_emission(out)
        # This MUST be invalid: 6 bytes have no provenance
        report(
            "C1: coverage gap detected",
            not v.valid,
            f"valid={v.valid}, errors={v.errors}"
        )


# =========================================================================
# C2: Overlapping provenance entries are errors, not warnings
# =========================================================================
def test_c2_overlap_is_error():
    """Overlapping provenance entries must produce errors, not warnings."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "c2"
        out.mkdir()

        (out / "source.txt").write_bytes(b"AAAABBBB\n")  # 9 bytes

        entries = [
            {
                "byte_start": 0, "byte_end": 4,
                "page_num": 1,
                "bbox": {"x0": 0, "y0": 0, "x1": 1, "y1": 1},
                "tier": 0, "confidence": 1.0,
            },
            {
                "byte_start": 2, "byte_end": 8,
                "page_num": 1,
                "bbox": {"x0": 0, "y0": 0, "x1": 1, "y1": 1},
                "tier": 0, "confidence": 1.0,
            },
        ]
        with open(out / "provenance.jsonl", "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
        (out / "candidates.jsonl").write_text("")

        v = validate_emission(out)
        # Must be invalid (error, not just warning)
        report(
            "C2: overlap is error not warning",
            not v.valid and len(v.errors) > 0,
            f"valid={v.valid}, errors={v.errors}, warnings={v.warnings}"
        )


# =========================================================================
# C3: Empty segment rejected at emission time
# =========================================================================
def test_c3_empty_segment_guard():
    """Emitting an empty segment must raise EmitterError."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "c3"

        try:
            with Emitter(out) as emitter:
                seg = Segment(
                    text="",
                    bbox=BBox(0, 0, 100, 100, 1),
                    tier=0, confidence=0.9,
                )
                emitter.emit_segment(seg)

            # If we get here, the guard is missing
            report("C3: empty segment rejected", False, "No exception raised")
        except AttributeError as e:
            if "__enter__" in str(e):
                # Old code: no context manager. Test emission without it.
                emitter = Emitter(out)
                emitter.emit_segment(Segment(text="", bbox=BBox(0,0,100,100,1), tier=0, confidence=0.9))
                emitter.close()
                report("C3: empty segment rejected", False, "Old code accepts empty segments")
            else:
                report("C3: empty segment rejected", False, str(e))
        except Exception as e:
            ename = type(e).__name__
            if ename == "EmitterError" or (EmitterError and isinstance(e, EmitterError)):
                report("C3: empty segment rejected", True)
            else:
                report("C3: empty segment rejected", False, f"{ename}: {e}")


# =========================================================================
# C4: process_document returns result (tested via Router if PDF available)
# =========================================================================
def test_c4_process_returns_result():
    """process_document() must return a ProcessResult, not None."""
    fixture = Path(__file__).parent / "fixtures" / "test_native.pdf"
    if not fixture.exists():
        report("C4: process_document returns result", False, "test_native.pdf not found")
        return

    from axm_forge.routing.router import Router
    try:
        from axm_forge.routing.router import ProcessResult
    except ImportError:
        ProcessResult = None

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "c4"

        with Router(fixture) as router:
            try:
                result = router.process_document(out, fail_on_invalid=False)
            except TypeError:
                # Original code doesn't accept fail_on_invalid
                result = router.process_document(out)

        if ProcessResult is not None:
            is_process_result = isinstance(result, ProcessResult)
        else:
            # Original code returns None
            is_process_result = result is not None
        report(
            "C4: process_document returns ProcessResult",
            is_process_result,
            f"got {type(result).__name__}" if not is_process_result else ""
        )

        if is_process_result:
            report(
                "C4: result has valid=True for good PDF",
                result.valid,
                f"errors={result.validation.errors}"
            )


# =========================================================================
# M2: Emitter supports context manager
# =========================================================================
def test_m2_context_manager():
    """Emitter must support 'with' statement."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "m2"

        try:
            with Emitter(out) as emitter:
                seg = Segment(
                    text="test",
                    bbox=BBox(0, 0, 100, 100, 1),
                    tier=0, confidence=0.9,
                )
                emitter.emit_segment(seg)
            # Files should be closed after exiting context
            report("M2: Emitter context manager", True)
        except AttributeError as e:
            report("M2: Emitter context manager", False, str(e))


# =========================================================================
# Baseline: multi-byte UTF-8 still works after fixes
# =========================================================================
def test_baseline_unicode():
    """UTF-8 multi-byte byte accounting must remain correct after fixes."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "unicode"

        with Emitter(out) as emitter:
            texts = ["cafe\u0301", "\u65e5\u672c\u8a9e\u30c6\u30b9\u30c8", "\U0001f9ecDNA"]
            for i, t in enumerate(texts):
                seg = Segment(
                    text=t,
                    bbox=BBox(0, 0, 100, 100, i + 1),
                    tier=0, confidence=0.9,
                )
                emitter.emit_segment(seg)

        raw = (out / "source.txt").read_bytes()
        prov_lines = (out / "provenance.jsonl").read_text().strip().split("\n")

        all_ok = True
        for i, line in enumerate(prov_lines):
            entry = json.loads(line)
            extracted = raw[entry["byte_start"]:entry["byte_end"]].decode("utf-8")
            if extracted != texts[i]:
                all_ok = False

        report("Baseline: UTF-8 byte ranges correct", all_ok)

        v = validate_emission(out)
        report("Baseline: UTF-8 emission validates", v.valid, str(v.errors))


# =========================================================================
# Baseline: happy-path multi-page still works
# =========================================================================
def test_baseline_multipage():
    """Multi-segment emission must produce valid, contiguous provenance."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "multi"

        with Emitter(out) as emitter:
            for i in range(5):
                seg = Segment(
                    text=f"Page {i+1} content here",
                    bbox=BBox(0, 0, 595, 842, i + 1),
                    tier=0, confidence=0.95,
                )
                emitter.emit_segment(seg)

        v = validate_emission(out)
        report("Baseline: 5-page emission validates", v.valid, str(v.errors))


# =========================================================================
# Run all
# =========================================================================
if __name__ == "__main__":
    print("=== Audit Regression Tests ===\n")

    print("[Critical]")
    test_c1_coverage_gap()
    test_c2_overlap_is_error()
    test_c3_empty_segment_guard()
    test_c4_process_returns_result()

    print("\n[Moderate]")
    test_m2_context_manager()

    print("\n[Baselines]")
    test_baseline_unicode()
    test_baseline_multipage()

    print(f"\n{'='*50}")
    print(f"Results: {PASS} passed, {FAIL} failed out of {PASS + FAIL}")
    print(f"{'='*50}")
    sys.exit(0 if FAIL == 0 else 1)
