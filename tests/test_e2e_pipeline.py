#!/usr/bin/env python3
"""Regression tests for the shipped end-to-end path.

Two defects these pin down:

1. `axm-forge extract` (cmd_extract) crashed with AttributeError on any
   document that actually produced claims — it reached for fields the Claim
   model does not have. It only looked healthy on fact-free inputs where the
   conversion loop never ran.

2. `integration_test.py` hardcoded a candidate whose evidence string did not
   appear in the shipped example input (`forge/inputs/doc1.txt`), so the
   documented demo failed on the shipped bytes with a bare
   "Genesis compile failed".

Both tests are keyless: tier-1 extraction is regex-only, and compilation uses
a throwaway keypair.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DOC1 = REPO / "forge" / "inputs" / "doc1.txt"

sys.path.insert(0, str(REPO / "forge"))


def _mldsa_available() -> bool:
    try:
        from axm_build.sign import hybrid1_keygen

        hybrid1_keygen()
        return True
    except Exception:
        return False


def test_forge_extract_cli_on_fact_bearing_doc(tmp_path):
    """cmd_extract must survive a document that yields real claims."""
    doc = tmp_path / "rich.txt"
    doc.write_text(
        "The invoice total was $4,321.00, due on 2025-12-31.\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    proc = subprocess.run(
        [sys.executable, "-m", "axm_forge.cli.main", "extract", str(doc), "--out", str(out)],
        capture_output=True,
        text=True,
        cwd=REPO / "forge",
    )
    assert proc.returncode == 0, proc.stderr

    candidate_files = list(out.rglob("candidates.jsonl"))
    assert len(candidate_files) == 1
    candidates = [
        json.loads(line)
        for line in candidate_files[0].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert candidates, "tier-1 extraction should find money + date candidates"
    evidences = {c["evidence"] for c in candidates}
    assert "$4,321.00" in evidences
    assert "2025-12-31" in evidences
    # Every candidate must carry the fields the Genesis compiler requires.
    for c in candidates:
        assert c["subject"] and c["predicate"] and c["evidence"]


@pytest.mark.skipif(not _mldsa_available(), reason="no ML-DSA-44 signing backend installed")
def test_integration_pipeline_on_shipped_input(tmp_path):
    """The documented demo must pass on the shipped example input, twice.

    The second run exercises workdir reuse: a stale shard tree from a prior
    run used to trip the Genesis compiler's wipe guard.
    """
    for _ in range(2):
        proc = subprocess.run(
            [
                sys.executable,
                str(REPO / "integration_test.py"),
                "--input",
                str(DOC1),
                "--workdir",
                str(tmp_path / "work"),
            ],
            capture_output=True,
            text=True,
            cwd=REPO,
        )
        assert proc.returncode == 0, proc.stderr + proc.stdout
        assert "Genesis verify: PASS" in proc.stdout
    # The shipped input must produce real extracted claims, not the fallback.
    candidates_path = tmp_path / "work" / "candidates.jsonl"
    predicates = {
        json.loads(line)["predicate"]
        for line in candidates_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    assert "mentions_money" in predicates
    assert "mentions_date" in predicates
