"""Boundary tests run against the real hash-pinned optional upstream library."""
from importlib import metadata
import json
from pathlib import Path
import socket
import subprocess

import pytest

import obsidian_bridge as bridge
import obsidian_export
import pack

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"


@pytest.fixture
def archive():
    return (FIXTURES / "example.axmpack.zip").read_bytes()


@pytest.fixture
def key():
    return (FIXTURES / "trusted-demo.pub").read_bytes()


@pytest.fixture
def upstream():
    pytest.importorskip("obsidian_wiki", reason="install requirements-obsidian.txt to exercise upstream")
    assert metadata.version("obsidian-wiki") == bridge.PACKAGE_VERSION


def test_real_keyword_search_retains_claim_provenance(archive, key, upstream):
    result = bridge.search_pack(archive, key, "Parquet")
    assert result["backend"] == {"package": "obsidian-wiki", "version": "2026.11.4"}
    assert result["count"] > 0
    claim_hits = [hit for hit in result["hits"] if hit["kind"] == "claim"]
    assert claim_hits
    assert all(hit["shard_id"] == result["head"] for hit in claim_hits)
    assert all(hit["claim_id"].startswith("c1_") for hit in claim_hits)
    canonical_tiers = {(record["id"], claim["claim_id"]): claim["tier"]
                       for record in pack.inspect_pack(archive, key)["records"]
                       for claim in record["claims"]}
    assert all(hit["axm_tier"] == canonical_tiers[(hit["shard_id"], hit["claim_id"])]
               for hit in claim_hits)
    assert all(hit["evidence"] for hit in claim_hits)
    assert all("should_read" not in hit for hit in result["hits"])
    for hit in claim_hits:
        assert hit["path"].startswith("current/")
        for evidence in hit["evidence"]:
            assert evidence["source_hash"] and evidence["byte_end"] > evidence["byte_start"]


def test_head_and_history_have_distinct_search_scopes(archive, key, upstream):
    head = bridge.search_pack(archive, key, "Parquet")
    history = bridge.search_pack(archive, key, "Parquet", scope="history")
    assert head["hits"]
    assert history["hits"] == []
    predecessor = bridge.search_pack(archive, key, "Genesis compiles signs", scope="history")
    assert predecessor["hits"]
    assert all(hit["shard_id"] != head["head"] for hit in predecessor["hits"])
    assert all(hit["role"] == "history" for hit in predecessor["hits"])


def test_context_uses_upstream_but_retains_edition_and_evidence(archive, key, upstream):
    current = bridge.context_pack(archive, key, "Parquet", budget=2000)
    history = bridge.context_pack(archive, key, "Parquet", scope="history", budget=2000)
    assert "Parquet" in current["context"]
    assert "There is no Parquet in the shard" not in history["context"]
    assert current["references"]
    assert all(ref["shard_id"] == current["head"] for ref in current["references"])
    assert current["budget_estimated_tokens"] == 2000
    assert "estimate" in current["budget_method"]
    assert "UNTRUSTED REFERENCE DATA" in current["context"]


def test_single_edition_has_empty_history(key, upstream):
    data = (FIXTURES / "edition-1.axmpack.zip").read_bytes()
    assert bridge.search_pack(data, key, "Genesis", scope="history")["hits"] == []
    assert bridge.context_pack(data, key, "Genesis", scope="history")["context"] == ""


@pytest.mark.parametrize("wrong_key", [False, True])
def test_integrity_failure_precedes_upstream(archive, key, wrong_key, monkeypatch):
    def forbidden():
        pytest.fail("upstream was invoked before pack integrity was accepted")
    monkeypatch.setattr(bridge, "_memory_class", forbidden)
    if wrong_key:
        value = bytearray(key)
        value[0] ^= 1
        key = bytes(value)
    else:
        files = pack.unpack(archive)
        head = pack.loads(files["pack.json"])["head"]
        source = f"shards/{head}/content/source.txt"
        files[source] = b"X" + files[source][1:]
        archive = pack.pack_files(files)
    with pytest.raises(ValueError):
        bridge.search_pack(archive, key, "Genesis")


@pytest.mark.parametrize("limit", [0, -1, 51, True])
def test_limit_is_bounded_before_backend(archive, key, limit):
    with pytest.raises(ValueError, match="limit"):
        bridge.search_pack(archive, key, "Genesis", limit=limit)


@pytest.mark.parametrize("budget", [0, 255, 100001, True])
def test_budget_contract(archive, key, budget):
    with pytest.raises(ValueError, match="budget"):
        bridge.context_pack(archive, key, "Genesis", budget=budget)


def test_pin_is_enforced(monkeypatch):
    monkeypatch.setattr(bridge.metadata, "version", lambda _: "2099.1.1")
    with pytest.raises(bridge.DependencyError, match="qualifies"):
        bridge._memory_class()


def test_missing_dependency_has_actionable_error(monkeypatch):
    def missing(_):
        raise metadata.PackageNotFoundError("obsidian-wiki")
    monkeypatch.setattr(bridge.metadata, "version", missing)
    with pytest.raises(bridge.DependencyError, match="require-hashes"):
        bridge._memory_class()


def test_query_cannot_use_ambient_vault_or_network(archive, key, upstream, tmp_path, monkeypatch):
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    sentinel = unrelated / "private.md"
    sentinel.write_text("DO_NOT_READ_AMBIENT_VAULT_9e81", encoding="utf-8")
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(unrelated))
    before = {path: path.read_bytes() for path in FIXTURES.iterdir() if path.is_file()}
    def forbidden(*args, **kwargs):
        raise AssertionError("retrieval attempted network or process execution")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    result = bridge.context_pack(archive, key, "Genesis", budget=2000)
    assert "DO_NOT_READ_AMBIENT_VAULT_9e81" not in result["context"]
    assert sentinel.read_text() == "DO_NOT_READ_AMBIENT_VAULT_9e81"
    assert {path: path.read_bytes() for path in before} == before


def test_cli_search_and_export_error_are_json(key, upstream, tmp_path, capsys):
    code = bridge.main(["search", str(FIXTURES / "example.axmpack.zip"), "Parquet",
                        "--trusted-key", str(FIXTURES / "trusted-demo.pub")])
    result = json.loads(capsys.readouterr().out)
    assert code == 0 and result["count"] > 0
    bad = tmp_path / "wrong.pub"
    bad.write_bytes(b"x" * 1344)
    destination = tmp_path / "no-output"
    code = bridge.main(["export", str(FIXTURES / "example.axmpack.zip"),
                        "--trusted-key", str(bad), "--output", str(destination)])
    assert code == 2 and json.loads(capsys.readouterr().out)["status"] == "ERROR"
    assert not destination.exists()


def test_cli_verifier_timeout_is_a_json_error(monkeypatch, capsys):
    def timed_out(*args):
        raise subprocess.TimeoutExpired("independent-verifier", 15)
    monkeypatch.setattr(bridge, "render_vault", timed_out)
    code = bridge.main(["search", str(FIXTURES / "example.axmpack.zip"), "Parquet",
                        "--trusted-key", str(FIXTURES / "trusted-demo.pub")])
    result = json.loads(capsys.readouterr().out)
    assert code == 2 and result["status"] == "ERROR"
    assert "timed out" in result["error"]


def test_upstream_reads_unicode_quotes_and_backslashes_from_projection(upstream, tmp_path):
    title = 'Café 東京 "résumé" C:\\notes'
    summary = 'Ελληνικά ภาษาไทย'
    note = obsidian_export._frontmatter({"title": title, "summary": summary})
    (tmp_path / "reference.md").write_text(note + "Ordinary body without search terms.\n",
                                          encoding="utf-8")
    memory = bridge._memory_class()(str(tmp_path), create=False)
    hits = memory.search("Café", limit=8)
    assert len(hits) == 1 and hits[0]["title"] == title
    assert hits[0]["summary"] == summary
    assert memory.search("ภาษาไทย", limit=8)[0]["path"] == "reference.md"
    context = memory.context("Café", budget=2000, recent=False)
    assert title in context and summary in context
