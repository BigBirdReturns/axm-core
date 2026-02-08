from typing import Any, Dict, List, Optional

from .retrieval import VectorIndex

class ChatEngine:
    def __init__(self, index: VectorIndex, provider: str, model: str, base_url: Optional[str] = None) -> None:
        self._index = index
        self.provider = provider
        self.model = model
        self.base_url = base_url

    def ask(self, question: str, top_k: int = 7) -> Dict[str, Any]:
        hits = self._index.search(question, top_k=top_k)
        citations: List[Dict[str, Any]] = []
        for h in hits:
            citations.append({
                "doc_id": h.get("doc_id"),
                "claim_id": h.get("claim_id"),
                "text": h.get("text") or h.get("claim")
            })
        return {
            "answer": "Spectra dev chat stub. Configure a real provider for generation.",
            "citations": citations,
        }
