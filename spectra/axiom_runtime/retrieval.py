"""
axiom_runtime.retrieval — Embedding and vector search for Spectra.

Stub implementation. Provides Embedder and VectorIndex so that
SpectraEngine can import without error. These are used for semantic
search over mounted shard content when available.

The current query path (NL -> SQL via nlquery.py) works without
embeddings. This module enables an optional similarity-search layer
for queries that don't map well to keyword SQL.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


class Embedder:
    """Compute text embeddings for semantic search.

    Stub that returns zero vectors. Replace with a real embedding
    model (e.g., sentence-transformers via Ollama or local ONNX)
    when semantic retrieval is needed.
    """

    def __init__(
        self,
        *,
        model: str = "stub",
        dim: int = 384,
        cache_path: Optional[str] = None,
    ) -> None:
        self._model = model
        self._dim = dim
        self._cache_path = cache_path
        self._ready = False

    def embed(self, text: str) -> List[float]:
        """Return an embedding vector for the given text."""
        # Stub: return zero vector
        return [0.0] * self._dim

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Return embedding vectors for a batch of texts."""
        return [self.embed(t) for t in texts]

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def is_ready(self) -> bool:
        return self._ready


class VectorIndex:
    """In-memory vector index for nearest-neighbor search over claims.

    Stub implementation that returns empty results. When a real
    Embedder is provided, this builds a brute-force or HNSW index
    over claim evidence text for semantic retrieval.
    """

    def __init__(self, embedder: Embedder) -> None:
        self._embedder = embedder
        self._vectors: List[Tuple[str, List[float]]] = []
        self._metadata: Dict[str, Any] = {}

    def add(self, claim_id: str, text: str, metadata: Optional[Dict] = None) -> None:
        """Add a claim's evidence text to the index."""
        vec = self._embedder.embed(text)
        self._vectors.append((claim_id, vec))
        if metadata:
            self._metadata[claim_id] = metadata

    def search(self, query: str, top_k: int = 10) -> List[Tuple[str, float]]:
        """Return the top-k most similar claim IDs with scores.

        Stub: returns empty list. Real implementation would compute
        cosine similarity against all indexed vectors.
        """
        return []

    def clear(self) -> None:
        """Remove all indexed vectors."""
        self._vectors.clear()
        self._metadata.clear()

    @property
    def size(self) -> int:
        return len(self._vectors)
