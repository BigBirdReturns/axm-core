"""
Constraint Engine

Evaluates ROE/FSCM/ACM/GUIDANCE constraints from mounted Genesis shards.

Key difference from SOCOM original: data source is Spectra-mounted DuckDB
views (claims.parquet, entities.parquet) instead of compiled JSONL packs.
The logic (precedence, authority chain, revocation) is ported unchanged.

Constraint shards are regular Genesis shards where:
  - entities = constraint nodes (ROE, FSCM, ACM rules)
  - claims   = constraint predicates (PROHIBITS, PERMITS, REQUIRES, DELEGATES_TO, REVOKES)
  - provenance chain traces back to source doctrine documents

To build a constraint shard: run nodal_run.py on the doctrine document,
then namespace it as "constraint/{type}".
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from .types import (
    ConstraintMatch, ConstraintType, Decision, DecisionStatus,
    EvaluateRequest, ProvenanceTrace, TYPE_PRECEDENCE, DENY_BY_DEFAULT,
)


def _to_ctype(tag: str) -> ConstraintType:
    try:
        return ConstraintType(tag.upper())
    except Exception:
        return ConstraintType.UNKNOWN


def _prec_index(ctype: ConstraintType) -> int:
    try:
        return TYPE_PRECEDENCE.index(ctype.value)
    except ValueError:
        return len(TYPE_PRECEDENCE) + 100


def _parse_iso(s: str) -> Optional[datetime]:
    s = s.strip().rstrip("Z")
    try:
        d = datetime.fromisoformat(s)
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d
    except Exception:
        return None


def _within_validity(metadata: Dict[str, Any], context: Dict[str, Any]) -> bool:
    now = datetime.now(timezone.utc)
    for k in ("now", "time", "timestamp"):
        v = context.get(k)
        if isinstance(v, str):
            d = _parse_iso(v)
            if d:
                now = d
                break
    vf = metadata.get("valid_from")
    vt = metadata.get("valid_to") or metadata.get("valid_until")
    if isinstance(vf, str):
        d = _parse_iso(vf)
        if d and now < d:
            return False
    if isinstance(vt, str):
        d = _parse_iso(vt)
        if d and now > d:
            return False
    return True


class ConstraintEngine:
    """
    Evaluates constraints from mounted Spectra shards.

    Looks for shards with namespace prefix "constraint/" and queries
    their claims/entities tables for applicable rules.
    """

    def __init__(self, spectra_engine: Any) -> None:
        self._engine = spectra_engine

    def _get_constraint_claims(self) -> List[Dict[str, Any]]:
        """Pull all claims + entity labels from constraint-namespaced shards."""
        try:
            cat = self._engine.catalog_json()
        except Exception:
            return []

        claims: List[Dict[str, Any]] = []
        for mount in cat.get("mounts", []):
            shard_id = mount.get("shard_id", "")
            tables = mount.get("tables", [])
            claims_view = next(
                (t for t in tables if t.startswith("claims__")), None)
            entities_view = next(
                (t for t in tables if t.startswith("entities__")), None)
            if not claims_view or not entities_view:
                continue

            try:
                sql = f"""
                SELECT
                    c.claim_id,
                    e_subj.label AS subject_label,
                    c.predicate,
                    CASE WHEN c.object_type = 'entity' THEN e_obj.label
                         ELSE c.object END AS object_val,
                    c.object_type,
                    c.object
                FROM "{claims_view}" c
                JOIN "{entities_view}" e_subj ON c.subject = e_subj.entity_id
                LEFT JOIN "{entities_view}" e_obj
                    ON c.object_type = 'entity' AND c.object = e_obj.entity_id
                """
                result = self._engine.query_json(sql)
                for row in result.get("rows", []):
                    claims.append({
                        "claim_id": row[0],
                        "subject": row[1],
                        "predicate": (row[2] or "").upper(),
                        "object": row[3],
                        "object_type": row[4],
                        "object_raw": row[5],
                        "shard_id": shard_id,
                    })
            except Exception:
                continue

        return claims

    def _find_matches(
        self,
        req: EvaluateRequest,
        claims: List[Dict[str, Any]],
    ) -> List[ConstraintMatch]:
        """Find claims applicable to this request."""
        matches: List[ConstraintMatch] = []
        for c in claims:
            pred = c["predicate"]
            if pred not in ("PROHIBITS", "PERMITS", "REQUIRES", "APPLIES_TO"):
                continue
            subj = (c["subject"] or "").lower()
            obj = (c["object"] or "").lower()
            action_l = req.action.lower()
            target_l = req.target.lower()

            # Match: subject or object mentions action or target
            if not (action_l in subj or action_l in obj or
                    target_l in subj or target_l in obj or
                    subj in action_l or obj in target_l):
                continue

            # Classify constraint type from claim predicate/subject text
            ctype_tag = "GUIDANCE"
            for tag in ("ROE", "FSCM", "ACM", "WCS", "FPCON", "EMCON", "JRFL"):
                if tag.lower() in subj or tag.lower() in obj:
                    ctype_tag = tag
                    break

            matches.append(ConstraintMatch(
                constraint_id=c["claim_id"],
                constraint_type=_to_ctype(ctype_tag),
                match_reason=f"predicate={pred} subject={c['subject'][:40]}",
                weight=1.0,
            ))
        return matches

    def _resolve_authority_chain(
        self,
        actor: str,
        claims: List[Dict[str, Any]],
    ) -> Tuple[List[str], List[str], Set[str]]:
        """Walk DELEGATES_TO chain upward. Collect REVOKES."""
        notes: List[str] = []
        revoked: Set[str] = set()

        # Build delegation map: delegatee -> delegator
        deleg: Dict[str, str] = {}
        for c in claims:
            if c["predicate"] == "DELEGATES_TO":
                deleg[c["subject"]] = c["object"]
            elif c["predicate"] == "REVOKES":
                revoked.add((c["object"] or "").upper())
                notes.append(f"delegation_revoked:{c['object']}")

        chain = [actor]
        seen: Set[str] = {actor}
        cur = actor
        while cur in deleg:
            nxt = deleg[cur]
            if nxt in seen:
                notes.append("delegation_cycle_detected")
                break
            chain.append(nxt)
            seen.add(nxt)
            cur = nxt

        return chain, notes, revoked

    def evaluate(self, req: EvaluateRequest) -> Decision:
        """Evaluate an action against all mounted constraint shards."""
        claims = self._get_constraint_claims()

        if not claims:
            return Decision(
                status=DecisionStatus.CONDITIONAL if not DENY_BY_DEFAULT else DecisionStatus.DENY,
                controlling_constraint=None,
                notes=["no_constraint_shards_mounted"],
                confidence=0.0,
            )

        matches = self._find_matches(req, claims)
        auth_chain, notes, revoked = self._resolve_authority_chain(req.actor, claims)

        # Select controlling constraint by doctrine precedence
        controlling: Optional[ConstraintMatch] = None
        if matches:
            # FSCM always controls unless explicit ROE override
            fscm = [m for m in matches if m.constraint_type == ConstraintType.FSCM]
            if fscm:
                controlling = fscm[0]
            else:
                controlling = sorted(matches, key=lambda m: _prec_index(m.constraint_type))[0]

        if not matches:
            status = DecisionStatus.DENY if DENY_BY_DEFAULT else DecisionStatus.PERMIT
            return Decision(
                status=status,
                controlling_constraint=None,
                evaluated_constraints=[],
                authority_chain=auth_chain,
                notes=notes + ["deny_by_default" if DENY_BY_DEFAULT else "permit_by_default"],
                confidence=0.5,
            )

        # Determine status from controlling claim's predicate
        ctrl_claim = next(
            (c for c in claims if c["claim_id"] == controlling.constraint_id), None)
        pred = ctrl_claim["predicate"] if ctrl_claim else "UNKNOWN"

        if pred == "PROHIBITS":
            status = DecisionStatus.DENY
        elif pred == "PERMITS":
            status = DecisionStatus.PERMIT
        else:
            status = DecisionStatus.CONDITIONAL

        # Revocation overrides permit
        if req.actor.upper() in revoked:
            status = DecisionStatus.DENY
            notes.append(f"actor_authority_revoked:{req.actor}")

        confidence = 0.9 if status == DecisionStatus.PERMIT else 0.85
        if status == DecisionStatus.CONDITIONAL:
            confidence = 0.6

        return Decision(
            status=status,
            controlling_constraint=controlling.constraint_id,
            evaluated_constraints=matches,
            authority_chain=auth_chain,
            conditions=[{"type": "constraint_type",
                         "value": controlling.constraint_type.value}],
            confidence=confidence,
            notes=notes,
        )
