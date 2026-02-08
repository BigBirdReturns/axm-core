#!/usr/bin/env python3
"""AXM Stack Doctor (fail closed).

Run:
  python scripts/doctor.py

Optional:
  python scripts/doctor.py --python /path/to/python
  python scripts/doctor.py --stack-root /path/to/axm-stack-v1

Exit codes:
  0 pass
  2 import failure
  3 gold shard verification failure
  4 unexpected error
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REQUIRED_MODULES = [
    "blake3",
    "duckdb",
    "pyarrow",
    "click",
    "cryptography",
    "axm_build",
    "axm_verify",
    "axm_forge",
]


def detect_stack_root(start: Path) -> Path | None:
    def is_root(p: Path) -> bool:
        return (
            (p / "genesis" / "src" / "axm_build").exists()
            and (p / "genesis" / "src" / "axm_verify").exists()
            and (p / "forge" / "axm_forge").exists()
        )

    p = start.resolve()
    for _ in range(9):
        if is_root(p):
            return p
        if p.parent == p:
            return None
        p = p.parent
    return None


def build_pythonpath(root: Path) -> str:
    sep = ";" if os.name == "nt" else ":"
    parts = [
        str(root / "genesis" / "src"),
        str(root / "forge"),
        str(root / "clarion"),
        str(root / "spectra"),
    ]
    return sep.join(parts)


def import_checks() -> tuple[bool, list[str]]:
    failures: list[str] = []
    for m in REQUIRED_MODULES:
        try:
            __import__(m)
        except Exception as e:
            failures.append(f"{m}: {e}")
    return (len(failures) == 0), failures


def run_gold_verify(python_exe: str, env: dict[str, str], root: Path) -> tuple[int, str, str]:
    gold_shard = root / "genesis" / "shards" / "gold" / "fm21-11-hemorrhage-v1"
    trusted_key = gold_shard / "sig" / "publisher.pub"
    if not gold_shard.exists():
        return (3, "", f"Gold shard not found: {gold_shard}")
    if not trusted_key.exists():
        return (3, "", f"Trusted key not found: {trusted_key}")

    cmd = [
        python_exe,
        "-m",
        "axm_verify.cli",
        "shard",
        str(gold_shard),
        "--trusted-key",
        str(trusted_key),
    ]
    p = subprocess.run(cmd, env=env, capture_output=True, text=True)
    return (0 if p.returncode == 0 else 3, p.stdout.strip(), p.stderr.strip())


def env_report(env: dict[str, str]) -> dict:
    versions = {}
    for m in ["blake3", "duckdb", "pyarrow", "click", "cryptography"]:
        try:
            mod = __import__(m)
            versions[m] = getattr(mod, "__version__", "unknown")
        except Exception:
            versions[m] = "missing"

    return {
        "python_executable": sys.executable,
        "python_version": sys.version.replace("\n", " "),
        "pythonpath": env.get("PYTHONPATH", ""),
        "sys_path_head": sys.path[:6],
        "versions": versions,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--python", dest="python_exe", default=sys.executable)
    ap.add_argument("--stack-root", dest="stack_root", default=None)
    args = ap.parse_args()

    root = Path(args.stack_root) if args.stack_root else detect_stack_root(Path.cwd())
    if root is None:
        print("ERROR: Could not detect stack root. Pass --stack-root.", file=sys.stderr)
        return 4

    pythonpath = build_pythonpath(root)
    env = dict(os.environ)
    env["PYTHONPATH"] = pythonpath

    # Ensure current process imports follow the same path rules
    for p in pythonpath.split(";" if os.name == "nt" else ":"):
        if p and p not in sys.path:
            sys.path.insert(0, p)

    ok, failures = import_checks()
    if not ok:
        print("IMPORT_FAIL", file=sys.stderr)
        for f in failures:
            print(f, file=sys.stderr)
        print(json.dumps({"report": env_report(env)}, indent=2))
        return 2

    code, vout, verr = run_gold_verify(args.python_exe, env, root)
    if code != 0:
        print("VERIFY_FAIL", file=sys.stderr)
        if vout:
            print(vout, file=sys.stderr)
        if verr:
            print(verr, file=sys.stderr)
        print(json.dumps({"report": env_report(env), "stack_root": str(root)}, indent=2))
        return 3

    result = {
        "status": "PASS",
        "stack_root": str(root),
        "verify_stdout": vout,
        "verify_stderr": verr,
        "report": env_report(env),
    }
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(4)
