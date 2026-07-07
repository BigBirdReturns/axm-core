"""Residual Exit v0 — source / apps / policy planks, sealed at their honest tiers."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from foundry_exit import residual_exit as R

requires_kernel = pytest.mark.skipif(
    not R.SC.kernel_available(), reason="axm-genesis kernel not on PATH"
)


def test_source_builder_seals_source_and_attests_runtime_not_carried():
    cands, src, content, tallies = R._build_source(R.DEFAULT_CAPTURE["source"])
    assert tallies["source_files"] == 2
    preds = {c["predicate"] for c in cands if c.get("type") == "claim"}
    assert {"sealed", "language", "carries", "not_carried"} <= preds
    notc = [c for c in cands if c.get("predicate") == "not_carried"][0]
    assert "runtime" in notc["object_label"].lower()
    # source files are carried verbatim in content
    assert any(k.endswith("flights_clean.py") for k in content)


def test_apps_builder_seals_slate_and_attests_workshop_aip():
    cands, src, content, tallies = R._build_apps(R.DEFAULT_CAPTURE["apps"])
    assert tallies["apps"] == 3
    assert tallies["sealed_exports"] == 1        # the Slate app
    assert tallies["attested_no_export"] == 2    # workshop + aip
    exports = [c["object_label"] for c in cands if c.get("predicate") == "export"]
    assert any("sealed verbatim" in e for e in exports)
    assert any("no published export" in e for e in exports)


def test_policy_builder_is_a_deliberate_non_port_attestation():
    cands, src, content, tallies = R._build_policy(R.DEFAULT_CAPTURE["policy"])
    claims = {c["predicate"]: c["object_label"] for c in cands if c.get("type") == "claim"}
    assert "by design" in claims["foundry_permission_model_ported"]
    assert "own policy" in claims["authorization_reconstructed_under"]
    assert "consent_gate" in claims
    # the customer's own destination policy is sealed verbatim
    assert "policy.md" in content


@requires_kernel
@pytest.mark.parametrize("kind", ["source", "apps", "policy"])
def test_each_kind_seals_and_verifies(kind, tmp_path):
    packet = R.run(kind, R.DEFAULT_CAPTURE[kind], tmp_path / kind)
    assert packet["verification"]["status"] == "PASS"
    assert packet["shard_id"].startswith("sh1_")
    assert packet["kind"] == kind


@requires_kernel
def test_slate_export_is_byte_identical(tmp_path):
    import tempfile
    from foundry_exit import _seal_common as SC
    cands, src, content, _ = R._build_apps(R.DEFAULT_CAPTURE["apps"])
    work = Path(tempfile.mkdtemp())
    sealed = SC.seal(cands, src, content, work / "shard", namespace=R.NAMESPACE["apps"], title="t")
    got = (Path(sealed.shard_dir) / "content" / "slate__orderBoard.json").read_bytes()
    assert got == (R.DEFAULT_CAPTURE["apps"] / "slate" / "orderBoard.json").read_bytes()
