"""Routing models for document processing.

Data structures for the routing contract:
- Segment: A piece of text with its bbox and metadata
- RegionDecision: Router's decision for a page region
- EmissionResult: Final outputs (source.txt, provenance.jsonl, candidates.jsonl)
- ValidationResult: Contract validation result
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Dict, Any


@dataclass
class BBox:
    """Bounding box in page coordinates."""
    x0: float
    y0: float
    x1: float
    y1: float
    page_num: int
    
    @property
    def width(self) -> float:
        return self.x1 - self.x0
    
    @property
    def height(self) -> float:
        return self.y1 - self.y0
    
    @property
    def area(self) -> float:
        return self.width * self.height


@dataclass
class Segment:
    """A text segment with provenance.
    
    This is what routers and extractors return.
    Only the emitter converts segments into bytes.
    """
    text: str
    bbox: BBox
    tier: int  # 0=native, 1=table, 2=OCR, 3=LLM
    confidence: float  # 0.0 to 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def byte_length_utf8(self) -> int:
        """Compute UTF-8 byte length of this segment's text."""
        return len(self.text.encode('utf-8'))


@dataclass
class RegionDecision:
    """Router's decision for a page region."""
    bbox: BBox
    tier: int
    confidence: float
    reason: str  # Human-readable explanation


@dataclass
class ProvenanceEntry:
    """One entry in provenance.jsonl.
    
    Maps a byte range in source.txt to its origin.
    """
    byte_start: int
    byte_end: int
    page_num: int
    bbox: Dict[str, float]  # x0, y0, x1, y1
    tier: int
    confidence: float
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for JSON serialization."""
        return {
            "byte_start": self.byte_start,
            "byte_end": self.byte_end,
            "page_num": self.page_num,
            "bbox": self.bbox,
            "tier": self.tier,
            "confidence": self.confidence,
            "metadata": self.metadata,
        }


@dataclass
class EmissionResult:
    """Result of document emission."""
    source_txt_path: Path
    provenance_jsonl_path: Path
    candidates_jsonl_path: Path
    total_bytes: int
    segment_count: int
    page_count: int


@dataclass
class ValidationResult:
    """Result of contract validation."""
    valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    
    def add_error(self, msg: str) -> None:
        self.valid = False
        self.errors.append(msg)
    
    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)


@dataclass
class PageSignals:
    """Signals computed for a page to guide routing decisions."""
    page_num: int
    text_density: float  # characters per unit area
    has_native_text: bool  # PDF has selectable text layer
    char_count: int
    page_area: float
    confidence_tier0: float  # Confidence for native text extraction
    
    def __str__(self) -> str:
        return (
            f"Page {self.page_num}: "
            f"density={self.text_density:.3f}, "
            f"native={self.has_native_text}, "
            f"chars={self.char_count}, "
            f"conf={self.confidence_tier0:.2f}"
        )
