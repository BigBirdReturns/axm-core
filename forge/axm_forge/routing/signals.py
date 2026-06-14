"""Signal extraction for routing decisions.

Functions to measure document properties that guide tier routing:
- Text density (characters per unit area)
- Native text layer presence
- Confidence scoring for Tier 0 (native text extraction)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    import pymupdf  # type: ignore
    from .models import PageSignals


def measure_text_density(page: "pymupdf.Page") -> float:
    """Measure text density as characters per unit area.
    
    Args:
        page: PyMuPDF page object
        
    Returns:
        Characters per square point. Higher values indicate text-heavy pages.
        Typical values:
        - Dense text (books, articles): 0.01 - 0.05
        - Normal documents: 0.005 - 0.01
        - Sparse text (forms, diagrams): 0.001 - 0.005
        - No text: 0.0
    """
    # Get page dimensions
    rect = page.rect
    page_area = rect.width * rect.height
    
    if page_area == 0:
        return 0.0
    
    # Extract text
    text = page.get_text()
    char_count = len(text.strip())
    
    if char_count == 0:
        return 0.0
    
    # Compute density
    density = char_count / page_area
    return density


def has_native_text_layer(page: "pymupdf.Page") -> bool:
    """Check if page has a native (selectable) text layer.
    
    Args:
        page: PyMuPDF page object
        
    Returns:
        True if page has selectable text, False if it's an image-only scan.
        
    Strategy:
        1. Try to extract text with get_text()
        2. Check if the text is substantial (>20 chars after stripping whitespace)
        3. Check text blocks to see if they have font information
    """
    # Strategy 1: Check raw text length
    text = page.get_text().strip()
    if len(text) < 20:
        # Very little text, likely a scan or image-heavy page
        return False
    
    # Strategy 2: Check if text blocks have font info
    # Native text PDFs have font metadata; scanned PDFs don't
    blocks = page.get_text("dict")["blocks"]
    text_blocks_with_font = 0
    
    for block in blocks:
        if block.get("type") == 0:  # Text block
            lines = block.get("lines", [])
            for line in lines:
                spans = line.get("spans", [])
                for span in spans:
                    if "font" in span and span.get("font"):
                        text_blocks_with_font += 1
                        break
    
    # If we found text blocks with font info, it's native
    return text_blocks_with_font > 0


def compute_tier0_confidence(
    text_density: float,
    has_native: bool,
    char_count: int,
) -> float:
    """Compute confidence score for Tier 0 (native text extraction).
    
    Args:
        text_density: Characters per unit area
        has_native: Whether page has native text layer
        char_count: Total character count on page
        
    Returns:
        Confidence score from 0.0 to 1.0
        
    Decision logic:
        - No native text → 0.0 (must use OCR)
        - Native text + high density → 0.95+ (very confident)
        - Native text + medium density → 0.7-0.9 (confident)
        - Native text + low density → 0.5-0.7 (use native but flag for review)
    """
    if not has_native:
        return 0.0
    
    if char_count < 10:
        # Almost no text, even if native layer exists
        return 0.3
    
    # Scale confidence based on text density
    # Dense text (0.01+) → high confidence
    # Medium text (0.005-0.01) → medium confidence
    # Sparse text (<0.005) → lower confidence
    
    if text_density >= 0.01:
        return 0.95
    elif text_density >= 0.005:
        return 0.80
    elif text_density >= 0.002:
        return 0.65
    else:
        return 0.50


def compute_page_signals(page: "pymupdf.Page") -> "PageSignals":
    """Compute all signals for a page.
    
    Args:
        page: PyMuPDF page object
        
    Returns:
        PageSignals with all computed metrics
    """
    from .models import PageSignals
    
    # Get page dimensions
    rect = page.rect
    page_area = rect.width * rect.height
    
    # Extract text
    text = page.get_text()
    char_count = len(text.strip())
    
    # Compute signals
    text_density = measure_text_density(page)
    has_native = has_native_text_layer(page)
    confidence = compute_tier0_confidence(text_density, has_native, char_count)
    
    return PageSignals(
        page_num=page.number + 1,  # PyMuPDF uses 0-based indexing
        text_density=text_density,
        has_native_text=has_native,
        char_count=char_count,
        page_area=page_area,
        confidence_tier0=confidence,
    )


def should_use_ocr(signals: "PageSignals", threshold: float = 0.5) -> bool:
    """Determine if OCR should be used instead of native extraction.
    
    Args:
        signals: Computed page signals
        threshold: Confidence threshold below which to use OCR
        
    Returns:
        True if OCR should be used, False if native extraction is sufficient
    """
    return signals.confidence_tier0 < threshold
