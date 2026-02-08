from __future__ import annotations

from typing import List
from axm_forge.models.types import Chunk, Locator, TextSpan

def chunk_text(doc_id: str, text: str, file_path: str) -> List[Chunk]:
    # v1.0: single-chunk strategy. Replace with real chunking later.
    locator = Locator(kind="txt", file_path=file_path, paragraph_index=0)
    span = TextSpan(artifact="extracted_text", start=0, end=len(text))
    return [Chunk(chunk_id=f"{doc_id}:0", chunk_type="prose", locator=locator, text_span=span, text=text)]
