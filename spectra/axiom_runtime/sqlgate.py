import re

_READONLY_RE = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)

def is_read_only_sql(sql: str) -> bool:
    if not isinstance(sql, str):
        return False
    return bool(_READONLY_RE.match(sql))
