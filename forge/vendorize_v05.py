#!/usr/bin/env python3
"""AXM Forge - vendorize_v05.py

Purpose
- Copy AXM v0.5 "Brain" sources into axm_forge/vendor/axm_v05
- Validate the minimum API contract used by Tier 3:
  - axm_forge.vendor.axm_v05.executor.get_executor
  - axm_forge.vendor.axm_v05.parser.LLMRequest

Usage
  python vendorize_v05.py /path/to/unzipped/axm-v0.5

Notes
- The v0.5 zips you provided typically contain src/axm/*
- This script prefers a src/axm tree if present, otherwise falls back to a flat axm/ package
- Import patching is conservative: it only rewrites absolute 'from axm.' / 'import axm.' references.
"""

from __future__ import annotations

import argparse
import ast
import shutil
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Tuple


REQUIRED_FILES = ("executor.py", "parser.py")
REQUIRED_SYMBOLS = {
    "executor.py": ("get_executor",),
    "parser.py": ("LLMRequest",),
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parent


def _vendor_dir() -> Path:
    return _repo_root() / "axm_forge" / "vendor" / "axm_v05"


def find_v05_source(search_root: Path) -> Path:
    """Find the best candidate for the v0.5 'axm' package.

    Priority:
    1) */src/axm (packaged layout)
    2) */axm with executor.py present (flat layout)
    """
    if not search_root.exists():
        raise FileNotFoundError(f"Source path does not exist: {search_root}")

    candidates: List[Path] = []

    # Prefer src/axm
    for p in search_root.rglob("src/axm"):
        if (p / "executor.py").exists() and (p / "parser.py").exists():
            candidates.append(p)

    # Fallback to flat axm/
    if not candidates:
        for p in search_root.rglob("axm"):
            if p.is_dir() and (p / "executor.py").exists() and (p / "parser.py").exists():
                candidates.append(p)

    if not candidates:
        raise FileNotFoundError(
            "Could not find AXM v0.5 package. Expected a directory containing executor.py and parser.py, " 
            "commonly at src/axm/."
        )

    # Choose the most complete candidate (more .py files wins)
    def score(pkg: Path) -> Tuple[int, int]:
        py_count = sum(1 for _ in pkg.rglob("*.py"))
        size = sum((f.stat().st_size for f in pkg.rglob("*.py")), 0)
        return (py_count, size)

    candidates.sort(key=score, reverse=True)
    return candidates[0]


def _has_symbol(py_text: str, symbol: str) -> bool:
    """AST-based symbol check for top-level defs/classes."""
    try:
        tree = ast.parse(py_text)
    except SyntaxError:
        # fail open for parsing issues; still allow vendoring but warn
        return True
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == symbol:
                return True
    return False


def validate_source(pkg_dir: Path) -> None:
    print(f"[*] Validating v0.5 source: {pkg_dir}")
    missing: List[str] = []
    for fname in REQUIRED_FILES:
        if not (pkg_dir / fname).exists():
            missing.append(fname)
    if missing:
        raise FileNotFoundError(f"Missing required files in v0.5 package: {', '.join(missing)}")

    for fname, symbols in REQUIRED_SYMBOLS.items():
        text = (pkg_dir / fname).read_text(encoding="utf-8", errors="ignore")
        for sym in symbols:
            ok = _has_symbol(text, sym)
            if not ok:
                raise RuntimeError(f"Required symbol '{sym}' not found in {fname}")

    print("[+] Required files and symbols present.")


def patch_imports(vendor_pkg: Path) -> int:
    """Rewrite absolute imports that reference 'axm.<module>' into relative imports.

    Conservative rules:
    - Replace lines like: from axm.foo import Bar  -> from .foo import Bar
    - Replace lines like: import axm.foo          -> from . import foo
    - Does NOT rewrite: import axm
    """
    import re

    patched = 0
    for py_file in vendor_pkg.rglob("*.py"):
        original = py_file.read_text(encoding="utf-8", errors="ignore")
        text = original

        # from axm.foo import X
        text = re.sub(r"^\s*from\s+axm\.(\w+)\s+import\s+", r"from .\1 import ", text, flags=re.MULTILINE)

        # import axm.foo [as bar]
        def _repl(m: re.Match) -> str:
            mod = m.group(1)
            as_part = m.group(2) or ""
            # 'from . import foo as bar' is valid
            return f"from . import {mod}{as_part}"
        text = re.sub(r"^\s*import\s+axm\.(\w+)(\s+as\s+\w+)?\s*$", _repl, text, flags=re.MULTILINE)

        if text != original:
            py_file.write_text(text, encoding="utf-8")
            patched += 1

    return patched


def install_vendor(src_pkg: Path) -> None:
    dst = _vendor_dir()
    print(f"[*] Installing into: {dst}")

    if dst.exists():
        shutil.rmtree(dst)

    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src_pkg, dst)

    # Ensure __init__.py exists
    init = dst / "__init__.py"
    if not init.exists():
        init.write_text("# vendored AXM v0.5\n", encoding="utf-8")

    patched = patch_imports(dst)
    print(f"[+] Patched imports in {patched} files.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Vendor AXM v0.5 into this AXM Forge repo")
    ap.add_argument("source", type=str, help="Path to unzipped AXM v0.5 folder")
    args = ap.parse_args()

    src_root = Path(args.source).expanduser().resolve()
    try:
        pkg = find_v05_source(src_root)
        print(f"[*] Selected package: {pkg}")
        validate_source(pkg)
        install_vendor(pkg)
        print("\n[SUCCESS] AXM v0.5 vendored. Tier 3 can now import the Brain.")
        print("Next: run smoke test: python scripts/smoke_tier3.py")
        return 0
    except Exception as e:
        print(f"\n[ERROR] {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
