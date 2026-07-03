"""Genesis v1 surface tests for the axm-core runtime.

Covers RFC 0002 (the v1.0 reset) adoption:
  - Spectra mounts canonical-JSONL shards (no Parquet in shards) with the
    verify gate anchored to a pinned trusted key.
  - The v2 gold shard from the sibling axm-genesis checkout mounts and its
    claims are queryable.
  - Shard identity is derived (sh1_ + BLAKE3(manifest bytes)), never stored.
  - The Forge emission path compiles axm-hybrid1 shards end to end,
    including compiler-emitted ext/*.jsonl tables, and the SPOKE_API import
    surface resolves against the installed v1 kernel.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import blake3
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
for sub in ("spectra", "forge"):
    p = str(REPO_ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

GENESIS_ROOT = REPO_ROOT.parent / "axm-genesis"
GOLD_SHARD = GENESIS_ROOT / "shards" / "gold" / "fm21-11-hemorrhage-v2"
GOLD_KEY = GENESIS_ROOT / "keys" / "gold-v2-provisional.pub"

requires_gold = pytest.mark.skipif(
    not (GOLD_SHARD.is_dir() and GOLD_KEY.is_file()),
    reason="sibling axm-genesis checkout with the v2 gold shard not found",
)


def _make_engine(tmp_path: Path, monkeypatch, trusted_key: Path):
    """SpectraEngine with per-test state files and a pinned trusted key."""
    monkeypatch.setenv("SPECTRA_DEV_MODE", "1")  # dev vault key only; verify still runs
    monkeypatch.setenv("SPECTRA_TRUSTED_PUBKEY", str(trusted_key))
    from axiom_runtime.engine import SpectraEngine

    return SpectraEngine(
        db_path=str(tmp_path / "spectra.db"),
        audit_path=str(tmp_path / "audit.jsonl"),
        cache_path=str(tmp_path / "cache.jsonl"),
    )


@requires_gold
def test_mount_gold_v2_shard_and_query_claim(tmp_path, monkeypatch):
    """Verify-gated mount of the v2 gold shard; query a known claim."""
    eng = _make_engine(tmp_path, monkeypatch, GOLD_KEY)
    spec = eng.mount_shard(str(GOLD_SHARD))

    # Identity is derived from the manifest bytes (spec section 9).
    expected_id = "sh1_" + blake3.blake3((GOLD_SHARD / "manifest.json").read_bytes()).hexdigest()
    assert spec.shard_id == expected_id
    assert spec.spec_version == "1.0.0"

    manifest = json.loads((GOLD_SHARD / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["suite"] == "axm-hybrid1"
    assert "shard_id" not in manifest

    # All four core tables mounted from JSONL.
    prefixes = sorted(t.split("__")[0] for t in spec.tables)
    assert prefixes == ["claims", "entities", "provenance", "spans"]

    # Query a claim through the read-only SQL gate; row counts must match
    # the signed statistics.
    res = eng.query_json("SELECT claim_id, subject, predicate, object, tier FROM claims ORDER BY claim_id")
    assert len(res["rows"]) == manifest["statistics"]["claims"]
    claim_id = res["rows"][0][0]
    assert claim_id.startswith("c1_") and len(claim_id) == 55

    res_e = eng.query_json("SELECT count(*) FROM entities")
    assert res_e["rows"][0][0] == manifest["statistics"]["entities"]

    # Evidence joins: every provenance row resolves to its claim and its
    # span text (in the v2 gold shard one claim carries no provenance row,
    # which the kernel permits — so the anchor count is provenance rows).
    n_prov = eng.query_json("SELECT count(*) FROM provenance")["rows"][0][0]
    res_j = eng.query_json(
        """
        SELECT c.claim_id, s.text
        FROM claims c
        JOIN provenance p ON p.claim_id = c.claim_id
        JOIN spans s ON s.source_hash = p.source_hash
         AND s.byte_start = p.byte_start AND s.byte_end = p.byte_end
        ORDER BY c.claim_id
        """
    )
    assert len(res_j["rows"]) == n_prov > 0
    assert all(isinstance(text, str) and text for _cid, text in res_j["rows"])


@requires_gold
def test_mount_rejects_tampered_gold_copy(tmp_path, monkeypatch):
    """The verify gate fails closed on a single flipped content byte."""
    import shutil

    tampered = tmp_path / "tampered"
    shutil.copytree(GOLD_SHARD, tampered)
    src = tampered / "content" / "source.txt"
    data = bytearray(src.read_bytes())
    data[0] ^= 0xFF
    src.write_bytes(bytes(data))

    eng = _make_engine(tmp_path, monkeypatch, GOLD_KEY)
    with pytest.raises(ValueError, match="Constitution check failed"):
        eng.mount_shard(str(tampered))


def _compile_test_shard(base: Path):
    """Compile a small axm-hybrid1 shard with a throwaway keypair.

    Returns (shard_dir, trusted_key_path).
    """
    from axm_build.compiler_generic import CompilerConfig, compile_generic_shard
    from axm_build.sign import HYBRID1_PK_LEN, HYBRID1_SK_LEN, hybrid1_keygen

    public_key, private_key = hybrid1_keygen()
    assert len(public_key) == HYBRID1_PK_LEN == 1344
    assert len(private_key) == HYBRID1_SK_LEN == 3904

    (base / "source.txt").write_text(
        "Tourniquets stop severe bleeding.\n"
        "The protocol takes effect on 2026-01-01 for all teams.\n",
        encoding="utf-8",
    )
    candidates = [
        {
            "subject": "tourniquet", "predicate": "treats", "object": "severe bleeding",
            "object_type": "entity", "tier": 3,
            "evidence": "Tourniquets stop severe bleeding.",
        },
        {
            "subject": "protocol", "predicate": "effective_date", "object": "2026-01-01",
            "object_type": "literal:string", "tier": 0,
            "evidence": "The protocol takes effect on 2026-01-01 for all teams.",
            "valid_from": "2026-01-01T00:00:00Z", "valid_until": "",
            "temporal_context": "effective_date: 2026-01-01",
        },
    ]
    with (base / "candidates.jsonl").open("w", encoding="utf-8") as f:
        for c in candidates:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    shard_dir = base / "shard"
    cfg = CompilerConfig(
        source_path=base / "source.txt",
        candidates_path=base / "candidates.jsonl",
        out_dir=shard_dir,
        private_key=private_key,
        publisher_id="@axm_core_tests",
        publisher_name="axm-core test suite",
        namespace="test/v1-mount",
        created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        title="v1 Mount Test Shard",
        license_spdx="CC0-1.0",
    )
    assert compile_generic_shard(cfg), "compiler rejected its own output"

    key_path = base / "trusted.pub"
    key_path.write_bytes(public_key)
    return shard_dir, key_path


def test_compile_verify_mount_roundtrip_with_ext_jsonl(tmp_path, monkeypatch):
    """Forge-style roundtrip: hybrid1 compile → verify PASS → JSONL mount →
    query core and ext tables."""
    from axm_verify.logic import verify_shard

    shard_dir, key_path = _compile_test_shard(tmp_path)

    result = verify_shard(shard_dir, key_path)
    assert result["status"] == "PASS", result["errors"]
    assert result["profiles_checked"] == []
    assert result["profiles_unchecked"] == []

    # Core tables are canonical JSONL; no Parquet anywhere in the shard.
    assert (shard_dir / "graph" / "claims.jsonl").is_file()
    assert not list(shard_dir.rglob("*.parquet"))
    # The temporal candidate keys became a sealed kernel-registry ext table.
    assert (shard_dir / "ext" / "temporal@1.jsonl").is_file()

    eng = _make_engine(tmp_path, monkeypatch, key_path)
    spec = eng.mount_shard(str(shard_dir))
    assert any(t.startswith("ext_temporal__") for t in spec.tables)

    res = eng.query_json(
        "SELECT object FROM claims WHERE predicate = ? AND object_type = 'entity'",
        ["treats"],
    )
    assert len(res["rows"]) == 1

    res_t = eng.query_json(
        """
        SELECT t.valid_from, c.predicate
        FROM temporal t JOIN claims c ON c.claim_id = t.claim_id
        """
    )
    assert res_t["rows"] == [("2026-01-01T00:00:00Z", "effective_date")]


def test_mount_skips_opaque_ext_formats(tmp_path, monkeypatch, capsys):
    """ext/ is opaque: unknown/binary ext files are skipped with a log line,
    while registered JSONL ext tables still mount.

    The binary file is injected only into the runtime's view of an already
    verified shard (verification is monkeypatched to PASS for this case, so
    the test isolates the mount-path tolerance rather than kernel layout
    rules)."""
    shard_dir, key_path = _compile_test_shard(tmp_path)
    (shard_dir / "ext" / "streams@1.bin").write_bytes(b"\x00\x01\x02opaque")

    eng = _make_engine(tmp_path, monkeypatch, key_path)
    monkeypatch.setattr(eng, "_verify_constitution", lambda _dir: None)
    spec = eng.mount_shard(str(shard_dir))

    captured = capsys.readouterr()
    assert "Skipping opaque ext file (not JSONL): ext/streams@1.bin" in captured.err
    assert any(t.startswith("ext_temporal__") for t in spec.tables)
    assert not any("streams" in t for t in spec.tables)


def test_spoke_api_import_surface():
    """Every genesis-facing name SPOKE_API.md lists must import from v1."""
    from axm_build.compiler_generic import CompilerConfig, compile_generic_shard  # noqa: F401
    from axm_build.merkle import compute_merkle_root  # noqa: F401  # drift-ok: surface test — imports the kernel's own compute_merkle_root to assert SPOKE_API.md's surface exists, not a reimplementation
    from axm_build.sign import (  # noqa: F401
        HYBRID1_PK_LEN,
        HYBRID1_SIG_LEN,
        HYBRID1_SK_LEN,
        SUITE_HYBRID1,
        hybrid1_keygen,
        hybrid1_public_key,
        hybrid1_sign,
        hybrid1_verify,
    )
    from axm_verify.identity import recompute_claim_id, recompute_entity_id  # noqa: F401
    from axm_verify.logic import verify_shard  # noqa: F401

    assert SUITE_HYBRID1 == "axm-hybrid1"
    assert (HYBRID1_SK_LEN, HYBRID1_PK_LEN, HYBRID1_SIG_LEN) == (3904, 1344, 2484)

    # Core runtime surfaces listed in SPOKE_API.md.
    from axiom_runtime.engine import SpectraEngine  # noqa: F401
    from axiom_runtime.nlquery import (  # noqa: F401
        natural_language_to_query,
        natural_language_to_sql,
    )
    from axm_forge.emission.genesis_emission import (  # noqa: F401
        EmissionConfig,
        emit_genesis_shard,
    )
    from axm_forge.ingestion.extractors import (  # noqa: F401
        DocumentBlock,
        ExtractedDocument,
        extract,
        extract_chat_json,
    )
