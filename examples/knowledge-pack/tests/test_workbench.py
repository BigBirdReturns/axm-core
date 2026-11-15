from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
import urllib.error
import urllib.request

import pytest
from axm_build.sign import hybrid1_keygen
import workbench_store as store
from workbench_store import Workbench, Cancelled
from axm_forge.vault_extraction import (
    Extractor,
    validate_proposals,
    deterministic_metadata,
    prose_blocks,
)
from vault_workbench import Application, make_server


@pytest.fixture(scope="module")
def keys():
    return hybrid1_keygen()


@pytest.fixture
def workspace(tmp_path, keys):
    keys_path = tmp_path / "keys"
    keys_path.mkdir()
    (keys_path / "publisher.pub").write_bytes(keys[0])
    (keys_path / "publisher.key").write_bytes(keys[1])
    source = tmp_path / "vault"
    source.mkdir()
    (source / "Policy.md").write_text(
        "---\ntitle: Retention policy\nowner: Research\n---\n# Policy\n\nOrion retains source evidence for 90 days.\nSee [[Evidence]].\n",
        encoding="utf-8",
    )
    (source / "Evidence.md").write_bytes(
        b"# Evidence\n\nOriginal artifacts remain immutable.\n"
    )
    w = Workbench(tmp_path / "state")
    w.register(
        "research",
        str(source),
        str(keys_path / "publisher.pub"),
        str(keys_path / "publisher.key"),
        publisher_id="test",
        publisher_name="Test publisher",
    )
    yield w, source
    w.close()


def commit(w, identifier="research", extract=False, select=True):
    plan = w.plan(identifier, extract=extract)
    accepted = (
        [c["id"] for f in plan["files"] for c in f["candidates"]] if select else []
    )
    result = w.approve(plan["id"], plan["digest"], accepted)
    return plan, result


@pytest.fixture
def provider():
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(body)
            prompt = body["messages"][0]["content"]
            claims = []
            if "90 days" in prompt:
                claims.append(
                    {
                        "subject": "Orion",
                        "predicate": "retains_source_evidence_days",
                        "object": "90",
                        "quote": "Orion retains source evidence for 90 days.",
                    }
                )
            if "30 days" in prompt:
                claims.append(
                    {
                        "subject": "Orion",
                        "predicate": "retains_source_evidence_days",
                        "object": "30",
                        "quote": "Orion retains source evidence for 30 days.",
                    }
                )
            claims.append(
                {
                    "subject": "Forged",
                    "predicate": "is",
                    "object": "true",
                    "quote": "This quotation was fabricated.",
                }
            )
            raw = json.dumps(
                {"choices": [{"message": {"content": json.dumps({"claims": claims})}}]}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield (
        {
            "type": "openai-compatible",
            "base_url": f"http://127.0.0.1:{server.server_port}/v1",
            "model": "deterministic-test-provider",
        },
        calls,
    )
    server.shutdown()
    server.server_close()


def test_incremental_documents_and_no_change_without_signer(workspace, monkeypatch):
    w, source = workspace
    first, initial = commit(w)
    assert initial["compiled_notes"] == 2 and initial["compiled_catalogs"] == 1
    before = {e["path"]: e["shard_id"] for e in w.head("research")["entries"]}
    original = (source / "Evidence.md").read_bytes()
    (source / "Policy.md").write_text(
        "# Policy\n\nOrion retains source evidence for 30 days.\n"
    )
    second, update = commit(w)
    after = {e["path"]: e["shard_id"] for e in w.head("research")["entries"]}
    assert update["compiled_notes"] == 1 and update["reused_notes"] == 1
    assert before["Evidence.md"] == after["Evidence.md"]
    assert before["Policy.md"] != after["Policy.md"]
    assert (source / "Evidence.md").read_bytes() == original

    def forbidden(*args, **kwargs):
        pytest.fail("Unchanged capture invoked the compiler")

    monkeypatch.setattr(w, "_compile", forbidden)
    plan, unchanged = commit(w)
    assert unchanged["status"] == "UNCHANGED" and unchanged["head"] == update["head"]
    assert unchanged["compiled_notes"] == unchanged["compiled_catalogs"] == 0
    assert len(w.history("research")["editions"]) == 2


def test_current_history_retrieval_uses_spectra(workspace):
    w, source = workspace
    _, first = commit(w)
    assert w.search("90 days")["hits"]
    (source / "Policy.md").write_text(
        "# Policy\n\nOrion retains source evidence for 30 days.\n"
    )
    assert w.search("30 days")["hits"] == []
    _, second = commit(w)
    assert w.search("90 days")["hits"] == []
    assert w.search("30 days")["hits"]
    assert w.search("90 days", "history")["hits"]
    assert first["head"] != second["head"]
    assert "Spectra" in w.search("Orion")["backend"]
    context = w.context("Orion", budget=2000)
    assert context["references"] and "30 days" in context["context"]
    assert "90 days" not in context["context"]


def test_real_provider_protocol_proposals_not_autoaccepted(workspace, provider):
    w, source = workspace
    config, calls = provider
    w.configure_provider(config)
    plan = w.plan("research", extract=True)
    assert len(calls) == plan["model_calls"] == 2
    semantics = [c for f in plan["files"] for c in f["candidates"] if c["tier"] == 2]
    assert len(semantics) == 1
    assert semantics[0]["object"] == "90"
    assert any(f["failures"] for f in plan["files"])
    assert w.head("research") is None
    assert w.search("retains_source_evidence_days")["hits"] == []
    w.approve(plan["id"], plan["digest"], [semantics[0]["id"]])
    hits = w.search("retains_source_evidence_days")["hits"]
    assert len(hits) == 1 and hits[0]["tier"] == 2 and hits[0]["object"] == "90"
    assert w.search("Forged")["hits"] == []
    assert "source evidence" in hits[0]["evidence"]


def test_provider_cache_and_force_reextract(workspace, provider):
    w, _ = workspace
    config, calls = provider
    w.configure_provider(config)
    first = w.plan("research", extract=True)
    second = w.plan("research", extract=True)
    assert first["model_calls"] == 2 and second["model_calls"] == 0
    assert second["cache_hits"] == 2 and len(calls) == 2
    third = w.plan("research", extract=True, force_extract=True)
    assert third["model_calls"] == 2 and len(calls) == 4


def test_selective_approval_preserves_whole_sources(workspace, provider):
    w, source = workspace
    w.configure_provider(provider[0])
    plan = w.plan("research", extract=True)
    w.approve(plan["id"], plan["digest"], [])
    assert w.search("retains_source_evidence_days")["hits"] == []
    assert w.search("90 days")["hits"]
    entry = next(e for e in w.head("research")["entries"] if e["path"] == "Policy.md")
    assert (
        w.state / "objects" / entry["shard_id"] / "content/original.md"
    ).read_bytes() == (source / "Policy.md").read_bytes()


def test_stale_review_and_wrong_digest_rejected(workspace):
    w, source = workspace
    plan = w.plan("research")
    with pytest.raises(ValueError, match="exact plan"):
        w.approve(plan["id"], "0" * 64, [])
    (source / "Evidence.md").write_text("Changed after review.")
    with pytest.raises(ValueError, match="changed after review"):
        w.approve(plan["id"], plan["digest"], [])
    assert w.head("research") is None


def test_competing_reviews_and_idempotency(workspace):
    w, source = workspace
    first = w.plan("research")
    second = w.plan("research")
    accepted = [c["id"] for f in first["files"] for c in f["candidates"]]
    result = w.approve(first["id"], first["digest"], accepted)
    replay = w.approve(first["id"], first["digest"], accepted)
    assert replay["head"] == result["head"] and replay["idempotent"]
    with pytest.raises(ValueError, match="different selection"):
        w.approve(first["id"], first["digest"], [])
    with pytest.raises(ValueError, match="head changed"):
        w.approve(second["id"], second["digest"], [])


def test_source_race_during_sealing_keeps_prior_head(workspace, monkeypatch):
    w, source = workspace
    _, first = commit(w)
    (source / "Policy.md").write_text("# Reviewed revision\n\nA real change.")
    plan = w.plan("research")
    compile_original = w._compile

    def race(*args, **kwargs):
        result = compile_original(*args, **kwargs)
        (source / "Evidence.md").write_text("Concurrent authoring must survive.")
        return result

    monkeypatch.setattr(w, "_compile", race)
    with pytest.raises(ValueError, match="changed during sealing"):
        w.approve(plan["id"], plan["digest"], [])
    assert w.head("research")["head"] == first["head"]


def test_rename_delete_and_empty_successor(workspace):
    w, source = workspace
    commit(w)
    old = next(e for e in w.head("research")["entries"] if e["path"] == "Evidence.md")
    (source / "Evidence.md").rename(source / "Renamed.md")
    plan, result = commit(w)
    renamed = next(f for f in plan["files"] if f["path"] == "Renamed.md")
    assert renamed["action"] == "renamed" and renamed["previous"] == old["shard_id"]
    new = next(e for e in w.head("research")["entries"] if e["path"] == "Renamed.md")
    manifest = json.loads(
        (w.state / "objects" / new["shard_id"] / "manifest.json").read_text()
    )
    assert manifest["supersedes"] == [old["shard_id"]]
    for p in source.glob("*.md"):
        p.unlink()
    _, empty = commit(w)
    assert empty["notes"] == empty["compiled_notes"] == 0
    assert w.search("Orion")["hits"] == []
    assert w.search("Orion", "history")["hits"]


def test_two_vaults_separate_anchors_and_competing_assertions(
    workspace, keys, provider
):
    w, source = workspace
    w.configure_provider(provider[0])
    commit(w, extract=True)
    other = source.parent / "other"
    other.mkdir()
    (other / "Runbook.md").write_text(
        "# Runbook\n\nOrion retains source evidence for 30 days.\n"
    )
    pub, private = hybrid1_keygen()
    keydir = source.parent / "other-keys"
    keydir.mkdir()
    (keydir / "pub").write_bytes(pub)
    (keydir / "private").write_bytes(private)
    w.register(
        "operations",
        str(other),
        str(keydir / "pub"),
        str(keydir / "private"),
        publisher_id="other",
        publisher_name="Other",
    )
    commit(w, "operations", extract=True)
    hits = w.search("retains_source_evidence_days")["hits"]
    assert {h["vault_id"] for h in hits} == {"research", "operations"}
    assert {h["object"] for h in hits} == {"30", "90"}
    groups = w.conflicts()["groups"]
    assert groups[0]["distinct_values"] == 2
    assert {
        h["vault_id"] for h in w.search("Orion", identifier="operations")["hits"]
    } == {"operations"}


def test_native_views_preserve_source_and_edits(workspace):
    w, source = workspace
    commit(w)
    before = {p.name: p.read_bytes() for p in source.glob("*.md")}
    first = w.refresh("research")
    assert first["source_files_written"] == 0
    assert {p.name: p.read_bytes() for p in source.glob("*.md")} == before
    owned = source / store.VIEWS
    page = owned / "editions" / first["head"] / "index.md"
    page.write_text("MY UNSIGNED VIEW EDIT")
    base = owned / "Editions.base"
    base.write_text("MY CUSTOM VIEW LAYOUT")
    again = w.refresh("research")
    assert "index.md" in again["preserved_view_edits"]
    assert page.read_text() == "MY UNSIGNED VIEW EDIT"
    assert base.read_text() == "MY CUSTOM VIEW LAYOUT"
    assert w.search("UNSIGNED")["hits"] == []
    assert w.plan("research")["changed_notes"] == 0
    graph = w.graph("research")
    assert len(graph["edges"]) == 1 and graph["edges"][0]["target"] == "Evidence.md"


def test_restart_rebuilds_derived_index(workspace):
    w, source = workspace
    _, result = commit(w)
    expected = w.search("Orion")["hits"]
    w.close()
    reopened = Workbench(w.state)
    try:
        assert reopened.head("research")["head"] == result["head"]
        assert reopened.search("Orion")["hits"] == expected
    finally:
        reopened.close()


@pytest.mark.parametrize("location", ["catalog", "note", "key", "review"])
def test_tamper_fails_closed(location, workspace):
    w, _ = workspace
    plan, result = commit(w)
    current = w.head("research")
    if location == "catalog":
        p = w.state / "objects" / current["head"] / "content/catalog.json"
        p.write_bytes(p.read_bytes() + b" ")
        with pytest.raises(ValueError):
            w.search("Orion")
    elif location == "note":
        w.search("Orion")
        entry = next(e for e in current["entries"] if e["path"] == "Policy.md")
        p = w.state / "objects" / entry["shard_id"] / "content/original.md"
        p.write_bytes(p.read_bytes() + b"TAMPER")
        with pytest.raises(ValueError):
            w.search("Orion")
    elif location == "key":
        p = Path(w.spec("research")["public_key"])
        raw = p.read_bytes()
        p.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
        with pytest.raises(ValueError):
            w.search("Orion")
    else:
        p = w.state / "plans" / plan["id"] / "plan.json"
        p.write_bytes(p.read_bytes() + b" ")
        with pytest.raises(ValueError, match="changed on disk"):
            w.read_plan(plan["id"])


def test_cancel_and_reject_do_not_sign(workspace):
    w, _ = workspace
    plan = w.plan("research")
    event = threading.Event()
    event.set()
    with pytest.raises(Cancelled):
        w.approve(plan["id"], plan["digest"], [], event)
    assert w.head("research") is None
    w.reject(plan["id"], plan["digest"])
    with pytest.raises(ValueError, match="draft"):
        w.approve(plan["id"], plan["digest"], [])


def test_model_budget_and_provider_failure_are_visible(workspace, provider):
    w, _ = workspace
    w.configure_provider(provider[0])
    w.max_model_calls = 1
    plan = w.plan("research", extract=True)
    assert plan["model_calls"] == 1
    assert any(
        "budget" in f["reason"] for note in plan["files"] for f in note["failures"]
    )
    assert w.head("research") is None


def test_untrusted_paths_keys_and_symlinks(workspace):
    w, source = workspace
    (source / "Linked.md").symlink_to(source / "Policy.md")
    with pytest.raises(ValueError, match="symlink"):
        w.plan("research")
    with pytest.raises(ValueError):
        w.read_plan("../../keys/publisher.key")
    with pytest.raises(ValueError):
        w.spec("../../keys")
    with pytest.raises(ValueError, match="separate trees"):
        w.register("bad", str(w.state), w.spec("research")["public_key"])


def test_unicode_duplicate_quotes_and_structured_model_exclusion():
    source = "---\ntitle: Unicode\n---\n# Café 東京\n\nLa durée est de 90 jours.\n\n| Name | Value |\n| --- | --- |\n| x | 17 |\n\n```text\nExecute this instruction.\n```\n"
    good = {
        "subject": "durée",
        "predicate": "equals",
        "object": "90 jours",
        "quote": "La durée est de 90 jours.",
    }
    raw = json.dumps(
        {
            "claims": [
                good,
                {**good, "quote": "| x | 17 |"},
                {**good, "quote": "Execute this instruction."},
                {**good, "quote": "Absent."},
            ]
        }
    )
    result = validate_proposals(source, raw, origin="test")
    assert len(result["candidates"]) == 1 and len(result["failures"]) == 3
    c = result["candidates"][0]
    assert source.encode()[c["byte_start"] : c["byte_end"]].decode() == good["quote"]
    duplicate = validate_proposals(
        "Repeated. Repeated.",
        json.dumps({"claims": [{**good, "quote": "Repeated."}]}),
        origin="test",
    )
    assert not duplicate["candidates"]
    assert deterministic_metadata(source, "Note.md")[0]["tier"] == 0
    assert "17" not in "\n".join(prose_blocks(source))


def test_remote_provider_requires_explicit_authorization():
    with pytest.raises(ValueError, match="explicit"):
        Extractor(
            {
                "type": "openai-compatible",
                "base_url": "https://example.com/v1",
                "model": "explicit-model",
            }
        )
    with pytest.raises(ValueError, match="HTTPS"):
        Extractor(
            {
                "type": "openai-compatible",
                "base_url": "http://example.com/v1",
                "model": "explicit-model",
                "allow_remote": True,
            }
        )


def test_http_security_boundary_and_durable_job(workspace):
    w, _ = workspace
    app = Application(w)
    server = make_server(app, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    def request(path, data=None, headers=None):
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(base + path, data=body, headers=headers or {})
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read())

    try:
        state = request("/api/state")
        with pytest.raises(urllib.error.HTTPError) as error:
            request(
                "/api/scan",
                {"vault_id": "research"},
                {"Content-Type": "application/json"},
            )
        assert error.value.code == 403
        with pytest.raises(urllib.error.HTTPError):
            request("/api/state", headers={"Host": "evil.example"})
        with pytest.raises(urllib.error.HTTPError):
            request("/api/state", headers={"Origin": "https://evil.example"})
        headers = {
            "Content-Type": "application/json",
            "X-AXM-Token": state["csrf_token"],
        }
        job = request("/api/scan", {"vault_id": "research", "extract": False}, headers)
        for _ in range(100):
            jobs = request("/api/state")["jobs"]
            if jobs[0]["state"] not in ("queued", "running"):
                break
            time.sleep(0.02)
        assert jobs[0]["id"] == job["job_id"] and jobs[0]["state"] == "completed"
        assert w.head("research") is None
        with pytest.raises(urllib.error.HTTPError):
            request("/api/unknown", {}, headers)
    finally:
        server.shutdown()
        server.server_close()
        app.close()


def test_source_review_exposes_exact_staged_bytes_and_no_other_files(workspace):
    w, source = workspace
    plan = w.plan("research")
    result = w.source_review(plan["id"], "Policy.md")
    assert result["proposed"].encode() == (source / "Policy.md").read_bytes()
    assert result["original"] == "" and "+Orion retains" in result["diff"]
    with pytest.raises(ValueError, match="changed note"):
        w.source_review(plan["id"], "../../keys/publisher.key")
    commit(w)
    (source / "Policy.md").write_text("Revised source.\n")
    successor = w.plan("research")
    review = w.source_review(successor["id"], "Policy.md")
    assert "90 days" in review["original"] and review["proposed"] == "Revised source.\n"


def test_closed_collection_export_external_verification(workspace, tmp_path):
    from workbench_collection import export_collection, verify_collection
    import zipfile

    w, source = workspace
    commit(w)
    (source / "Policy.md").write_text("Orion retains source evidence for 30 days.\n")
    commit(w)
    archive = tmp_path / "accepted.axmcollection.zip"
    result = export_collection(w, "research", archive)
    assert (
        result["status"] == "EXPORTED"
        and result["editions"] == 2
        and result["objects"] == 5
    )
    checked = verify_collection(archive, w.spec("research")["public_key"])
    assert checked["status"] == "PASS" and checked["head"] == w.head("research")["head"]
    with zipfile.ZipFile(archive) as z:
        assert not any(name.endswith(".key") for name in z.namelist())
        original_members = [
            n for n in z.namelist() if n.endswith("content/original.md")
        ]
        assert len(original_members) == 3
    with pytest.raises(FileExistsError):
        export_collection(w, "research", archive)


@pytest.mark.parametrize(
    "mutation", ["source", "missing", "traversal", "duplicate", "wrong-key"]
)
def test_portable_collection_failure_controls(mutation, workspace, tmp_path):
    from workbench_collection import export_collection, verify_collection
    import zipfile

    w, _ = workspace
    commit(w)
    archive = tmp_path / "original.zip"
    export_collection(w, "research", archive)
    with zipfile.ZipFile(archive) as z:
        files = {n: z.read(n) for n in z.namelist()}
    key = Path(w.spec("research")["public_key"])
    if mutation == "wrong-key":
        key = tmp_path / "wrong.pub"
        key.write_bytes(hybrid1_keygen()[0])
    elif mutation == "source":
        name = next(n for n in files if n.endswith("content/original.md"))
        files[name] += b"TAMPER"
    elif mutation == "missing":
        name = next(n for n in files if n.endswith("content/note.json"))
        del files[name]
    elif mutation == "traversal":
        files["../outside"] = b"escape"
    changed = tmp_path / "changed.zip"
    with zipfile.ZipFile(changed, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for n, b in files.items():
            z.writestr(n, b)
        if mutation == "duplicate":
            z.writestr("collection.json", files["collection.json"])
    with pytest.raises((ValueError, OSError, KeyError)):
        verify_collection(changed, key)
    assert not (tmp_path / "outside").exists()


def test_query_worker_counts_are_explicitly_bounded(workspace):
    w, _ = workspace
    commit(w)
    w.search("Orion")
    assert w.index.engine.con.execute(
        "SELECT current_setting('threads'),current_setting('async_threads')"
    ).fetchone() == (2, 2)


def test_configuration_identity_and_provider_boundaries(workspace):
    w, source = workspace
    app = Application(w)
    try:
        with pytest.raises(ValueError, match="confirmation"):
            app.configure("identity", {"name": "test"})
        result = app.configure(
            "identity",
            {"name": "browser-identity", "confirm": "create-local-signing-identity"},
        )
        assert Path(result["public_key"]).stat().st_size == 1344
        assert Path(result["private_key"]).stat().st_size == 3904
        assert source not in Path(result["private_key"]).parents
        with pytest.raises(ValueError, match="CLI only"):
            app.configure(
                "provider", {"provider": {"type": "command", "argv": ["/bin/echo"]}}
            )
    finally:
        app.close()


def test_durable_running_jobs_become_interrupted_not_replayed(workspace):
    w, _ = workspace
    with w.db() as con:
        con.execute(
            "INSERT INTO jobs VALUES(?,?,?,?,?,?)",
            ("j_old", "running", "scan:research", "", "", "2026-11-15T00:00:00Z"),
        )
    app = Application(w)
    try:
        jobs = app.state()["jobs"]
        assert jobs[0]["state"] == "interrupted" and w.head("research") is None
    finally:
        app.close()


def test_watcher_retains_rescan_while_work_is_running(workspace):
    w, source = workspace
    app = Application(w)
    release = threading.Event()
    try:
        app.watch("research", True)
        time.sleep(0.4)
        app.submit(
            "test-held-work",
            lambda cancel: (
                (release.wait(5) and {"status": "TEST_FINISHED"})
                or {"status": "TEST_FINISHED"}
            ),
        )
        (source / "Policy.md").write_text("Changed while a different job is active.\n")
        deadline = time.time() + 5
        while time.time() < deadline:
            if "research" in app.pending_scans:
                break
            time.sleep(0.05)
        assert "research" in app.pending_scans
        release.set()
        deadline = time.time() + 8
        while time.time() < deadline:
            with w.db() as con:
                count = con.execute("SELECT count(*) FROM plans").fetchone()[0]
            if count:
                break
            time.sleep(0.05)
        assert count >= 1 and w.head("research") is None
        with w.db() as con:
            summary = json.loads(
                con.execute(
                    "SELECT summary FROM plans ORDER BY rowid DESC LIMIT 1"
                ).fetchone()[0]
            )
        assert summary["model_calls"] == 0
        app.watch("research", False)
    finally:
        release.set()
        app.close()


def test_private_state_and_unowned_directory_refusal(tmp_path):
    import os
    state = tmp_path / 'private-state'
    w = Workbench(state)
    try:
        if os.name != 'nt':
            assert state.stat().st_mode & 0o777 == 0o700
    finally:
        w.close()
    other = tmp_path / 'existing-user-data'
    other.mkdir()
    (other / 'personal.txt').write_text('PRESERVE')
    with pytest.raises(ValueError, match='owned workbench'):
        Workbench(other)
    assert [p.name for p in other.iterdir()] == ['personal.txt']
