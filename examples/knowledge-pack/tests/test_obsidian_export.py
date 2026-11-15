"""Projection boundary tests using the retained signed knowledge-pack fixtures."""

import copy
import json
from pathlib import Path

import pytest

import obsidian_export
import pack


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture(scope="module")
def archive():
    return (FIXTURES / "example.axmpack.zip").read_bytes()


@pytest.fixture(scope="module")
def trusted_key():
    return (FIXTURES / "trusted-demo.pub").read_bytes()


@pytest.fixture(scope="module")
def inspected(archive, trusted_key):
    verdict = pack.inspect_pack(archive, trusted_key)
    assert verdict["status"] == "PASS"
    return verdict


@pytest.fixture(scope="module")
def projection(archive, trusted_key):
    return obsidian_export.render_vault(archive, trusted_key)


def frontmatter(content):
    header, body = content.decode("utf-8")[4:].split("\n---\n", 1)
    values = {}
    lines = iter(header.splitlines())
    for line in lines:
        key, value = line.split(": ", 1)
        if value == ">-":
            content = next(lines)
            assert content.startswith("  ")
            values[key] = content[2:]
        else:
            values[key] = json.loads(value)
    return values, body


def tree_bytes(root):
    return {
        file.relative_to(root).as_posix(): file.read_bytes()
        for file in root.rglob("*") if file.is_file()
    }


def test_deterministic_projection_and_identity(archive, trusted_key, projection):
    files, metadata = projection
    again, second_metadata = obsidian_export.render_vault(archive, trusted_key)
    assert files == again
    assert metadata == second_metadata
    assert list(files) == sorted(files)
    assert metadata["schema"] == "axm-obsidian-projection/0.1"
    assert metadata["archive_sha256"] == pack.digest(archive)
    assert metadata["trusted_key_sha256"] == pack.digest(trusted_key)
    assert metadata["counts"]["files"] == len(files)
    assert json.loads(files["_axm/projection.json"]) == metadata
    assert "milliseconds" not in metadata


def test_sources_and_original_archive_are_byte_exact(archive, inspected, projection):
    files, _ = projection
    raw = pack.unpack(archive)
    assert files["_axm/source.axmpack.zip"] == archive
    assert not any(path.endswith(".pub") for path in files)
    for record in inspected["records"]:
        for source in record["sources"]:
            original = raw[f"shards/{record['id']}/{source['path']}"]
            projected = files[f"_axm/sources/{record['id']}/{source['path']}"]
            assert projected == original
            assert pack.digest(projected) == source["hash"]
        original = files[f"_axm/sources/{record['id']}/content/original.txt"]
        normalized = files[f"_axm/sources/{record['id']}/content/source.txt"]
        assert b"\r\n" in original and b"\r\n" not in normalized


def test_head_and_history_keep_composite_claim_identity(inspected, projection):
    files, metadata = projection
    pages = metadata["pages"]
    pairs = set()
    claims_by_record = []
    for record in inspected["records"]:
        sid = record["id"]
        role = "current" if sid == inspected["head"] else "history"
        record_path = f"{role}/record-{sid}.md"
        header, body = frontmatter(files[record_path])
        assert pages[record_path]["kind"] == "record"
        assert header["axm_shard_id"] == sid
        assert header["axm_role"] == role
        assert header["created"] == record["created_at"]
        assert header["axm_supersedes"] == record["supersedes"]
        assert ("Current edition" if role == "current" else "Historical predecessor") in body
        claims_by_record.append({claim["claim_id"] for claim in record["claims"]})
        for claim in record["claims"]:
            cid = claim["claim_id"]
            path = f"{role}/claim-{sid}-{cid}.md"
            pair = (sid, cid)
            assert pair not in pairs
            pairs.add(pair)
            page = pages[path]
            header, body = frontmatter(files[path])
            assert (page["shard_id"], page["claim_id"]) == pair
            assert header["axm_claim_id"] == cid
            assert header["axm_role"] == role
            assert header["axm_tier"] == claim["tier"]
            assert page["axm_tier"] == claim["tier"]
            assert "tier" not in header
            assert "valid_until" not in header and "superseded_by" not in header
            assert f"[[record-{sid}|record]]" in body
            for evidence in page["evidence"]:
                source = files[f"_axm/sources/{sid}/{evidence['source']}"]
                start, end = evidence["byte_start"], evidence["byte_end"]
                assert source[start:end].decode("utf-8") == evidence["text"]
                assert evidence["text"] in body
                assert evidence["source_hash"] in body
                assert evidence["provenance_id"] in body
                assert f"[{start}, {end})" in body
    assert claims_by_record[0] & claims_by_record[1], "Fixture must exercise a repeated claim across editions"
    assert len(pairs) == metadata["counts"]["claims"] == 4
    assert all(page["shard_id"] == metadata["head"] for path, page in pages.items() if path.startswith("current/"))  # drift-ok: compare verified-derived IDs
    assert all(page["shard_id"] != metadata["head"] for path, page in pages.items() if path.startswith("history/"))


def test_canvas_identity_paths_and_predecessor_direction(inspected, projection):
    files, metadata = projection
    canvas = json.loads(files["Knowledge.canvas"])
    assert set(canvas) == {"nodes", "edges"}
    nodes = {node["id"]: node for node in canvas["nodes"]}
    assert len(nodes) == len(canvas["nodes"]) == len(metadata["pages"])
    assert {node["file"] for node in nodes.values()} == set(metadata["pages"])
    for node in nodes.values():
        assert node["type"] == "file" and node["file"] in files
        assert all(type(node[field]) is int for field in ("x", "y", "width", "height"))
        assert node["width"] > 0 and node["height"] > 0
    edge_ids = set()
    for edge in canvas["edges"]:
        assert edge["id"] not in edge_ids
        edge_ids.add(edge["id"])
        assert edge["fromNode"] in nodes and edge["toNode"] in nodes
        assert edge["toEnd"] == "arrow"
    predecessor_edges = {
        (edge["fromNode"], edge["toNode"])
        for edge in canvas["edges"] if edge["label"] == "supersedes"
    }
    expected = {
        (f"record-{record['id']}", f"record-{previous}")
        for record in inspected["records"] for previous in record["supersedes"]
    }
    assert predecessor_edges == expected
    assert all(source == "record-" + metadata["head"] for source, _ in predecessor_edges)


def test_native_base_scopes_claims_by_edition(projection):
    files, _ = projection
    base = json.loads(files["Evidence.base"])
    assert base["filters"] == {"and": ['file.hasTag("axm-claim")']}
    assert len(base["views"]) == 2
    assert {view["type"] for view in base["views"]} == {"table"}
    assert {view["filters"]["and"][0] for view in base["views"]} == {
        'file.inFolder("current")', 'file.inFolder("history")',
    }
    assert all("note.axm_tier" in view["order"] for view in base["views"])


@pytest.mark.parametrize("mode", ["source", "wrong-key"])
def test_real_verification_failure_produces_no_output(mode, archive, trusted_key, tmp_path):
    data, key = archive, trusted_key
    if mode == "wrong-key":
        key = bytes([key[0] ^ 1]) + key[1:]
    else:
        raw = pack.unpack(data)
        head = pack.loads(raw["pack.json"])["head"]
        name = f"shards/{head}/content/source.txt"
        raw[name] = bytes([raw[name][0] ^ 1]) + raw[name][1:]
        data = pack.pack_files(raw)
    target = tmp_path / "vault"
    with pytest.raises(ValueError, match="requires a PASS"):
        obsidian_export.export_vault(data, key, target)
    assert list(tmp_path.iterdir()) == []


def test_verifier_gate_runs_before_projection_derivation(monkeypatch, tmp_path):
    calls = []
    def failed_inspection(data, key, go_binary):
        calls.append((data, key, go_binary))
        return {"status": "FAIL"}
    def forbidden_unpack(_):
        pytest.fail("Projection unpack happened before a PASS")
    monkeypatch.setattr(pack, "inspect_pack", failed_inspection)
    monkeypatch.setattr(pack, "unpack", forbidden_unpack)
    with pytest.raises(ValueError, match="PASS"):
        obsidian_export.export_vault(b"archive", b"anchor", tmp_path / "vault", "go-verifier")
    assert calls == [(b"archive", b"anchor", "go-verifier")]
    assert list(tmp_path.iterdir()) == []


def test_profiles_unchecked_remain_explicit(archive, trusted_key, inspected, monkeypatch):
    verdict = copy.deepcopy(inspected)
    verdict["checks"][0]["profiles_unchecked"] = ["domain@1", "other@1", "domain@1"]
    verdict["checks"][1]["profiles_unchecked"] = ["domain@1"]
    monkeypatch.setattr(pack, "inspect_pack", lambda *args: verdict)
    files, metadata = obsidian_export.render_vault(archive, trusted_key)
    assert metadata["profiles_unchecked"] == ["domain@1", "other@1"]
    for path, page in metadata["pages"].items():
        header, body = frontmatter(files[path])
        assert header["axm_profiles_unchecked"] == page["profiles_unchecked"]
        assert "domain@1" in body
    assert "real-world currentness" in files["index.md"].decode()


def test_signed_title_cannot_inject_frontmatter(archive, trusted_key, inspected, monkeypatch):
    verdict = copy.deepcopy(inspected)
    title = 'A "quote"\naxm_role: history\n---\n[[untrusted]]: \u2603'
    verdict["records"][0]["title"] = title
    monkeypatch.setattr(pack, "inspect_pack", lambda *args: verdict)
    files, metadata = obsidian_export.render_vault(archive, trusted_key)
    sid = verdict["records"][0]["id"]
    path = f"history/record-{sid}.md"
    header, _ = frontmatter(files[path])
    assert header["title"] == 'A "quote" axm_role: history --- [[untrusted]]: \u2603'
    assert header["axm_role"] == "history"
    assert metadata["pages"][path]["title"] == title


def test_atomic_export_and_idempotence(archive, trusted_key, projection, tmp_path):
    target = tmp_path / "vault"
    first = obsidian_export.export_vault(archive, trusted_key, target)
    assert first["idempotent"] is False
    assert tree_bytes(target) == projection[0]
    before = {path: (target / path).stat().st_mtime_ns for path in projection[0]}
    second = obsidian_export.export_vault(archive, trusted_key, target)
    assert second["idempotent"] is True
    assert tree_bytes(target) == projection[0]
    assert {path: (target / path).stat().st_mtime_ns for path in projection[0]} == before
    assert not any(path.name.startswith(".axm-projection-") for path in tmp_path.iterdir())


@pytest.mark.parametrize("change", ["edit", "extra-file", "extra-directory", "missing-file"])
def test_existing_projection_changes_are_preserved_and_refused(change, archive, trusted_key, tmp_path):
    target = tmp_path / "vault"
    obsidian_export.export_vault(archive, trusted_key, target)
    if change == "edit":
        (target / "index.md").write_bytes(b"User edit\n")
    elif change == "extra-file":
        (target / "personal.md").write_bytes(b"Unowned content\n")
    elif change == "extra-directory":
        (target / "empty-personal-folder").mkdir()
    else:
        (target / "index.md").unlink()
    before = tree_bytes(target)
    with pytest.raises(FileExistsError, match="not an identical"):
        obsidian_export.export_vault(archive, trusted_key, target)
    assert tree_bytes(target) == before


def test_existing_empty_unowned_directory_is_refused(archive, trusted_key, tmp_path):
    target = tmp_path / "vault"
    target.mkdir()
    with pytest.raises(FileExistsError):
        obsidian_export.export_vault(archive, trusted_key, target)
    assert list(target.iterdir()) == []


def test_target_symlink_is_refused(archive, trusted_key, tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    target = tmp_path / "vault"
    target.symlink_to(actual, target_is_directory=True)
    with pytest.raises(FileExistsError, match="symlink"):
        obsidian_export.export_vault(archive, trusted_key, target)
    assert target.is_symlink() and list(actual.iterdir()) == []


def test_symlink_inside_projection_is_refused(archive, trusted_key, tmp_path):
    target = tmp_path / "vault"
    obsidian_export.export_vault(archive, trusted_key, target)
    outside = tmp_path / "personal.md"
    outside.write_bytes((target / "index.md").read_bytes())
    (target / "index.md").unlink()
    (target / "index.md").symlink_to(outside)
    with pytest.raises(FileExistsError):
        obsidian_export.export_vault(archive, trusted_key, target)
    assert (target / "index.md").is_symlink()
    assert outside.read_bytes().startswith(b"---\n")


def test_racing_empty_target_is_never_replaced(archive, trusted_key, tmp_path, monkeypatch):
    target = tmp_path / "vault"
    rename = obsidian_export._rename_new
    def race(source, destination):
        destination.mkdir()
        rename(source, destination)
    monkeypatch.setattr(obsidian_export, "_rename_new", race)
    with pytest.raises(FileExistsError, match="appeared and differs"):
        obsidian_export.export_vault(archive, trusted_key, target)
    assert target.is_dir() and list(target.iterdir()) == []
    assert not any(path.name.startswith(".axm-projection-") for path in tmp_path.iterdir())


def test_staging_failure_leaves_no_partial_destination(archive, trusted_key, tmp_path, monkeypatch):
    target = tmp_path / "vault"
    original = Path.write_bytes
    def fail_on_index(path, content):
        if path.name == "index.md":
            raise OSError("simulated write failure")
        return original(path, content)
    monkeypatch.setattr(Path, "write_bytes", fail_on_index)
    with pytest.raises(OSError, match="simulated"):
        obsidian_export.export_vault(archive, trusted_key, target)
    assert list(tmp_path.iterdir()) == []
