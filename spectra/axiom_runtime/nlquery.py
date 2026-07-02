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
    from axiom_runtime.nlquery import natural_language_to_query

    sql, params = natural_language_to_query("what decisions conflict")
    # execute through the read-only gate with bound parameters:
    #   con.execute(sql, params)

    # Backward-compatible string-only entry point (no user values inlined):
    from axiom_runtime.nlquery import natural_language_to_sql
    sql = natural_language_to_sql("all decisions")

SECURITY: All user/data-derived values (topics, dates) are passed as DuckDB
bound parameters (``?`` placeholders) — never interpolated into the SQL text.
Only fixed internal table/column identifiers and the static decision-predicate
allowlist are rendered as literal SQL. This gives the "zero injection surface"
the engine's read-only gate (sqlgate.is_read_only_sql) advertises.

Real Genesis claims schema (frozen):
    claims(claim_id, subject, predicate, object, object_type, tier)
    entities(entity_id, namespace, label, entity_type)
  Note: claims has NO shard_id column — the union views are plain
  ``SELECT *`` across per-shard JSONL-backed tables, which carry no shard
  provenance column. Queries therefore do not project/join shard_id off
  claims.
  Optional (from extensions — Genesis v1 kernel registry):
    temporal(claim_id, valid_from, valid_until, temporal_context)
    lineage(supersedes_shard_id, action, timestamp, note)
      — no self-id column: a shard's own id is derived from its manifest
        and never appears in its own files (spec section 9)
    refs(src_claim_id, relation_type, dst_shard_id, dst_object_type, dst_object_id, confidence, note)
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

# A parameterized query: SQL text with ``?`` placeholders plus the ordered
# list of bound parameter values.
Query = Tuple[str, List[object]]


# ---------------------------------------------------------------------------
# Decision predicates recognized by the system
#
# Static, code-defined allowlist (not user data). Rendered as a literal IN
# clause; values can never come from the request.
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

def natural_language_to_query(question: str, limit: int = 50) -> Query:
    """Convert a plain-English question to a parameterized SQL query.

    Returns ``(sql, params)`` where ``sql`` contains ``?`` placeholders and
    ``params`` is the ordered list of bound values. Execute with
    ``con.execute(sql, params)`` (through the read-only gate).
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
    return (
        """
        SELECT DISTINCT subject, object AS title
        FROM claims
        WHERE predicate = 'has_title'
        ORDER BY subject
        LIMIT ?
        """,
        [limit],
    )


def natural_language_to_sql(question: str, limit: int = 50) -> str:
    """Backward-compatible string entry point.

    Returns the SQL text only. Because user/data values are bound as ``?``
    placeholders, the returned string is parameter-free SQL and must be
    executed with the matching parameters from ``natural_language_to_query``.
    Prefer ``natural_language_to_query`` for execution.
    """
    sql, _params = natural_language_to_query(question, limit)
    return sql


# ---------------------------------------------------------------------------
# Pattern handlers — each returns (sql, params) or None to pass through
# ---------------------------------------------------------------------------

def _handle_contradictions(q: str, limit: int) -> Optional[Query]:
    """Detect: 'what contradicts', 'conflicts', 'contradictions', 'inconsistent'"""
    if not any(k in q for k in ["contradict", "conflict", "inconsisten"]):
        return None

    return (
        f"""
        SELECT
            a.subject,
            a.predicate,
            a.object AS decision_a,
            b.object AS decision_b
        FROM claims a
        JOIN claims b
            ON a.subject = b.subject
            AND a.predicate = b.predicate
            AND a.object != b.object
            AND a.claim_id < b.claim_id
        WHERE a.predicate IN {DECISION_IN_CLAUSE}
        ORDER BY a.subject
        LIMIT ?
        """,
        [limit],
    )


def _handle_timeline(q: str, limit: int) -> Optional[Query]:
    """Detect: 'timeline', 'history of', 'chronolog'"""
    if not any(k in q for k in ["timeline", "history of", "chronolog"]):
        return None

    # Check if timeline is about a specific topic
    m = re.search(r"(?:timeline|history)\s+(?:of|for)\s+(.+?)(?:\?|$)", q)
    if m:
        topic = _clean_topic(m.group(1))
        like = _like(topic)
        return (
            f"""
            SELECT
                c.subject, c.predicate, c.object,
                t.valid_from AS decided_at
            FROM claims c
            LEFT JOIN temporal t ON c.claim_id = t.claim_id
            WHERE c.predicate IN {DECISION_IN_CLAUSE}
              AND (lower(c.object) LIKE ?
                   OR lower(c.subject) LIKE ?)
            ORDER BY t.valid_from ASC NULLS LAST
            LIMIT ?
            """,
            [like, like, limit],
        )

    # General timeline of all decisions
    return (
        f"""
        SELECT
            c.subject, c.predicate, c.object,
            t.valid_from AS decided_at
        FROM claims c
        LEFT JOIN temporal t ON c.claim_id = t.claim_id
        WHERE c.predicate IN {DECISION_IN_CLAUSE}
        ORDER BY t.valid_from ASC NULLS LAST
        LIMIT ?
        """,
        [limit],
    )


def _handle_staleness(q: str, limit: int) -> Optional[Query]:
    """Detect: 'stale', 'outdated', 'old decisions', 'not reviewed', 'coverage'"""
    if not any(k in q for k in ["stale", "outdat", "not review", "coverage", "old decision"]):
        return None

    return (
        f"""
        SELECT
            c.subject, c.predicate, c.object,
            t.valid_from AS decided_at,
            t.valid_until
        FROM claims c
        LEFT JOIN temporal t ON c.claim_id = t.claim_id
        WHERE c.predicate IN {DECISION_IN_CLAUSE}
          AND (t.valid_until IS NULL OR t.valid_until = '')
        ORDER BY t.valid_from ASC NULLS FIRST
        LIMIT ?
        """,
        [limit],
    )


def _handle_lineage(q: str, limit: int) -> Optional[Query]:
    """Detect: 'supersed', 'what replaced', 'version', 'lineage'

    Reads the ext lineage view (lineage@1). Rows name only PREDECESSOR
    shards (supersedes_shard_id, sh1_ form) — there is no self-id column,
    because a shard's own id is derived from its manifest and cannot appear
    in its own files. No user values are interpolated.
    """
    if not any(k in q for k in ["supersed", "replaced", "lineage", "version chain"]):
        return None

    return (
        """
        SELECT
            l.supersedes_shard_id AS replaced_shard,
            l.action,
            l.timestamp,
            l.note
        FROM lineage l
        ORDER BY l.timestamp DESC
        LIMIT ?
        """,
        [limit],
    )


def _handle_changed_since(q: str, limit: int) -> Optional[Query]:
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

    return (
        f"""
        SELECT
            c.subject, c.predicate, c.object,
            t.valid_from AS decided_at
        FROM claims c
        LEFT JOIN temporal t ON c.claim_id = t.claim_id
        WHERE c.predicate IN {DECISION_IN_CLAUSE}
          AND t.valid_from >= ?
        ORDER BY t.valid_from ASC
        LIMIT ?
        """,
        [date_str, limit],
    )


def _handle_decisions_about(q: str, limit: int) -> Optional[Query]:
    """Detect: 'what did I/we decide about X', 'decisions about X'"""
    m = re.search(
        r"(?:decide|decided|decision).{0,20}(?:about|on|for|regarding)\s+(.+?)(?:\?|$)", q
    )
    if not m:
        return None

    topic = _clean_topic(m.group(1))
    like = _like(topic)
    return (
        f"""
        SELECT DISTINCT
            c.subject, c.predicate, c.object,
            t.valid_from AS decided_at
        FROM claims c
        LEFT JOIN temporal t ON c.claim_id = t.claim_id
        WHERE c.predicate IN {DECISION_IN_CLAUSE}
          AND (lower(c.object) LIKE ? OR lower(c.subject) LIKE ?)
        ORDER BY t.valid_from ASC NULLS LAST
        """,
        [like, like],
    )


def _handle_all_decisions(q: str, limit: int) -> Optional[Query]:
    """Detect: 'all decisions', 'what decisions', 'list decisions'"""
    if not any(k in q for k in ["all decision", "what decision", "list decision",
                                  "every decision", "our decision"]):
        return None

    return (
        f"""
        SELECT
            c.subject, c.predicate, c.object,
            t.valid_from AS decided_at
        FROM claims c
        LEFT JOIN temporal t ON c.claim_id = t.claim_id
        WHERE c.predicate IN {DECISION_IN_CLAUSE}
        ORDER BY t.valid_from ASC NULLS LAST
        LIMIT ?
        """,
        [limit],
    )


def _handle_list_all(q: str, limit: int) -> Optional[Query]:
    """Detect: 'all conversations', 'list all', 'show all', 'everything'"""
    if not any(k in q for k in ["all conversations", "list all", "show all", "everything"]):
        return None

    return (
        """
        SELECT DISTINCT subject, object AS title
        FROM claims
        WHERE predicate = 'has_title'
        ORDER BY subject
        """,
        [],
    )


def _handle_topic_query(q: str, limit: int) -> Optional[Query]:
    """Detect: 'about X', 'regarding X', 'related to X'"""
    m = re.search(
        r"(?:about|regarding|related to|involving|mention(?:ing)?)\s+(.+?)(?:\?|$)", q
    )
    if not m:
        return None

    topic = _clean_topic(m.group(1))
    like = _like(topic)
    return (
        """
        SELECT DISTINCT c.subject AS conversation, c2.object AS title
        FROM claims c
        JOIN claims c2 ON c.subject = c2.subject AND c2.predicate = 'has_title'
        WHERE c.predicate = 'has_title'
           OR (lower(c.object) LIKE ? OR lower(c.subject) LIKE ?)
        ORDER BY title
        """,
        [like, like],
    )


def _handle_show_find(q: str, limit: int) -> Optional[Query]:
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

    like = _like(topic)
    return (
        """
        SELECT DISTINCT subject, predicate, object
        FROM claims
        WHERE lower(object) LIKE ?
           OR lower(subject) LIKE ?
        ORDER BY subject
        LIMIT ?
        """,
        [like, like, limit],
    )


def _handle_keyword_fallback(q: str, limit: int) -> Optional[Query]:
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

    selected = words[:4]
    # One ``(lower(object) LIKE ? OR lower(subject) LIKE ?)`` group per word;
    # every value is bound, none interpolated.
    conditions = " OR ".join(
        "lower(object) LIKE ? OR lower(subject) LIKE ?" for _ in selected
    )
    params: List[object] = []
    for w in selected:
        like = _like(w)
        params.extend([like, like])
    params.append(limit)

    return (
        f"""
        SELECT DISTINCT subject, predicate, object
        FROM claims
        WHERE {conditions}
        ORDER BY subject
        LIMIT ?
        """,
        params,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_topic(raw: str) -> str:
    """Strip surrounding whitespace, trailing punctuation, and stray quotes.

    Quotes are stripped purely for cleaner matching; injection safety comes
    from parameter binding (``_like`` values are bound, never interpolated),
    not from this sanitization.
    """
    cleaned = raw.strip().rstrip("?.,;:").strip()
    return cleaned.strip("'\"").strip()


def _like(topic: str) -> str:
    """Build a case-insensitive substring LIKE value to bind as a parameter."""
    return f"%{topic.lower()}%"
