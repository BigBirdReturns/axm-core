"""Router: Orchestrates document processing.

The router:
1. Computes signals for each page
2. Makes routing decisions (which tier/extractor to use)
3. Calls appropriate extractors
4. Emits segments via the emitter
5. Validates output and FAILS CLOSED on invalid emission

v2: Returns results, context manager support, raises on validation failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

try:
    import pymupdf  # type: ignore
except ImportError:
    pymupdf = None

from .models import Segment, BBox, RegionDecision, PageSignals, EmissionResult, ValidationResult
from .signals import compute_page_signals, should_use_ocr
from .emitter import Emitter
from .validator import validate_emission


class RoutingError(Exception):
    """Raised when document routing or emission fails validation."""
    pass


@dataclass
class ProcessResult:
    """Result of process_document(), providing both emission stats and validation."""
    emission: EmissionResult
    validation: ValidationResult

    @property
    def valid(self) -> bool:
        return self.validation.valid


class Router:
    """Routes document pages to appropriate extractors.

    Usage:
        with Router(path) as router:
            signals = router.analyze_document()
            result = router.process_document(output_dir)
            if not result.valid:
                handle_failure(result.validation.errors)
    """

    def __init__(self, doc_path: Path):
        self.doc_path = Path(doc_path)

        if pymupdf is None:
            raise ImportError("PyMuPDF is required for routing. Install with: pip install pymupdf")

        self.doc = pymupdf.open(str(self.doc_path))
        self.page_signals: List[PageSignals] = []

    def __enter__(self) -> "Router":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
        return None

    def analyze_document(self) -> List[PageSignals]:
        """Compute signals for all pages.

        Returns:
            List of PageSignals, one per page
        """
        self.page_signals = []

        for page in self.doc:
            signals = compute_page_signals(page)
            self.page_signals.append(signals)

        return self.page_signals

    def route_page(self, page_num: int) -> List[RegionDecision]:
        """Make routing decisions for a page.

        Args:
            page_num: Page number (1-indexed)

        Returns:
            List of RegionDecisions covering the entire page
        """
        if not self.page_signals:
            self.analyze_document()

        signals = self.page_signals[page_num - 1]
        page = self.doc[page_num - 1]

        # Get page bounds
        rect = page.rect
        bbox = BBox(
            x0=rect.x0,
            y0=rect.y0,
            x1=rect.x1,
            y1=rect.y1,
            page_num=page_num,
        )

        # Make routing decision based on signals
        if should_use_ocr(signals):
            decision = RegionDecision(
                bbox=bbox,
                tier=2,  # OCR
                confidence=1.0 - signals.confidence_tier0,
                reason=f"Low native text confidence ({signals.confidence_tier0:.2f}), using OCR"
            )
        else:
            decision = RegionDecision(
                bbox=bbox,
                tier=0,  # Native text
                confidence=signals.confidence_tier0,
                reason=f"High native text confidence ({signals.confidence_tier0:.2f})"
            )

        return [decision]

    def extract_native_text(self, page_num: int) -> List[Segment]:
        """Extract native text from a page.

        Args:
            page_num: Page number (1-indexed)

        Returns:
            List of Segments with extracted text
        """
        page = self.doc[page_num - 1]

        # Get full page text
        text = page.get_text()

        # Get page bounds for bbox
        rect = page.rect
        bbox = BBox(
            x0=rect.x0,
            y0=rect.y0,
            x1=rect.x1,
            y1=rect.y1,
            page_num=page_num,
        )

        # Get confidence from signals
        if not self.page_signals:
            self.analyze_document()

        signals = self.page_signals[page_num - 1]

        stripped = text.strip()
        if not stripped:
            # No extractable text on this page; return empty list
            # (the emitter would reject an empty segment anyway)
            return []

        # Create segment
        segment = Segment(
            text=stripped,
            bbox=bbox,
            tier=0,
            confidence=signals.confidence_tier0,
            metadata={
                "extraction_method": "pymupdf_get_text",
                "char_count": signals.char_count,
                "text_density": signals.text_density,
            }
        )

        return [segment]

    def process_document(
        self,
        output_dir: Path,
        *,
        fail_on_invalid: bool = True,
    ) -> ProcessResult:
        """Process entire document and emit to output directory.

        Args:
            output_dir: Directory to write source.txt, provenance.jsonl, candidates.jsonl
            fail_on_invalid: If True, raise RoutingError when validation fails

        Returns:
            ProcessResult with emission stats and validation result

        Raises:
            RoutingError: If fail_on_invalid is True and validation fails
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Analyze document first
        self.analyze_document()

        # Use emitter as context manager for safe cleanup
        with Emitter(output_dir) as emitter:
            # Process each page
            for page_num in range(1, len(self.doc) + 1):
                decisions = self.route_page(page_num)

                for decision in decisions:
                    if decision.tier == 0:
                        segments = self.extract_native_text(page_num)
                    elif decision.tier == 2:
                        # OCR not implemented yet; fall back to native with warning
                        segments = self.extract_native_text(page_num)
                        for seg in segments:
                            seg.metadata["fallback_reason"] = "OCR not implemented yet"
                    else:
                        segments = []

                    emitter.emit_segments(segments)

            emission_result = emitter.close()

        # Validate output
        validation = validate_emission(output_dir)

        result = ProcessResult(emission=emission_result, validation=validation)

        if not validation.valid and fail_on_invalid:
            error_summary = "; ".join(validation.errors)
            raise RoutingError(
                f"Emission validation failed ({len(validation.errors)} errors): {error_summary}"
            )

        return result

    def close(self) -> None:
        """Close the document."""
        if self.doc:
            self.doc.close()
