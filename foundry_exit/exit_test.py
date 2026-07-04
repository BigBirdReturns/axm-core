"""The exit property: the liberated record survives Palantir, GhostBox, and the
importer.

Verify the sealed Foundry exit bundle using ONLY the shard bytes plus the
out-of-band public key, through the genesis verifier CLI. This module imports no
importer code and no ghostbox code, and touches no Palantir endpoint -- it is the
proof that the record's verifiability does not depend on any of them.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Dict


def verify_detached(shard_dir: str | Path, trusted_key: str | Path, axm_verify: str = "axm-verify") -> Dict[str, Any]:
    proc = subprocess.run(
        [axm_verify, "shard", str(shard_dir), "--trusted-key", str(trusted_key)],
        capture_output=True,
        text=True,
    )
    result: Dict[str, Any] = {}
    body = proc.stdout.strip()
    if body:
        try:
            result = json.loads(body.splitlines()[-1])
        except json.JSONDecodeError:
            result = {"raw_stdout": proc.stdout, "raw_stderr": proc.stderr}
    return {
        "exit_code": proc.returncode,
        "status": result.get("status"),
        "importer_involved": False,
        "ghostbox_involved": False,
        "palantir_involved": False,
        "genesis_result": result,
    }
