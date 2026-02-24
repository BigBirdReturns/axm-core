"""
Spectra Constraint Engine

Evaluates ROE/FSCM/ACM/GUIDANCE constraints against mounted Genesis shards.
Ported from SOCOM axiom-knowledge-core constraint/ module, adapted to
read from DuckDB-mounted shards instead of compiled JSONL packs.

Usage:
    from axiom_runtime.constraint import ConstraintEngine
    engine = ConstraintEngine(spectra_engine)
    decision = engine.evaluate(EvaluateRequest(
        actor="CCDR",
        action="fires",
        target="grid_123456",
        context={"environment": "urban"},
    ))
"""
from .engine import ConstraintEngine
from .types import EvaluateRequest, Decision, DecisionStatus

__all__ = ["ConstraintEngine", "EvaluateRequest", "Decision", "DecisionStatus"]
