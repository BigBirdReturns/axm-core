"""
axm_forge/chunking/legal.py — Structure-aware legal document chunker.

Replaces the flat-text chunker for legal corpora. Recognizes the structural
roles that matter for claim quality:

    SECTION      — numbered statutory sections (§ 2040, § 3011, etc.)
    VERBATIM     — quoted statutory text (blockquotes, indented blocks)
    DEADLINE     — mandatory deadline / triggering event paragraphs
    SANCTION     — penalty / consequence paragraphs
    CASE         — case citation blocks
    AUTHORITY    — operative authority lists
    HEADING      — section or subsection headings
    PROSE        — general explanatory text

Each chunk carries a Locator with its structural role as `block_id`.
The compiler preserves this via ext/locators@1 so queries can filter
by document role: "show me only VERBATIM statutory text" or
"find all DEADLINE claims in this shard."

Domain hints from CompilerConfig.domain_hints are injected into the
chunk's metadata for downstream tier 2/3 LLM extraction context.

Usage:
    from axm_forge.chunking.legal import chunk_legal_document
    chunks = chunk_legal_document(doc_id, text, file_path, domain_hints="...")
"""
from __future__ import annotations

import re
from typing import List, Optional

from axm_forge.models.types import Chunk, Locator, TextSpan


# ---------------------------------------------------------------------------
# Role detection patterns
# ---------------------------------------------------------------------------

# Statutory section: "Family Code § 2040", "Fam. Code § 271", "§ 2040(a)"
_SECTION_RE = re.compile(
    r'(?:(?:California\s+)?(?:Family\s+Code|Fam\.?\s*Code|Evidence\s+Code|'
    r'Code\s+of\s+Civil\s+Procedure|CCP|Penal\s+Code)\s*)?'
    r'§+\s*\d+(?:\.\d+)?(?:\([a-zA-Z0-9]+\))*',
    re.IGNORECASE,
)

# Mandatory deadline / triggering event paragraphs
_DEADLINE_KW = re.compile(
    r'\b(?:shall\s+(?:serve|file|submit|provide|exchange|disclose)|'
    r'must\s+(?:be\s+filed|be\s+served|file|provide)|'
    r'within\s+\d+\s+days?|'
    r'mandatory\s+deadline|'
    r'triggering\s+event|'
    r'no\s+later\s+than|'
    r'prior\s+to\s+(?:filing|service)|'
    r'upon\s+(?:filing|service|signing))\b',
    re.IGNORECASE,
)

# Sanction / consequence paragraphs
_SANCTION_KW = re.compile(
    r'\b(?:sanctions?|contempt|monetary\s+sanctions?|'
    r'attorney.s?\s+fees?|'
    r'set\s+aside\s+(?:the\s+)?judgment|'
    r'harmless\s+error|'
    r'penalties?|'
    r'consequences?\s+(?:for|of)\s+non-?compliance|'
    r'violation\s+(?:of|results?\s+in)|'
    r'failure\s+to\s+(?:comply|serve|file))\b',
    re.IGNORECASE,
)

# Verbatim statutory text markers
_VERBATIM_RE = re.compile(
    r'^(?:>|\s{4,}|\t)\s*\S',  # blockquote or indented 4+ spaces
    re.MULTILINE,
)
_VERBATIM_LABEL_RE = re.compile(
    r'(?:verbatim\s+statutory\s+text|statutory\s+text:|'
    r'states\s+(?:in\s+)?(?:relevant\s+part|full)[:,])',
    re.IGNORECASE,
)

# Case citation (In re Marriage of X, Montenegro v. Diaz, etc.)
_CASE_RE = re.compile(
    r'(?:In\s+re\s+Marriage\s+of\s+\w+|'
    r'\w+\s+v\.?\s+\w+\s*\(\d{4}\)|'
    r'\*[^*]{5,60}\*\s*\(\d{4}\))',
    re.IGNORECASE,
)

# Operative authority lists
_AUTHORITY_RE = re.compile(
    r'(?:operative\s+(?:phase\s+\d+\s+)?authorities|'
    r'governing\s+statute|'
    r'applicable\s+(?:code|rule|statute)|'
    r'controlling\s+authority)',
    re.IGNORECASE,
)

# Section/subsection headings (markdown ## or bold **text**)
_HEADING_RE = re.compile(
    r'^(#{1,4})\s+.+$|^\*\*[^*]{3,80}\*\*\s*$',
    re.MULTILINE,
)


# ---------------------------------------------------------------------------
# Role classifier
# ---------------------------------------------------------------------------

def _classify_paragraph(text: str) -> str:
    """Return the structural role of a paragraph."""
    stripped = text.strip()

    if _HEADING_RE.match(stripped):
        return "HEADING"

    if _VERBATIM_LABEL_RE.search(stripped):
        return "VERBATIM"

    # Blockquote / deeply indented = verbatim statutory text
    lines = stripped.split("\n")
    indented = sum(1 for ln in lines if ln and (ln.startswith(">") or ln.startswith("    ") or ln.startswith("\t")))
    if indented > 0 and indented >= len([ln for ln in lines if ln.strip()]) * 0.5:
        return "VERBATIM"

    if _SANCTION_KW.search(stripped):
        return "SANCTION"

    if _DEADLINE_KW.search(stripped):
        return "DEADLINE"

    if _AUTHORITY_RE.search(stripped):
        return "AUTHORITY"

    if _CASE_RE.search(stripped):
        return "CASE"

    if _SECTION_RE.search(stripped):
        return "SECTION"

    return "PROSE"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def chunk_legal_document(
    doc_id: str,
    text: str,
    file_path: str,
    *,
    domain_hints: str = "",
    min_chunk_len: int = 40,
) -> List[Chunk]:
    """
    Chunk a legal document into structurally-tagged segments.

    Each chunk's Locator.block_id carries the structural role
    (SECTION, VERBATIM, DEADLINE, SANCTION, CASE, AUTHORITY, HEADING, PROSE).

    Consecutive paragraphs of the same role are merged to avoid
    fragmenting multi-paragraph statutory sections.

    Args:
        doc_id:       Document identifier (used as chunk_id prefix).
        text:         Full normalized document text.
        file_path:    Original filename (for locator).
        domain_hints: Optional domain context (passed through as metadata).
        min_chunk_len: Minimum character length to keep a chunk.

    Returns:
        List[Chunk] ordered by document position.
    """
    chunks: List[Chunk] = []
    paragraphs = _split_paragraphs(text)

    current_role: Optional[str] = None
    current_parts: List[str] = []
    current_start: int = 0
    current_para_idx: int = 0
    char_offset: int = 0

    def _flush(role: str, parts: List[str], start: int, para_idx: int) -> None:
        merged = "\n\n".join(parts).strip()
        if len(merged) < min_chunk_len:
            return
        chunk_id = f"{doc_id}:{para_idx}:{role.lower()}"
        locator = Locator(
            kind="legal",
            file_path=file_path,
            paragraph_index=para_idx,
            block_id=role,
        )
        end = start + len(merged.encode("utf-8"))
        span = TextSpan(artifact="extracted_text", start=start, end=end)
        chunks.append(Chunk(
            chunk_id=chunk_id,
            chunk_type="legal_" + role.lower(),
            locator=locator,
            text_span=span,
            text=merged,
            meta={"domain_hints": domain_hints} if domain_hints else {},
        ))

    for para_idx, (para, para_offset) in enumerate(paragraphs):
        role = _classify_paragraph(para)

        # Headings always flush and stand alone
        if role == "HEADING":
            if current_parts:
                _flush(current_role, current_parts, current_start, current_para_idx)
                current_parts = []
                current_role = None
            _flush("HEADING", [para], para_offset, para_idx)
            char_offset = para_offset + len(para.encode("utf-8"))
            continue

        # VERBATIM and SANCTION always flush surrounding content and stand alone
        # (they're the most structurally significant — don't merge with prose)
        if role in ("VERBATIM", "SANCTION", "DEADLINE"):
            if current_parts:
                _flush(current_role, current_parts, current_start, current_para_idx)
                current_parts = []
                current_role = None
            _flush(role, [para], para_offset, para_idx)
            char_offset = para_offset + len(para.encode("utf-8"))
            continue

        # Merge consecutive paragraphs of same role
        if role == current_role:
            current_parts.append(para)
        else:
            if current_parts:
                _flush(current_role, current_parts, current_start, current_para_idx)
            current_role = role
            current_parts = [para]
            current_start = para_offset
            current_para_idx = para_idx

        char_offset = para_offset + len(para.encode("utf-8"))

    # Flush remainder
    if current_parts:
        _flush(current_role, current_parts, current_start, current_para_idx)

    return chunks


def _split_paragraphs(text: str) -> List[tuple[str, int]]:
    """
    Split text into (paragraph, byte_offset) pairs.
    Paragraphs are separated by one or more blank lines.
    """
    result = []
    current_byte_offset = 0
    parts = re.split(r'\n\s*\n', text)

    for part in parts:
        stripped = part.strip()
        # Find the actual byte offset of this paragraph in the original text
        encoded = stripped.encode("utf-8")
        if stripped:
            # Find offset in original text
            idx = text.find(stripped, current_byte_offset // 1)  # rough char position
            if idx >= 0:
                byte_start = len(text[:idx].encode("utf-8"))
            else:
                byte_start = current_byte_offset
            result.append((stripped, byte_start))

        current_byte_offset += len(part.encode("utf-8")) + 2  # +2 for \n\n separator

    return result
