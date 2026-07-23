"""Canonical source text shared by Forge structured adapters and Genesis v1."""
from __future__ import annotations

import re
import unicodedata


def canonical_source_text(text: str) -> str:
    """Mirror the frozen Genesis v1 source normalization without importing it."""
    text = unicodedata.normalize("NFC", text)
    lines = []
    for line in text.splitlines():
        lines.append(re.sub(r"\s+", " ", line.rstrip()))
    result = "\n".join(lines)
    return result if result.endswith("\n") else result + "\n"
