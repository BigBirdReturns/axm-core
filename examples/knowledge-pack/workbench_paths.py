"""Resolve unchanged repository/package ownership in source-kit distributions."""

from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
for parent in (HERE.parents[1], HERE / "runtime"):
    for name in ("forge", "spectra"):
        candidate = parent / name
        if candidate.is_dir():
            sys.path.insert(0, str(candidate))
