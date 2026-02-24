"""Constraint types — ported from SOCOM constraint/types.py."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class DecisionStatus(str, Enum):
    PERMIT = "permit"
    DENY = "deny"
    CONDITIONAL = "conditional"


class ConstraintType(str, Enum):
    ROE = "ROE"
    FSCM = "FSCM"
    ACM = "ACM"
    WCS = "WCS"
    FPCON = "FPCON"
    EMCON = "EMCON"
    JRFL = "JRFL"
    GUIDANCE = "GUIDANCE"
    UNKNOWN = "UNKNOWN"


# Type precedence order (ROE overrides FSCM overrides ACM, etc.)
TYPE_PRECEDENCE = ["ROE", "FSCM", "ACM", "WCS", "FPCON", "EMCON", "JRFL", "GUIDANCE"]

AUTHORITY_HIERARCHY = ["SECDEF", "CJCS", "CCDR", "JFC", "COMPONENT", "SUBORDINATE"]

DENY_BY_DEFAULT = True


@dataclass
class EvaluateRequest:
    actor: str
    action: str
    target: str
    context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ConstraintMatch:
    constraint_id: str
    constraint_type: ConstraintType
    match_reason: str
    weight: float = 1.0


@dataclass
class ProvenanceTrace:
    prov_id: str
    source_path: str
    source_sha256: str
    locator: Dict[str, Any] = field(default_factory=dict)
    note: Optional[str] = None


@dataclass
class Decision:
    status: DecisionStatus
    controlling_constraint: Optional[str]
    evaluated_constraints: List[ConstraintMatch] = field(default_factory=list)
    authority_chain: List[str] = field(default_factory=list)
    conditions: List[Dict[str, Any]] = field(default_factory=list)
    confidence: Optional[float] = None
    provenance: List[ProvenanceTrace] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "controlling_constraint": self.controlling_constraint,
            "evaluated_constraints": [
                {"id": c.constraint_id, "type": c.constraint_type.value,
                 "reason": c.match_reason, "weight": c.weight}
                for c in self.evaluated_constraints
            ],
            "authority_chain": self.authority_chain,
            "conditions": self.conditions,
            "confidence": self.confidence,
            "notes": self.notes,
        }
