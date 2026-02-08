import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

class Embedder:
    def __init__(self, *, cache_path: str, provider: str, model: str, base_url: Optional[str] = None) -> None:
        self.cache_path = Path(cache_path)
        self.provider = provider
        self.model = model
        self.base_url = base_url

    def embed(self, text: str) -> List[float]:
        import hashlib
        h = hashlib.sha256(text.encode("utf-8")).digest()
        return [b / 255.0 for b in h[:32]]

class VectorIndex:
    def __init__(self, embedder: Embedder) -> None:
        self._embedder = embedder
        self._rows: List[Tuple[List[float], Dict[str, Any]]] = []

    def size(self) -> int:
        return len(self._rows)

    def index_claims(self, claims: List[Dict[str, Any]]) -> int:
        added = 0
        for c in claims or []:
            text = c.get("text") or c.get("claim") or json.dumps(c, sort_keys=True)
            v = self._embedder.embed(text)
            self._rows.append((v, c))
            added += 1
        return added

    def search(self, query: str, top_k: int = 7) -> List[Dict[str, Any]]:
        qv = self._embedder.embed(query)
        def dot(a, b):
            return sum(x*y for x, y in zip(a, b))
        scored = [(dot(qv, v), row) for v, row in self._rows]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [row for _, row in scored[:max(1, int(top_k))]]
