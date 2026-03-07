"""
axm_core / spectra / nlquery.py
================================
Natural language → SQL translation for Spectra's query engine.

No LLM required. Handles common patterns for querying the
claims/entities/temporal/lineage tables mounted by Spectra.

Supports:
  - Basic search (keyword, topic, show/find)
  - Decision lifecycle (what was decided, revised, rejected)
  - Temporal queries (what changed since X, timeline)
  - Contradiction detection (conflicting decisions)
  - Staleness/coverage (what hasn't been reviewed)
  - Lineage (what superseded what)

Usage:
    from axiom_runtime.nlquery import natural_language_to_sql

    sql = natural_language_to_sql("what decisions conflict")
    results = spectra_engine.query_json(sql)

Assumes standard AXM schema:
    claims(claim_id, subject, predicate, object, object_type, tier, shard_id)
    entities(entity_id, label, shard_id)
  Optional (from extensions):
    temporal(claim_id, valid_from, valid_until, temporal_context)
    lineage(shard_id, supersedes_shard_id, action, timestamp, note)
    refs(src_claim_id, relation_type, dst_shard_id, dst_object_type, dst_object_id, confidence, note)
"""
from __future__ import annotations

import re
from typing import Optional


# ---------------------------------------------------------------------------
# Decision predicates recognized by the system
# ---------------------------------------------------------------------------

DECISION_PREDICATES = (
    "'decided'", "'chose'", "'selected'", "'rejected'", "'confirmed'",
    "'proposed'", "'revised'", "'superseded'", "'approved'", "'committed'",
    "'adopted'", "'abandoned'", "'deferred'", "'pivoted'", "'discovered'",
)
DECISION_IN_CLAUSE = f"({', '.join(DECISION_PREDICATES)})"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def natural_language_to_sql(question: str, limit: int = 50) -> str:
    """Convert a plain-English question to SQL.

    Returns a SQL string ready for Spectra's query_json().
    """
    q = question.lower().strip()

    # Try each pattern family in order of specificity
    for handler in [
        _handle_contradictions,
        _handle_timeline,
        _handle_staleness,
        _handle_lineage,
        _handle_changed_since,
        _handle_decisions_about,
        _handle_all_decisions,
        _handle_list_all,
        _handle_topic_query,
        _handle_show_find,
        _handle_keyword_fallback,
    ]:
        result = handler(q, limit)
        if result is not None:
            return result

    # Last resort
    return f"""
        SELECT DISTINCT subject, object AS title
        FROM claims
        WHERE predicate = 'has_title'
        ORDER BY subject
        LIMIT {limit}
    """


# ---------------------------------------------------------------------------
# Pattern handlers — each returns SQL or None to pass through
# ---------------------------------------------------------------------------

def _handle_contradictions(q: str, limit: int) -> Optional[str]:
    """Detect: 'what contradicts', 'conflicts', 'contradictions', 'inconsistent'"""
    if not any(k in q for k in ["contradict", "conflict", "inconsisten"]):
        return None

    return f"""
        SELECT
            a.subject,
            a.predicate,
            a.object AS decision_a,
            b.object AS decision_b,
            a.shard_id AS shard_a,
            b.shard_id AS shard_b
        FROM claims a
        JOIN claims b
            ON a.subject = b.subject
            AND a.predicate = b.predicate
            AND a.object != b.object
            AND a.claim_id < b.claim_id
        WHERE a.predicate IN {DECISION_IN_CLAUSE}
        ORDER BY a.subject
        LIMIT {limit}
    """


def _handle_timeline(q: str, limit: int) -> Optional[str]:
    """Detect: 'timeline', 'history of', 'chronolog'"""
    if not any(k in q for k in ["timeline", "history of", "chronolog"]):
        return None

    # Check if timeline is about a specific topic
    m = re.search(r"(?:timeline|history)\s+(?:of|for)\s+(.+?)(?:\?|$)", q)
    if m:
        topic = _clean_topic(m.group(1))
        return f"""
            SELECT
                c.subject, c.predicate, c.object,
                t.valid_from AS decided_at,
                c.shard_id
            FROM claims c
            LEFT JOIN temporal t ON c.claim_id = t.claim_id
            WHERE c.predicate IN {DECISION_IN_CLAUSE}
              AND (lower(c.object) LIKE '%{topic}%'
                   OR lower(c.subject) LIKE '%{topic}%')
            ORDER BY t.valid_from ASC NULLS LAST
            LIMIT {limit}
        """

    # General timeline of all decisions
    return f"""
        SELECT
            c.subject, c.predicate, c.object,
            t.valid_from AS decided_at,
            c.shard_id
        FROM claims c
        LEFT JOIN temporal t ON c.claim_id = t.claim_id
        WHERE c.predicate IN {DECISION_IN_CLAUSE}
        ORDER BY t.valid_from ASC NULLS LAST
        LIMIT {limit}
    """


def _handle_staleness(q: str, limit: int) -> Optional[str]:
    """Detect: 'stale', 'outdated', 'old decisions', 'not reviewed', 'coverage'"""
    if not any(k in q for k in ["stale", "outdat", "not review", "coverage", "old decision"]):
        return None

    return f"""
        SELECT
            c.subject, c.predicate, c.object,
            t.valid_from AS decided_at,
            t.valid_until,
            c.shard_id
        FROM claims c
        LEFT JOIN temporal t ON c.claim_id = t.claim_id
        WHERE c.predicate IN {DECISION_IN_CLAUSE}
          AND (t.valid_until IS NULL OR t.valid_until = '')
        ORDER BY t.valid_from ASC NULLS FIRST
        LIMIT {limit}
    """


def _handle_lineage(q: str, limit: int) -> Optional[str]:
    """Detect: 'supersed', 'what replaced', 'version', 'lineage'"""
    if not any(k in q for k in ["supersed", "replaced", "lineage", "version chain"]):
        return None

    return f"""
        SELECT
            l.shard_id AS current_shard,
            l.supersedes_shard_id AS replaced_shard,
            l.action,
            l.timestamp,
            l.note
        FROM lineage l
        ORDER BY l.timestamp DESC
        LIMIT {limit}
    """


def _handle_changed_since(q: str, limit: int) -> Optional[str]:
    """Detect: 'changed since', 'new since', 'after january', 'since february'"""
    # Look for date references
    m = re.search(
        r"(?:since|after|from|changed since|new since)\s+"
        r"(\d{4}-\d{2}-\d{2}|(?:january|february|march|april|may|june|"
        r"july|august|september|october|november|december)\s*\d{0,4})",
        q,
    )
    if not m:
        return None

    date_str = m.group(1).strip()

    # Convert month names to approximate ISO dates
    month_map = {
        "january": "01", "february": "02", "march": "03", "april": "04",
        "may": "05", "june": "06", "july": "07", "august": "08",
        "september": "09", "october": "10", "november": "11", "december": "12",
    }
    for month_name, month_num in month_map.items():
        if month_name in date_str:
            year_match = re.search(r"(\d{4})", date_str)
            year = year_match.group(1) if year_match else "2026"
            date_str = f"{year}-{month_num}-01"
            break

    return f"""
        SELECT
            c.subject, c.predicate, c.object,
            t.valid_from AS decided_at,
            c.shard_id
        FROM claims c
        LEFT JOIN temporal t ON c.claim_id = t.claim_id
        WHERE c.predicate IN {DECISION_IN_CLAUSE}
          AND t.valid_from >= '{date_str}'
        ORDER BY t.valid_from ASC
        LIMIT {limit}
    """


def _handle_decisions_about(q: str, limit: int) -> Optional[str]:
    """Detect: 'what did I/we decide about X', 'decisions about X'"""
    m = re.search(
        r"(?:decide|decided|decision).{0,20}(?:about|on|for|regarding)\s+(.+?)(?:\?|$)", q
    )
    if not m:
        return None

    topic = _clean_topic(m.group(1))
    return f"""
        SELECT DISTINCT
            c.subject, c.predicate, c.object,
            t.valid_from AS decided_at,
            c.shard_id
        FROM claims c
        LEFT JOIN temporal t ON c.claim_id = t.claim_id
        WHERE c.predicate IN {DECISION_IN_CLAUSE}
          AND (lower(c.object) LIKE '%{topic}%' OR lower(c.subject) LIKE '%{topic}%')
        ORDER BY t.valid_from ASC NULLS LAST
    """


def _handle_all_decisions(q: str, limit: int) -> Optional[str]:
    """Detect: 'all decisions', 'what decisions', 'list decisions'"""
    if not any(k in q for k in ["all decision", "what decision", "list decision",
                                  "every decision", "our decision"]):
        return None

    return f"""
        SELECT
            c.subject, c.predicate, c.object,
            t.valid_from AS decided_at,
            c.shard_id
        FROM claims c
        LEFT JOIN temporal t ON c.claim_id = t.claim_id
        WHERE c.predicate IN {DECISION_IN_CLAUSE}
        ORDER BY t.valid_from ASC NULLS LAST
        LIMIT {limit}
    """


def _handle_list_all(q: str, limit: int) -> Optional[str]:
    """Detect: 'all conversations', 'list all', 'show all', 'everything'"""
    if not any(k in q for k in ["all conversations", "list all", "show all", "everything"]):
        return None

    return """
        SELECT DISTINCT subject, object AS title
        FROM claims
        WHERE predicate = 'has_title'
        ORDER BY subject
    """


def _handle_topic_query(q: str, limit: int) -> Optional[str]:
    """Detect: 'about X', 'regarding X', 'related to X'"""
    m = re.search(
        r"(?:about|regarding|related to|involving|mention(?:ing)?)\s+(.+?)(?:\?|$)", q
    )
    if not m:
        return None

    topic = _clean_topic(m.group(1))
    return f"""
        SELECT DISTINCT c.subject AS conversation, c2.object AS title
        FROM claims c
        JOIN claims c2 ON c.subject = c2.subject AND c2.predicate = 'has_title'
        WHERE c.predicate = 'has_title'
           OR (lower(c.object) LIKE '%{topic}%' OR lower(c.subject) LIKE '%{topic}%')
        ORDER BY title
    """


def _handle_show_find(q: str, limit: int) -> Optional[str]:
    """Detect: 'show me X', 'find X', 'search X'"""
    m = re.search(
        r"(?:show|find|search|get|list)\s+(?:me\s+)?(?:all\s+)?(.+?)(?:\?|$)", q
    )
    if not m:
        return None

    topic = _clean_topic(m.group(1))
    topic = re.sub(r"^(?:my|the|all|conversations?|about)\s+", "", topic).strip()
    if not topic:
        return None

    return f"""
        SELECT DISTINCT subject, predicate, object, shard_id
        FROM claims
        WHERE lower(object) LIKE '%{topic}%'
           OR lower(subject) LIKE '%{topic}%'
        ORDER BY subject
        LIMIT {limit}
    """


def _handle_keyword_fallback(q: str, limit: int) -> Optional[str]:
    """Last resort: keyword search across subject + object columns."""
    _STOP = frozenset({
        "what", "when", "where", "which", "that", "this",
        "have", "from", "with", "about", "show", "find",
        "tell", "give", "list", "know", "does", "your",
        "were", "there", "their", "would", "could", "should",
    })
    words = [
        w for w in re.split(r"\W+", q)
        if len(w) > 3 and w not in _STOP
    ]
    if not words:
        return None

    conditions = " OR ".join(
        f"lower(object) LIKE '%{w}%' OR lower(subject) LIKE '%{w}%'"
        for w in words[:4]
    )
    return f"""
        SELECT DISTINCT subject, predicate, object, shard_id
        FROM claims
        WHERE {conditions}
        ORDER BY subject
        LIMIT {limit}
    """


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_topic(raw: str) -> str:
    """Strip trailing punctuation and whitespace."""
    return raw.strip().rstrip("?.,;:").strip()
