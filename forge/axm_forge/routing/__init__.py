"""Routing module for tier-based document processing.

The router analyzes documents and makes tier decisions:
- Tier 0: Native text extraction (PDFs with selectable text)
- Tier 1: Table extraction (structured data)
- Tier 2: OCR (scanned documents)
- Tier 3: LLM extraction (complex layout/handwriting)

The routing contract ensures byte stability and provenance alignment.
"""

from .models import (
    BBox,
    Segment,
    RegionDecision,
    ProvenanceEntry,
    EmissionResult,
    ValidationResult,
    PageSignals,
)

from .signals import (
    measure_text_density,
    has_native_text_layer,
    compute_tier0_confidence,
    compute_page_signals,
    should_use_ocr,
)

from .emitter import Emitter
from .validator import validate_emission
from .router import Router

__all__ = [
    # Models
    "BBox",
    "Segment",
    "RegionDecision",
    "ProvenanceEntry",
    "EmissionResult",
    "ValidationResult",
    "PageSignals",
    # Signals
    "measure_text_density",
    "has_native_text_layer",
    "compute_tier0_confidence",
    "compute_page_signals",
    "should_use_ocr",
    # Emitter
    "Emitter",
    # Validator
    "validate_emission",
    # Router
    "Router",
]
