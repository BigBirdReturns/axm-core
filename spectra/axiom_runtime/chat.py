"""
axiom_runtime.chat — Chat interface for Spectra.

Stub implementation. Provides the ChatEngine class so that
SpectraEngine can import without error. Full implementation
(conversation history, multi-turn context) is planned.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


class ChatEngine:
    """Conversational wrapper around SpectraEngine queries.

    Maintains a session history and translates natural-language
    follow-ups into scoped SQL queries.
    """

    def __init__(
        self,
        *,
        engine: Any = None,
        max_history: int = 20,
    ) -> None:
        self._engine = engine
        self._max_history = max_history
        self._history: List[Dict[str, str]] = []

    def ask(self, question: str) -> Dict[str, Any]:
        """Process a natural-language question and return results.

        Falls back to the engine's query_json if available,
        otherwise returns an empty result.
        """
        self._history.append({"role": "user", "content": question})
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        if self._engine is not None:
            try:
                from .nlquery import natural_language_to_sql
                sql = natural_language_to_sql(question)
                result = self._engine.query_json(sql)
                self._history.append({"role": "assistant", "content": sql})
                return result
            except Exception as e:
                return {"error": str(e), "columns": [], "rows": []}

        return {"error": "No engine attached", "columns": [], "rows": []}

    def clear_history(self) -> None:
        self._history.clear()

    @property
    def history(self) -> List[Dict[str, str]]:
        return list(self._history)
