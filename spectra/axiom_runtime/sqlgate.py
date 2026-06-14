import re

_READONLY_RE = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)

# Denylist of DuckDB functions / statements that can read external files or
# mutate engine state. These must NEVER be reachable through the user-submitted
# SQL entry point (engine.query_json), which is the ONLY caller of
# is_read_only_sql(). The engine's internal mount SQL legitimately uses
# read_parquet() but executes via self.con.execute(...) directly and never
# passes through this gate, so scoping the denylist here applies it exclusively
# to user queries.
_DENYLIST = (
    "read_parquet",
    "read_csv",
    "read_csv_auto",
    "read_json",
    "read_json_auto",
    "read_text",
    "read_blob",
    "parquet_scan",
    "glob",
    "attach",
    "copy",
    "install",
    "load",
    "pragma",
)

# Case-insensitive, word-boundary match for any denied token.
_DENY_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(tok) for tok in _DENYLIST) + r")\b",
    re.IGNORECASE,
)


def is_read_only_sql(sql: str) -> bool:
    """Gate for USER-submitted SQL only (engine.query_json).

    Returns True only if the SQL both starts with SELECT/WITH and contains none
    of the denied file-reading / external / state-changing functions.
    """
    if not isinstance(sql, str):
        return False
    if not _READONLY_RE.match(sql):
        return False
    if _DENY_RE.search(sql):
        return False
    return True
