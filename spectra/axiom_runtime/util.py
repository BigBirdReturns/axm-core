import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Tuple

_SAFE_IDENT_RE = re.compile(r"[^a-zA-Z0-9_]+")

def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def sanitize_identifier(s: str) -> str:
    s = str(s).strip()
    s = _SAFE_IDENT_RE.sub("_", s)
    s = s.strip("_")
    if not s:
        return "x"
    return s[:64]

def quote_ident(name: str) -> str:
    safe = name.replace('"', '""')
    return f'"{safe}"'

def choose_temp_root() -> Tuple[Path, str]:
    for env in ("SPECTRA_TEMP_ROOT", "TMPDIR", "TEMP", "TMP"):
        v = os.environ.get(env)
        if v:
            p = Path(v).expanduser().resolve(strict=False)
            p.mkdir(parents=True, exist_ok=True)
            return p, f"env:{env}"
    return Path(tempfile.gettempdir()).resolve(), "system"
