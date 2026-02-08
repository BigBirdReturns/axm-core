from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Literal

ArtifactKind = Literal["extracted_text", "ocr_text", "native_text"]

@dataclass(frozen=True)
class TextSpan:
    artifact: ArtifactKind
    start: int
    end: int

    def validate(self, text: str) -> None:
        if self.start < 0 or self.end < 0 or self.end < self.start:
            raise ValueError(f"Invalid span: {self.start}-{self.end}")
        if self.end > len(text):
            raise ValueError(f"Span end {self.end} exceeds text length {len(text)}")

LocatorKind = Literal["pdf", "docx", "html", "txt", "pptx", "xlsx", "test"]

@dataclass(frozen=True)
class Locator:
    kind: LocatorKind
    page: Optional[int] = None
    paragraph_index: Optional[int] = None
    file_path: Optional[str] = None
    block_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "page": self.page,
            "paragraph_index": self.paragraph_index,
            "file_path": self.file_path,
            "block_id": self.block_id,
        }

ChunkType = Literal["prose", "table", "list", "heading"]

@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    chunk_type: ChunkType
    locator: Locator
    text_span: TextSpan
    text: str

    def validate(self) -> None:
        self.text_span.validate(self.text)
