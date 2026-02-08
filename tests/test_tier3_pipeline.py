"""
Tests for AXM Tier 3 sentence-based extraction pipeline.

Tests Stage 0 (segmenter), Stage 2 (binder), and doctor_tier3 (validator).
Stage 1 (LLM) is tested via synthetic raw_claims that simulate LLM output.

Run:  python -m pytest tests/test_tier3_pipeline.py -v
  or: python tests/test_tier3_pipeline.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "forge"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from axm_forge.extraction.tiers.tier3_segmenter import segment_source, run_segmentation
from axm_forge.extraction.tiers.tier3_stage1 import _safe_parse_json, _get_resume_point
from axm_forge.extraction.tiers.tier3_stage2 import run_stage2
from scripts.doctor_tier3 import validate_candidates_against_source, run_tier3_doctor


# ============================================================================
# Helpers
# ============================================================================

def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _write_jsonl(path: Path, records: list) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list:
    out = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if s:
                out.append(json.loads(s))
    return out


# ============================================================================
# Stage 0: Segmenter
# ============================================================================

class TestSegmenter:
    """Tests for the pysbd-based sentence segmenter."""

    def test_basic_segmentation(self):
        text = "The drug is effective. It reduces mortality. Patients recover faster."
        source = text.encode("utf-8")
        segs = segment_source(source)

        assert len(segs) == 3
        for s in segs:
            # Ground truth: text sliced from source bytes matches
            assert source[s.byte_start:s.byte_end].decode("utf-8") == s.text

    def test_byte_exact_roundtrip(self):
        """Every segment's byte span must exactly reproduce its text."""
        text = (
            "Tranexamic acid (1g i.v.) was administered at 1.5 mg/kg. "
            "The CRASH-2 protocol (Lancet, 2010; vol. 376, pp. 23-32) showed efficacy. "
            "Dr. Smith reviewed the results."
        )
        source = text.encode("utf-8")
        segs = segment_source(source)

        for s in segs:
            reconstructed = source[s.byte_start:s.byte_end].decode("utf-8")
            assert reconstructed == s.text, (
                f"Segment {s.index} byte mismatch:\n"
                f"  expected: {s.text!r}\n"
                f"  got:      {reconstructed!r}"
            )

    def test_medical_abbreviations(self):
        """pysbd should handle 'Dr.' correctly.  Some abbreviations like 'approx.'
        may still cause splits; the pipeline handles this via multi-sentence IDs.
        The critical invariant is byte-exact spans, not perfect sentence count."""
        text = "Dr. Smith administered the treatment. The results were positive."
        source = text.encode("utf-8")
        segs = segment_source(source)

        assert len(segs) == 2, f"Expected 2 sentences, got {len(segs)}: {[s.text for s in segs]}"
        # "Dr." should not cause a split
        assert "Dr. Smith" in segs[0].text
        # Byte spans still exact regardless
        for s in segs:
            assert source[s.byte_start:s.byte_end].decode("utf-8") == s.text

    def test_unicode_text(self):
        """Byte offsets must be correct for multi-byte UTF-8 characters."""
        text = "Die Behandlung mit Tranexamsaure ist wirksam. Der Patient erholte sich."
        source = text.encode("utf-8")
        segs = segment_source(source)

        for s in segs:
            assert source[s.byte_start:s.byte_end].decode("utf-8") == s.text

    def test_contiguous_indices(self):
        """Segment indices must be 0, 1, 2, ... with no gaps."""
        text = "First. Second. Third. Fourth."
        segs = segment_source(text.encode("utf-8"))
        indices = [s.index for s in segs]
        assert indices == list(range(len(indices)))

    def test_full_coverage(self):
        """Segments should cover all non-whitespace content."""
        text = "Sentence one. Sentence two. Sentence three."
        source = text.encode("utf-8")
        segs = segment_source(source)

        covered = set()
        for s in segs:
            for i in range(s.byte_start, s.byte_end):
                covered.add(i)

        # Every non-whitespace byte should be covered
        for i, b in enumerate(source):
            if chr(b).strip():
                assert i in covered, f"Byte {i} ({chr(b)!r}) not covered by any segment"

    def test_empty_input(self):
        segs = segment_source(b"")
        assert segs == []

    def test_single_sentence(self):
        text = "Just one sentence."
        source = text.encode("utf-8")
        segs = segment_source(source)
        assert len(segs) == 1
        assert segs[0].text == text

    def test_file_roundtrip(self):
        """run_segmentation writes sentences.jsonl that can be read back."""
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "source.txt"
            out = Path(tmp) / "sentences.jsonl"
            _write(src, "First sentence. Second sentence.")

            n = run_segmentation(src, out)
            assert n == 2

            records = _read_jsonl(out)
            assert len(records) == 2
            assert records[0]["index"] == 0
            assert records[1]["index"] == 1


# ============================================================================
# Stage 1: Helpers only (LLM calls can't be tested in sandbox)
# ============================================================================

class TestStage1Helpers:
    """Test Stage 1 utility functions."""

    def test_safe_parse_json_clean(self):
        text = '{"claims": [{"text": "X", "ids": [0]}]}'
        result = _safe_parse_json(text)
        assert result is not None
        assert "claims" in result

    def test_safe_parse_json_wrapped(self):
        text = 'Here is the result: {"claims": [{"text": "X", "ids": [0]}]} hope this helps!'
        result = _safe_parse_json(text)
        assert result is not None
        assert "claims" in result

    def test_safe_parse_json_garbage(self):
        assert _safe_parse_json("not json at all") is None
        assert _safe_parse_json("") is None
        assert _safe_parse_json("{}") is not None  # valid JSON, just no claims key

    def test_safe_parse_json_no_claims_key(self):
        """Fallback parser should reject JSON without 'claims' key."""
        text = 'prefix {"other": "value"} suffix'
        result = _safe_parse_json(text)
        # Direct parse fails, fallback finds braces but no "claims" key
        assert result is None

    def test_resume_point_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "raw.jsonl"
            assert _get_resume_point(p, overlap=5) == 0

    def test_resume_point_with_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "raw.jsonl"
            _write_jsonl(p, [
                {"claim_text": "X", "meta": {"last_sent_idx": 19}},
                {"meta": {"type": "progress", "last_sent_idx": 19}},
                {"claim_text": "Y", "meta": {"last_sent_idx": 34}},
                {"meta": {"type": "progress", "last_sent_idx": 34}},
            ])
            # With overlap=5, resume from 34 - 5 + 1 = 30
            assert _get_resume_point(p, overlap=5) == 30

    def test_resume_point_zero_progress(self):
        """If last_sent_idx is small, don't go negative."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "raw.jsonl"
            _write_jsonl(p, [
                {"meta": {"type": "progress", "last_sent_idx": 2}},
            ])
            # 2 - 5 + 1 = -2, clamped to 0
            assert _get_resume_point(p, overlap=5) == 0


# ============================================================================
# Stage 2: Binder
# ============================================================================

class TestBinder:
    """Tests for the deterministic binder (Stage 2)."""

    def _setup(self, tmp, source_text, sentences, raw_claims):
        """Write test fixtures and return paths."""
        src = Path(tmp) / "source.txt"
        sent = Path(tmp) / "sentences.jsonl"
        raw = Path(tmp) / "raw_claims.jsonl"
        out = Path(tmp) / "candidates.jsonl"

        _write(src, source_text)
        _write_jsonl(sent, sentences)
        _write_jsonl(raw, raw_claims)

        return src, sent, raw, out

    def test_basic_binding(self):
        text = "Aspirin reduces inflammation. It also prevents blood clots."
        source = text.encode("utf-8")

        s0_text = "Aspirin reduces inflammation. "
        s1_text = "It also prevents blood clots."
        s0_end = len(s0_text.encode("utf-8"))
        s1_start = s0_end
        s1_end = s1_start + len(s1_text.encode("utf-8"))

        with tempfile.TemporaryDirectory() as tmp:
            src, sent, raw, out = self._setup(tmp, text,
                sentences=[
                    {"index": 0, "text": s0_text, "byte_start": 0, "byte_end": s0_end, "page": 0},
                    {"index": 1, "text": s1_text, "byte_start": s1_start, "byte_end": s1_end, "page": 0},
                ],
                raw_claims=[
                    {"claim_text": "Aspirin reduces inflammation", "subject": "Aspirin",
                     "predicate": "reduces", "object": "inflammation", "sentence_ids": [0]},
                ],
            )

            report = run_stage2(src, sent, raw, out)
            assert report["emitted"] == 1

            cands = _read_jsonl(out)
            assert len(cands) == 1
            assert cands[0]["byte_start"] == 0
            assert cands[0]["byte_end"] == s0_end
            # Evidence must match source bytes exactly
            assert cands[0]["evidence"] == s0_text

    def test_contiguous_merge(self):
        """Contiguous sentence IDs [0,1] should produce one merged span."""
        text = "The engine overheated. This caused gasket failure."
        source = text.encode("utf-8")

        # Manually compute spans
        s0 = "The engine overheated. "
        s1 = "This caused gasket failure."
        s0_end = len(s0.encode("utf-8"))

        with tempfile.TemporaryDirectory() as tmp:
            src, sent, raw, out = self._setup(tmp, text,
                sentences=[
                    {"index": 0, "text": s0, "byte_start": 0, "byte_end": s0_end, "page": 0},
                    {"index": 1, "text": s1, "byte_start": s0_end, "byte_end": len(source), "page": 0},
                ],
                raw_claims=[
                    {"claim_text": "Engine overheating caused gasket failure",
                     "subject": "overheating", "predicate": "caused", "object": "gasket failure",
                     "sentence_ids": [0, 1]},
                ],
            )

            report = run_stage2(src, sent, raw, out)
            cands = _read_jsonl(out)

            assert len(cands) == 1
            # Merged span covers both sentences
            assert cands[0]["byte_start"] == 0
            assert cands[0]["byte_end"] == len(source)
            assert cands[0]["evidence"] == text

    def test_noncontiguous_split(self):
        """Non-contiguous IDs [0, 2] should produce two separate candidates."""
        text = "Fact A. Unrelated. Fact B."
        source = text.encode("utf-8")

        s0 = "Fact A. "
        s1 = "Unrelated. "
        s2 = "Fact B."
        b0 = len(s0.encode("utf-8"))
        b1 = b0 + len(s1.encode("utf-8"))
        b2 = b1 + len(s2.encode("utf-8"))

        with tempfile.TemporaryDirectory() as tmp:
            src, sent, raw, out = self._setup(tmp, text,
                sentences=[
                    {"index": 0, "text": s0, "byte_start": 0, "byte_end": b0, "page": 0},
                    {"index": 1, "text": s1, "byte_start": b0, "byte_end": b1, "page": 0},
                    {"index": 2, "text": s2, "byte_start": b1, "byte_end": b2, "page": 0},
                ],
                raw_claims=[
                    {"claim_text": "A and B are related", "subject": "A",
                     "predicate": "relates to", "object": "B",
                     "sentence_ids": [0, 2]},
                ],
            )

            report = run_stage2(src, sent, raw, out)
            cands = _read_jsonl(out)

            # Non-contiguous: split into two candidates
            assert len(cands) == 2
            assert cands[0]["byte_end"] == b0
            assert cands[1]["byte_start"] == b1

    def test_content_aware_dedup(self):
        """Same claim + same span from overlapping batches should dedup to one."""
        text = "Aspirin helps."
        source = text.encode("utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            src, sent, raw, out = self._setup(tmp, text,
                sentences=[
                    {"index": 0, "text": text, "byte_start": 0, "byte_end": len(source), "page": 0},
                ],
                raw_claims=[
                    # Same claim appearing in two overlapping batches
                    {"claim_text": "Aspirin helps", "subject": "Aspirin",
                     "predicate": "helps", "object": "patients", "sentence_ids": [0],
                     "meta": {"batch_start": 0, "last_sent_idx": 19}},
                    {"claim_text": "Aspirin helps", "subject": "Aspirin",
                     "predicate": "helps", "object": "patients", "sentence_ids": [0],
                     "meta": {"batch_start": 15, "last_sent_idx": 34}},
                ],
            )

            report = run_stage2(src, sent, raw, out)
            assert report["emitted"] == 1
            assert report["skipped_dedup"] == 1

    def test_different_claims_same_sentence(self):
        """Two different claims from the same sentence should BOTH survive."""
        text = "Aspirin reduces inflammation and prevents blood clots."
        source = text.encode("utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            src, sent, raw, out = self._setup(tmp, text,
                sentences=[
                    {"index": 0, "text": text, "byte_start": 0, "byte_end": len(source), "page": 0},
                ],
                raw_claims=[
                    {"claim_text": "Aspirin reduces inflammation", "subject": "Aspirin",
                     "predicate": "reduces", "object": "inflammation", "sentence_ids": [0]},
                    {"claim_text": "Aspirin prevents blood clots", "subject": "Aspirin",
                     "predicate": "prevents", "object": "blood clots", "sentence_ids": [0]},
                ],
            )

            report = run_stage2(src, sent, raw, out)
            assert report["emitted"] == 2, "Two distinct claims from same sentence must both survive"

    def test_hallucinated_ids_dropped(self):
        """IDs not in sentences.jsonl should be ignored."""
        text = "Only sentence."
        source = text.encode("utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            src, sent, raw, out = self._setup(tmp, text,
                sentences=[
                    {"index": 0, "text": text, "byte_start": 0, "byte_end": len(source), "page": 0},
                ],
                raw_claims=[
                    {"claim_text": "Bogus", "subject": "X", "predicate": "Y", "object": "Z",
                     "sentence_ids": [999]},
                ],
            )

            report = run_stage2(src, sent, raw, out)
            assert report["emitted"] == 0
            assert report["skipped_no_valid_ids"] == 1

    def test_progress_markers_skipped(self):
        """Progress markers from Stage 1 must not produce candidates."""
        text = "Some text."
        source = text.encode("utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            src, sent, raw, out = self._setup(tmp, text,
                sentences=[
                    {"index": 0, "text": text, "byte_start": 0, "byte_end": len(source), "page": 0},
                ],
                raw_claims=[
                    {"meta": {"type": "progress", "last_sent_idx": 19}},
                    {"claim_text": "Some text exists", "subject": "text",
                     "predicate": "exists", "object": "here", "sentence_ids": [0]},
                ],
            )

            report = run_stage2(src, sent, raw, out)
            assert report["skipped_progress"] == 1
            assert report["emitted"] == 1


# ============================================================================
# Doctor / Validator
# ============================================================================

class TestDoctor:
    """Tests for the byte-exact validator."""

    def test_valid_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            text = "Tranexamic acid inhibits fibrinolysis.\n"
            _write(d / "source.txt", text)

            source = text.encode("utf-8")
            ev = "Tranexamic acid inhibits fibrinolysis."
            ev_bytes = ev.encode("utf-8")
            bs = source.find(ev_bytes)

            _write_jsonl(d / "candidates.jsonl", [{
                "subject": "Tranexamic acid", "predicate": "inhibits",
                "object": "fibrinolysis", "evidence": ev,
                "byte_start": bs, "byte_end": bs + len(ev_bytes),
            }])

            r = validate_candidates_against_source(d / "source.txt", d / "candidates.jsonl")
            assert r.ok
            assert r.validated == 1
            assert r.dropped == 0

    def test_mismatched_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _write(d / "source.txt", "The quick brown fox.")
            _write_jsonl(d / "candidates.jsonl", [{
                "subject": "fox", "predicate": "is", "object": "slow",
                "evidence": "The slow brown fox", "byte_start": 0, "byte_end": 18,
            }])

            r = validate_candidates_against_source(d / "source.txt", d / "candidates.jsonl")
            assert not r.ok
            assert "span bytes do not match" in r.errors[0]

    def test_out_of_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _write(d / "source.txt", "Short.")
            _write_jsonl(d / "candidates.jsonl", [{
                "subject": "x", "predicate": "y", "object": "z",
                "evidence": "Short.", "byte_start": 0, "byte_end": 500,
            }])

            r = validate_candidates_against_source(d / "source.txt", d / "candidates.jsonl")
            assert not r.ok

    def test_empty_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _write(d / "source.txt", "Some text.")
            _write_jsonl(d / "candidates.jsonl", [{
                "subject": "x", "predicate": "y", "object": "z",
                "evidence": "", "byte_start": 0, "byte_end": 4,
            }])

            r = validate_candidates_against_source(d / "source.txt", d / "candidates.jsonl")
            assert not r.ok

    def test_ambiguity_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            text = "The drug works. Studies confirm the drug works in practice."
            _write(d / "source.txt", text)

            source = text.encode("utf-8")
            ev = "drug works"
            ev_bytes = ev.encode("utf-8")
            bs = source.find(ev_bytes)

            _write_jsonl(d / "candidates.jsonl", [{
                "subject": "drug", "predicate": "works", "object": "yes",
                "evidence": ev, "byte_start": bs, "byte_end": bs + len(ev_bytes),
            }])

            r = validate_candidates_against_source(d / "source.txt", d / "candidates.jsonl")
            assert r.ok
            assert r.ambiguous_spans == 1
            assert r.ambiguity_rate == 1.0


# ============================================================================
# End-to-End: Segmenter -> Synthetic Claims -> Binder -> Doctor
# ============================================================================

class TestEndToEnd:
    """Full pipeline test: Stage 0 -> synthetic Stage 1 -> Stage 2 -> Doctor."""

    def test_full_pipeline(self):
        source_text = (
            "Tranexamic acid is a synthetic derivative of lysine. "
            "It inhibits fibrinolysis by blocking lysine binding sites on plasminogen. "
            "The CRASH-2 trial demonstrated a significant reduction in mortality. "
            "Administration within 3 hours of injury showed the greatest benefit."
        )

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            src_path = d / "source.txt"
            sent_path = d / "sentences.jsonl"
            raw_path = d / "raw_claims.jsonl"
            cand_path = d / "candidates.jsonl"

            _write(src_path, source_text)

            # Stage 0: Segment
            n = run_segmentation(src_path, sent_path)
            assert n >= 3, f"Expected >=3 sentences, got {n}"

            # Verify segmenter output
            sents = _read_jsonl(sent_path)
            source_bytes = source_text.encode("utf-8")
            for s in sents:
                actual = source_bytes[s["byte_start"]:s["byte_end"]].decode("utf-8")
                assert actual == s["text"], f"Segment {s['index']} byte mismatch"

            # Stage 1: Simulate LLM output (synthetic raw_claims)
            raw_claims = [
                {
                    "claim_text": "Tranexamic acid is a synthetic derivative of lysine",
                    "subject": "Tranexamic acid",
                    "predicate": "is derivative of",
                    "object": "lysine",
                    "sentence_ids": [0],
                    "meta": {"batch_start": 0, "last_sent_idx": n - 1},
                },
                {
                    "claim_text": "Tranexamic acid inhibits fibrinolysis by blocking lysine binding sites",
                    "subject": "Tranexamic acid",
                    "predicate": "inhibits",
                    "object": "fibrinolysis",
                    "sentence_ids": [1],
                    "meta": {"batch_start": 0, "last_sent_idx": n - 1},
                },
                {
                    "claim_text": "CRASH-2 trial showed TXA reduces mortality when given within 3 hours",
                    "subject": "CRASH-2 trial",
                    "predicate": "demonstrated",
                    "object": "mortality reduction with early TXA",
                    "sentence_ids": [2, 3],  # Multi-sentence claim
                    "meta": {"batch_start": 0, "last_sent_idx": n - 1},
                },
                # Progress marker (must be skipped by binder)
                {"meta": {"type": "progress", "last_sent_idx": n - 1}},
            ]
            _write_jsonl(raw_path, raw_claims)

            # Stage 2: Bind
            report = run_stage2(src_path, sent_path, raw_path, cand_path)
            assert report["status"] == "PASS"
            assert report["emitted"] == 3
            assert report["skipped_progress"] == 1

            # Doctor: Validate
            vr = validate_candidates_against_source(src_path, cand_path)
            assert vr.ok, f"Doctor failed: {vr.errors}"
            assert vr.validated == 3
            assert vr.dropped == 0

            # Verify Genesis compatibility: every candidate has required fields
            cands = _read_jsonl(cand_path)
            for c in cands:
                assert "subject" in c
                assert "predicate" in c
                assert "object" in c
                assert "evidence" in c
                assert isinstance(c["byte_start"], int)
                assert isinstance(c["byte_end"], int)
                # Byte-exact: evidence matches source slice
                ev = c["evidence"].encode("utf-8")
                actual = source_bytes[c["byte_start"]:c["byte_end"]]
                assert ev == actual

    def test_pipeline_with_unicode(self):
        """Pipeline handles multi-byte UTF-8 correctly end-to-end."""
        source_text = (
            "Die Behandlung mit Tranexamsaure reduziert die Sterblichkeit. "
            "Patienten erholen sich schneller nach der Behandlung."
        )

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            src_path = d / "source.txt"
            sent_path = d / "sentences.jsonl"
            raw_path = d / "raw_claims.jsonl"
            cand_path = d / "candidates.jsonl"

            _write(src_path, source_text)
            n = run_segmentation(src_path, sent_path)
            assert n >= 2

            sents = _read_jsonl(sent_path)
            _write_jsonl(raw_path, [
                {
                    "claim_text": "Tranexamsaure reduces mortality",
                    "subject": "Tranexamsaure", "predicate": "reduziert",
                    "object": "Sterblichkeit", "sentence_ids": [0],
                    "meta": {"last_sent_idx": n - 1},
                },
                {"meta": {"type": "progress", "last_sent_idx": n - 1}},
            ])

            report = run_stage2(src_path, sent_path, raw_path, cand_path)
            assert report["emitted"] == 1

            vr = validate_candidates_against_source(src_path, cand_path)
            assert vr.ok, f"Unicode validation failed: {vr.errors}"


# ============================================================================
# Runner
# ============================================================================

def _run_all():
    """Simple test runner (no pytest dependency)."""
    import traceback
    classes = [TestSegmenter, TestStage1Helpers, TestBinder, TestDoctor, TestEndToEnd]
    passed = 0
    failed = 0
    errors = []

    for cls in classes:
        instance = cls()
        methods = [m for m in dir(instance) if m.startswith("test_")]
        for method_name in sorted(methods):
            name = f"{cls.__name__}.{method_name}"
            try:
                getattr(instance, method_name)()
                print(f"  PASS  {name}")
                passed += 1
            except Exception as exc:
                print(f"  FAIL  {name}: {exc}")
                errors.append((name, traceback.format_exc()))
                failed += 1

    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed")

    if errors:
        print(f"\nFailure details:")
        for name, tb in errors:
            print(f"\n--- {name} ---")
            print(tb)

    return failed == 0


if __name__ == "__main__":
    ok = _run_all()
    raise SystemExit(0 if ok else 1)
