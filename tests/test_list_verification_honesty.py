"""Regression: `axm list` must not call a shard "verified" on faith.

Perimeter-sweep finding axm-core F2 (MEDIUM): `cmd_list` derived its status
from the mere existence of ``sig/manifest.sig`` and printed a "✓", so a shard
with a present-but-unchecked (or invalid) signature was shown as verified.
A green check must mean the genesis verifier returned PASS against an
out-of-band trusted key; absent that, the honest label is "signed".

These tests need only click + a fake shard tree (no kernel): they exercise
the no-trust-anchor path, which must never say "verified".
"""
from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from axm_core.cli import cmd_list


def _fake_shard(home: Path, name: str, *, signed: bool, namespace: str,
                title: str) -> None:
    sd = home / ".axm" / "shards" / name
    (sd / "sig").mkdir(parents=True)
    sd.joinpath("manifest.json").write_text(
        json.dumps({"metadata": {"title": title, "namespace": namespace}}))
    if signed:
        # A signature file that is present but never cryptographically checked.
        sd.joinpath("sig", "manifest.sig").write_bytes(b"\x00" * 2484)


def _combined(result) -> str:
    text = result.output or ""
    try:
        if result.stderr:
            text += result.stderr
    except ValueError:  # older click: stderr already mixed into output
        pass
    return text


def test_list_without_trust_anchor_says_signed_not_verified(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    _fake_shard(tmp_path, "sh1_deadbeef", signed=True,
                namespace="decisions/2026", title="Decision: adopt hybrid suite")

    result = CliRunner().invoke(cmd_list, [])
    assert result.exit_code == 0, _combined(result)
    assert "signed" in result.output
    assert "verified" not in result.output   # signature presence is not verification
    assert "decision" in result.output       # derived from namespace, not a v0.x shard_type


def test_list_verified_filter_requires_a_trusted_key(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    _fake_shard(tmp_path, "sh1_deadbeef", signed=True,
                namespace="general/2026", title="A shard")

    result = CliRunner().invoke(cmd_list, ["--verified"])
    assert result.exit_code != 0             # refuses rather than guess from a signature file
    text = _combined(result)
    assert "trust anchor" in text or "trusted-key" in text
    assert "shard(s)" not in text            # no shard listed/labelled without a trust anchor
