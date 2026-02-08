from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple
import hashlib

@dataclass(frozen=True)
class IngestedDoc:
    doc_id: str
    path: Path
    extracted_text: str
    metadata: dict

def ingest_paths(paths: List[Path]) -> List[IngestedDoc]:
    docs: List[IngestedDoc] = []
    for p in paths:
        text = p.read_text(encoding="utf-8", errors="ignore")
        h = hashlib.blake2b(digest_size=10)
        h.update(str(p).encode("utf-8"))
        h.update(b"|")
        h.update(text[:2048].encode("utf-8", errors="ignore"))
        doc_id = f"doc:{h.hexdigest()}"
        docs.append(IngestedDoc(doc_id=doc_id, path=p, extracted_text=text, metadata={"file_name": p.name}))
    return docs
