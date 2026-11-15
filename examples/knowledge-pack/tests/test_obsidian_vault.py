"""Real vault round-trip tests using Genesis, pinned upstream, and native Go."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket

import pytest

from axm_build.sign import hybrid1_keygen
import obsidian_bridge as bridge
import obsidian_vault as vault
import pack

ROOT = Path(__file__).resolve().parents[1]
GO = str(ROOT / "build" / ("axm-verify-go.exe" if os.name == "nt" else "axm-verify-go"))


@pytest.fixture(scope="module")
def keys():
    return hybrid1_keygen()


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "vault"
    (root / "Research").mkdir(parents=True)
    (root / "Research" / "Café 東京.md").write_bytes(
        '\ufeff---\r\ntitle: Café 東京\r\n---\r\n# Protocol\r\n\r\nOldbeacon alpha remains the accepted reference.\r\n\r\nSee [[Duplicate]].\r\n'.encode("utf-8"))
    (root / "Duplicate.md").write_bytes(b"Repeated content.\n")
    (root / "Copy.md").write_bytes(b"Repeated content.\n")
    (root / "Empty.md").write_bytes(b"")
    (root / ".obsidian").mkdir()
    (root / ".obsidian" / "private.md").write_bytes(b"HIDDEN_SECRET\n")
    (root / "AGENTS.md").write_bytes(b"EXECUTE_UNTRUSTED_INSTRUCTION\n")
    (root / "image.png").write_bytes(b"Not a parsed attachment")
    return root


def make_plan(source, stage, keys, previous=None, **kwargs):
    return vault.plan_vault(source, stage, keys[0], vault_id="test-vault", publisher_id="test-publisher",
                            publisher_name="Ephemeral test publisher", previous=previous,
                            created_at="2026-11-15T07:00:00Z", **kwargs)


def seal(source, stage, keys, previous=None, **kwargs):
    planned = make_plan(source, stage, keys, previous)
    data, receipt = vault.seal_vault(source, stage, planned["approval_sha256"], keys[1], keys[0],
                                    previous=previous, **kwargs)
    return data, receipt


def source_bytes(source):
    return {p.relative_to(source).as_posix(): p.read_bytes() for p in source.rglob("*")
            if p.is_file() and vault.MANAGED not in p.relative_to(source).parts}


def test_real_initial_snapshot_retains_byte_exact_unicode_crlf_and_empty(source, tmp_path, keys):
    before = source_bytes(source)
    data, receipt = seal(source, tmp_path / "review", keys)
    verdict, index, original = vault.read_snapshot(data, keys[0])
    assert verdict["status"] == "PASS" and receipt["notes"] == 4
    assert index["publisher"]["id"] == "test-publisher"
    assert original == {p: raw for p, raw in before.items() if p in original}
    assert original["Empty.md"] == b""
    assert original["Research/Café 東京.md"].startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in original["Research/Café 東京.md"]
    assert "AGENTS.md" not in original and "image.png" not in original
    assert source_bytes(source) == before
    claims = verdict["records"][-1]["claims"]
    assert len(claims) == 4  # Three nonempty note captures plus the inventory.
    duplicate = [c for c in claims if c["object"] == "Repeated content."]
    assert len(duplicate) == 2 and len({c["claim_id"] for c in duplicate}) == 2
    raw = pack.unpack(data)
    head = verdict["head"]
    approved = raw[f"shards/{head}/content/approved-plan.json"]
    assert vault._sha(approved) == receipt["approval_sha256"] == index["approved_plan_sha256"]
    for claim in claims:
        for evidence in claim["evidence"]:
            content = raw[f"shards/{head}/{evidence['source']}"]
            assert content[evidence["byte_start"]:evidence["byte_end"]].decode() == evidence["text"]


def test_review_is_required_before_compiler(source, tmp_path, keys, monkeypatch):
    stage = tmp_path / "review"
    make_plan(source, stage, keys)
    def forbidden(*args, **kwargs):
        pytest.fail("Compiler reached without matching approval")
    monkeypatch.setattr("axm_build.compiler_generic.compile_generic_shard", forbidden)
    with pytest.raises(ValueError, match="approval"):
        vault.seal_vault(source, stage, "0" * 64, keys[1], keys[0])


@pytest.mark.parametrize("edit", ["source", "staged", "review", "plan", "selection"])
def test_changes_after_review_fail_closed(edit, source, tmp_path, keys):
    stage = tmp_path / "review"
    planned = make_plan(source, stage, keys)
    if edit == "source":
        (source / "Duplicate.md").write_bytes(b"New unreviewed instruction.\n")
    elif edit == "staged":
        next((stage / "notes").glob("*.md")).write_bytes(b"Forged staged source")
    elif edit == "review":
        (stage / "review.md").write_text("Harmless-looking altered review.")
    elif edit == "plan":
        (stage / "plan.json").write_text((stage / "plan.json").read_text() + " ")
    else:
        (source / ".okignore").write_text("Duplicate.md\n")
    with pytest.raises(ValueError):
        vault.seal_vault(source, stage, planned["approval_sha256"], keys[1], keys[0])


def test_previous_pack_is_bound_to_review(source, tmp_path, keys):
    first, _ = seal(source, tmp_path / "first", keys)
    stage = tmp_path / "second"
    planned = make_plan(source, stage, keys, first)
    with pytest.raises(ValueError, match="Previous pack"):
        vault.seal_vault(source, stage, planned["approval_sha256"], keys[1], keys[0])


def test_successor_preserves_predecessor_and_handles_edit_delete_rename(source, tmp_path, keys):
    first, one = seal(source, tmp_path / "first", keys)
    (source / "Research/Café 東京.md").write_text("# Replacement\nNewbeacon beta is the revised reference.\n", encoding="utf-8")
    (source / "Copy.md").unlink()
    (source / "Duplicate.md").rename(source / "Renamed.md")
    (source / "Added.md").write_text("A new note.\n")
    second, two = seal(source, tmp_path / "second", keys, first)
    old_files, new_files = pack.unpack(first), pack.unpack(second)
    assert all(new_files[k] == v for k, v in old_files.items() if k != "pack.json")
    verdict, index, originals = vault.read_snapshot(second, keys[0])
    assert verdict["records"][-1]["supersedes"] == [one["head"]]
    assert two["editions"] == 2 and two["head"] != one["head"]
    assert "Copy.md" not in originals and "Duplicate.md" not in originals and "Renamed.md" in originals
    assert {c["kind"] for c in index["changes"]} == {"added", "deleted", "modified"}
    # Two indistinguishable removed originals make rename attribution ambiguous.
    assert bridge.search_pack(second, keys[0], "Newbeacon")["hits"]
    assert bridge.search_pack(second, keys[0], "Oldbeacon")["hits"] == []
    assert bridge.search_pack(second, keys[0], "Oldbeacon", scope="history")["hits"]
    assert "Oldbeacon" not in bridge.context_pack(second, keys[0], "Newbeacon")["context"]


def test_unambiguous_rename_is_reported(source, tmp_path, keys):
    first, _ = seal(source, tmp_path / "first", keys)
    (source / "Research/Café 東京.md").rename(source / "Research/Renamed.md")
    planned = make_plan(source, tmp_path / "second", keys, first)
    assert planned["changes"] == [{"kind": "renamed", "from": "Research/Café 東京.md", "path": "Research/Renamed.md",
                                    "sha256": vault._sha((source / "Research/Renamed.md").read_bytes())}]


def test_no_change_reuses_exact_existing_archive(source, tmp_path, keys):
    first, one = seal(source, tmp_path / "first", keys)
    second, two = seal(source, tmp_path / "second", keys, first)
    assert second == first and two["status"] == "UNCHANGED" and not two["new_edition"]
    assert two["head"] == one["head"]


def test_empty_successor_explicitly_records_all_deletions(source, tmp_path, keys):
    first, _ = seal(source, tmp_path / "first", keys)
    for p in [source / "Duplicate.md", source / "Copy.md", source / "Empty.md", source / "Research/Café 東京.md"]:
        p.unlink()
    second, receipt = seal(source, tmp_path / "second", keys, first)
    assert receipt["notes"] == 0 and receipt["claims"] == 1
    assert vault.read_snapshot(second, keys[0])[2] == {}
    assert len(receipt["changes"]) == 4


def test_refresh_adds_views_inside_original_vault_without_source_writes(source, tmp_path, keys):
    data, receipt = seal(source, tmp_path / "plan", keys)
    before = source_bytes(source)
    result = vault.refresh_vault(source, data, keys[0])
    assert source_bytes(source) == before
    assert result["source_files_written"] == 0
    assert result["working_copy"]["status"] == "MATCHES_CAPTURED_NOTES"
    generated = Path(result["projection"])
    prefix = generated.relative_to(source).as_posix()
    canvas = json.loads((generated / "Knowledge.canvas").read_text())
    assert all(node["file"].startswith(prefix + "/") and (source / node["file"]).is_file() for node in canvas["nodes"])
    base = json.loads((generated / "Evidence.base").read_text())
    assert base["views"][0]["filters"]["and"] == [f'file.inFolder("{prefix}/current")']
    assert (source / vault.MANAGED / "Evidence editions.base").is_file()
    assert receipt["head"] in next((source / vault.MANAGED / "updates").glob("*.md")).read_text()
    assert vault.scan_vault(source)[0] == json.loads((tmp_path / "plan/plan.json").read_text())["notes"]


def test_refresh_preserves_annotations_edited_views_and_obisidian_layout(source, tmp_path, keys):
    first, one = seal(source, tmp_path / "first", keys)
    a = vault.refresh_vault(source, first, keys[0])
    managed = source / vault.MANAGED
    annotation = managed / "Notes.md"
    annotation.write_bytes(b"MY_UNVERIFIED_ANNOTATION\n")
    layout = managed / "Evidence editions.base"
    layout.write_bytes(b"MY_CUSTOM_BASE_LAYOUT\n")
    generated_note = Path(a["projection"]) / "index.md"
    generated_note.write_bytes(b"MY_VIEW_EDIT\n")
    (source / "Duplicate.md").write_text("Newbeacon beta.\n")
    second, two = seal(source, tmp_path / "second", keys, first)
    b = vault.refresh_vault(source, second, keys[0])
    assert annotation.read_bytes() == b"MY_UNVERIFIED_ANNOTATION\n"
    assert layout.read_bytes() == b"MY_CUSTOM_BASE_LAYOUT\n"
    assert generated_note.read_bytes() == b"MY_VIEW_EDIT\n"
    assert b["preserved_previous_view_edits"] == ["index.md"]
    assert one["head"] != two["head"] and b["head"] == two["head"]
    assert bridge.search_pack(second, keys[0], "MY_UNVERIFIED_ANNOTATION")["hits"] == []
    assert bridge.search_pack(second, keys[0], "MY_VIEW_EDIT")["hits"] == []
    assert len(list((managed / "updates").glob("*.md"))) == 2
    again = vault.refresh_vault(source, second, keys[0])
    assert again["head"] == two["head"]


def test_same_edition_refresh_preserves_modified_view(source, tmp_path, keys):
    data, _ = seal(source, tmp_path / "plan", keys)
    result = vault.refresh_vault(source, data, keys[0])
    index = Path(result["projection"]) / "index.md"
    index.write_text("Edited locally.")
    retried = vault.refresh_vault(source, data, keys[0])
    assert retried["current_view_edits"] == ["index.md"]
    assert index.read_text() == "Edited locally."


def test_source_edit_stays_unsigned_until_new_approval(source, tmp_path, keys):
    data, _ = seal(source, tmp_path / "plan", keys)
    (source / "Duplicate.md").write_text("UNAPPROVED_ASSERTION\n")
    status = vault.vault_status(source, data, keys[0])
    assert status["status"] == "WORKING_COPY_CHANGED" and status["changes"][0]["kind"] == "modified"
    refreshed = vault.refresh_vault(source, data, keys[0])
    assert refreshed["working_copy"]["status"] == "WORKING_COPY_CHANGED"
    assert bridge.search_pack(data, keys[0], "UNAPPROVED_ASSERTION")["hits"] == []


def test_rollback_and_divergent_branch_are_refused(source, tmp_path, keys):
    first, _ = seal(source, tmp_path / "first", keys)
    vault.refresh_vault(source, first, keys[0])
    (source / "Duplicate.md").write_text("Branch A")
    second, _ = seal(source, tmp_path / "second", keys, first)
    vault.refresh_vault(source, second, keys[0])
    with pytest.raises(ValueError, match="rollback"):
        vault.refresh_vault(source, first, keys[0])
    (source / "Duplicate.md").write_text("Branch B")
    branch, _ = seal(source, tmp_path / "branch", keys, first)
    with pytest.raises(ValueError, match="divergent"):
        vault.refresh_vault(source, branch, keys[0])


@pytest.mark.parametrize("mode", ["source", "wrong-key", "signature"])
def test_tamper_or_wrong_key_creates_no_views(mode, source, tmp_path, keys):
    data, _ = seal(source, tmp_path / "plan", keys)
    key = keys[0]
    if mode == "wrong-key":
        key = bytes([key[0] ^ 1]) + key[1:]
    else:
        files = pack.unpack(data)
        head = pack.loads(files["pack.json"])["head"]
        name = f"shards/{head}/" + ("content/source.txt" if mode == "source" else "sig/manifest.sig")
        files[name] = bytes([files[name][0] ^ 1]) + files[name][1:]
        data = pack.pack_files(files)
    with pytest.raises(ValueError):
        vault.refresh_vault(source, data, key)
    assert not (source / vault.MANAGED).exists()


@pytest.mark.parametrize("kind", ["file", "directory", "ignore"])
def test_symlink_intake_is_refused(kind, source, tmp_path, keys):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("OUTSIDE_SENTINEL")
    if kind == "file":
        (source / "link.md").symlink_to(outside / "secret.md")
    elif kind == "directory":
        (source / "mount").symlink_to(outside, target_is_directory=True)
    else:
        (source / ".okignore").symlink_to(outside / "secret.md")
    with pytest.raises(ValueError, match="[Ss]ymlink"):
        make_plan(source, tmp_path / "plan", keys)
    assert not (tmp_path / "plan").exists()


def test_case_collisions_fail_before_stage(source, tmp_path, keys):
    (source / "duplicate.md").write_text("Different case")
    with pytest.raises(ValueError, match="colliding"):
        make_plan(source, tmp_path / "plan", keys)


def test_reserved_unowned_directory_is_preserved(source, tmp_path, keys):
    managed = source / vault.MANAGED
    managed.mkdir()
    (managed / "precious.md").write_text("User data")
    with pytest.raises((ValueError, OSError)):
        make_plan(source, tmp_path / "plan", keys)
    assert (managed / "precious.md").read_text() == "User data"


def test_upstream_ignore_conventions_are_retained_in_snapshot(source, tmp_path, keys):
    (source / ".okignore").write_text("Copy.md\n")
    data, _ = seal(source, tmp_path / "plan", keys)
    _, index, originals = vault.read_snapshot(data, keys[0])
    assert "Copy.md" not in originals
    assert index["selection"]["okignore"] == "Copy.md\n"
    assert {"path": "Copy.md", "reason": "upstream-selection"} in index["selection"]["excluded"]


def test_count_and_byte_limits(source, tmp_path, keys, monkeypatch):
    monkeypatch.setattr(vault, "MAX_NOTES", 1)
    with pytest.raises(ValueError, match="limit"):
        make_plan(source, tmp_path / "plan", keys)
    assert not (tmp_path / "plan").exists()


def test_stage_must_be_outside_source(source, keys):
    with pytest.raises(ValueError, match="outside"):
        make_plan(source, source / "stage", keys)


def test_wrong_signer_fails_before_publication(source, tmp_path, keys):
    other = hybrid1_keygen()
    planned = make_plan(source, tmp_path / "plan", keys)
    with pytest.raises(ValueError, match="verification"):
        vault.seal_vault(source, tmp_path / "plan", planned["approval_sha256"], other[1], keys[0])


def test_refresh_lock_preserves_existing_work(source, tmp_path, keys):
    data, _ = seal(source, tmp_path / "plan", keys)
    vault.refresh_vault(source, data, keys[0])
    lock = source / vault.MANAGED / ".refresh-lock"
    lock.mkdir()
    with pytest.raises(FileExistsError):
        vault.refresh_vault(source, data, keys[0])
    assert lock.exists()


def test_queries_and_intake_make_no_network_calls(source, tmp_path, keys, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected socket")
    monkeypatch.setattr(socket, "socket", forbidden)
    data, _ = seal(source, tmp_path / "plan", keys)
    assert bridge.search_pack(data, keys[0], "Oldbeacon")["hits"]
    vault.refresh_vault(source, data, keys[0])


def test_real_go_and_python_verify_the_same_vault_history(source, tmp_path, keys):
    assert Path(GO).is_file(), "Build the existing native verifier before qualification"
    first, _ = seal(source, tmp_path / "first", keys, go_binary=GO)
    (source / "Duplicate.md").write_text("A natively verified successor.")
    second, _ = seal(source, tmp_path / "second", keys, first, go_binary=GO)
    result, _, originals = vault.read_snapshot(second, keys[0], GO)
    assert len(result["checks"]) == 4 and all(c["status"] == "PASS" for c in result["checks"])
    assert originals["Duplicate.md"] == b"A natively verified successor."
    changed = pack.unpack(second)
    note = next(n for n in changed if n.startswith(f"shards/{result['head']}/content/notes/"))
    changed[note] += b"tamper"
    with pytest.raises(ValueError):
        vault.read_snapshot(pack.pack_files(changed), keys[0], GO)


def test_cli_full_cycle_and_stale_exit(source, tmp_path, keys, capsys):
    public = tmp_path / "trust.pub"
    secret = tmp_path / "signing.key"
    public.write_bytes(keys[0])
    secret.write_bytes(keys[1])
    stage = tmp_path / "plan"
    output = tmp_path / "vault.axmpack.zip"
    common = [str(source), "--trusted-key", str(public)]
    assert vault.main(["plan", *common, "--out", str(stage), "--vault-id", "test-vault",
                       "--publisher-id", "test", "--publisher-name", "Test publisher"]) == 0
    planned = json.loads(capsys.readouterr().out)
    args = ["seal", *common, "--plan", str(stage), "--approve", planned["approval_sha256"],
            "--private-key", str(secret), "--output", str(output)]
    assert vault.main(args) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "SEALED" and output.is_file()
    assert vault.main(["refresh", *common, "--pack", str(output)]) == 0
    assert json.loads(capsys.readouterr().out)["source_files_written"] == 0
    (source / "Duplicate.md").write_text("Unapproved update")
    assert vault.main(args) == 2
    assert "changed after review" in json.loads(capsys.readouterr().out)["error"]


def test_approved_bytes_are_retained_even_if_stage_changes_after_validation(source, tmp_path, keys, monkeypatch):
    stage = tmp_path / "plan"
    planned = make_plan(source, stage, keys)
    approved = vault._approved
    original_plan = (stage / "plan.json").read_bytes()
    original_review = (stage / "review.md").read_bytes()
    def change_after_validation(*args, **kwargs):
        result = approved(*args, **kwargs)
        (stage / "plan.json").write_bytes(b"CHANGED_AFTER_VALIDATION")
        (stage / "review.md").write_bytes(b"CHANGED_AFTER_VALIDATION")
        return result
    monkeypatch.setattr(vault, "_approved", change_after_validation)
    data, receipt = vault.seal_vault(source, stage, planned["approval_sha256"], keys[1], keys[0])
    files = pack.unpack(data)
    prefix = f"shards/{receipt['head']}/content/"
    assert files[prefix + "approved-plan.json"] == original_plan
    assert files[prefix + "review.md"] == original_review
    assert vault.read_snapshot(data, keys[0])[0]["status"] == "PASS"


def test_no_change_never_invokes_the_signing_compiler(source, tmp_path, keys, monkeypatch):
    first, _ = seal(source, tmp_path / "first", keys)
    planned = make_plan(source, tmp_path / "second", keys, first)
    def forbidden(*args, **kwargs):
        pytest.fail("No-change run invoked the signing compiler")
    monkeypatch.setattr("axm_build.compiler_generic.compile_generic_shard", forbidden)
    data, result = vault.seal_vault(source, tmp_path / "second", planned["approval_sha256"], keys[1], keys[0], previous=first)
    assert data == first and not result["new_edition"]


def test_fresh_output_publication_never_overwrites(source, tmp_path, keys):
    destination = tmp_path / "review"
    destination.mkdir()
    precious = destination / "personal.md"
    precious.write_text("PRESERVE_ME")
    with pytest.raises(FileExistsError):
        make_plan(source, destination, keys)
    assert precious.read_text() == "PRESERVE_ME" and len(list(destination.iterdir())) == 1
    output = tmp_path / "old.axmpack.zip"
    output.write_bytes(b"ORIGINAL_OUTPUT")
    with pytest.raises((ValueError, FileExistsError)):
        vault._new_file(output, b"NEW_OUTPUT")
    assert output.read_bytes() == b"ORIGINAL_OUTPUT"


def test_same_approved_plan_compiles_reproducibly(source, tmp_path, keys):
    planned = make_plan(source, tmp_path / "plan", keys)
    first, one = vault.seal_vault(source, tmp_path / "plan", planned["approval_sha256"], keys[1], keys[0])
    second, two = vault.seal_vault(source, tmp_path / "plan", planned["approval_sha256"], keys[1], keys[0])
    assert first == second and one["head"] == two["head"]


def test_private_key_is_not_opened_when_cli_approval_fails(source, tmp_path, keys, capsys):
    public = tmp_path / "trust.pub"
    public.write_bytes(keys[0])
    make_plan(source, tmp_path / "plan", keys)
    result = vault.main(["seal", str(source), "--trusted-key", str(public),
                         "--plan", str(tmp_path / "plan"), "--approve", "0" * 64,
                         "--private-key", str(tmp_path / "nonexistent.key"),
                         "--output", str(tmp_path / "not-created.zip")])
    assert result == 2
    assert "approval" in json.loads(capsys.readouterr().out)["error"]
    assert not (tmp_path / "not-created.zip").exists()


def test_supported_128_note_bound_and_129th_refusal(tmp_path, keys):
    source = tmp_path / "capacity-vault"
    source.mkdir()
    for number in range(128):
        (source / f"Note-{number:03}.md").write_text(f"# Capacity note {number}\n\nUnique capture {number}.\n")
    data, receipt = seal(source, tmp_path / "capacity-review", keys, go_binary=GO)
    verdict, index, originals = vault.read_snapshot(data, keys[0], GO)
    assert len(originals) == len(index["notes"]) == receipt["notes"] == 128
    assert len(verdict["records"][-1]["claims"]) == 129
    (source / "Note-128.md").write_text("This note crosses the explicit bound.")
    with pytest.raises(ValueError, match="limit"):
        make_plan(source, tmp_path / "over-capacity-review", keys)
    assert not (tmp_path / "over-capacity-review").exists()
