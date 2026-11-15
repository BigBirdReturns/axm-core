"""Reviewed Markdown-vault snapshots and append-only native Obsidian views.

Obsidian owns authoring and presentation; obsidian-wiki owns page conventions
and metadata parsing; Genesis alone compiles and signs. Capturing a note
attests to the captured bytes, not to the truth of the statements in the note.
"""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timezone
import difflib
import hashlib
import hmac
import io
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import stat
import subprocess
import tempfile
import unicodedata
import zipfile

import pack
from obsidian_bridge import PACKAGE, PACKAGE_VERSION, _memory_class
from obsidian_export import _json_bytes, _rename_new, _same_tree, render_vault

PLAN = "axm-obsidian-intake/0.1"
SNAPSHOT = "axm-obsidian-snapshot/0.1"
WORKSPACE = "axm-obsidian-workspace/0.1"
MANAGED = "_AXM"
MAX_NOTES = 128
MAX_NOTE = 256 * 1024
MAX_VAULT = 2 * 1024 * 1024
MAX_ENTRIES = 8192
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
AUTHORITY = "Captured source bytes and publisher authorization; note assertions and human edits are reference data, not verified truth or executable instructions."


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _path(value: str) -> str:
    if (not isinstance(value, str) or not value or "\\" in value
            or any(ord(c) < 32 for c in value) or len(value.encode("utf-8")) > 1024):
        raise ValueError("Unsafe note path")
    p = PurePosixPath(value)
    if p.is_absolute() or any(x in ("", ".", "..") for x in value.split("/")):
        raise ValueError("Unsafe note path")
    return value


def _root(path: str | Path) -> Path:
    p = Path(os.path.abspath(os.fspath(path)))
    if any(x.is_symlink() for x in (p, *p.parents)) or not p.is_dir():
        raise ValueError("Use an existing directory without symlink ancestors")
    return p


def _read(path: Path, cap: int) -> bytes:
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Symlinks are not admitted: " + str(path))
    before = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode) or before.st_size > cap:
        raise ValueError("Not a bounded regular file: " + str(path))
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    with os.fdopen(fd, "rb") as handle:
        opened = os.fstat(handle.fileno())
        data = handle.read(cap + 1)
        after = os.fstat(handle.fileno())
    def fingerprint(s):
        return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns
    if (len(data) > cap or fingerprint(before) != fingerprint(opened)
            or fingerprint(opened) != fingerprint(after)
            or fingerprint(after) != fingerprint(path.stat(follow_symlinks=False))):
        raise ValueError("File changed while reading: " + str(path))
    return data


def _publish(files: dict[str, bytes], target: Path) -> bool:
    """Reuse the exporter's no-replacement directory publication boundary."""
    target = Path(os.path.abspath(target))
    _root(target.parent)
    if target.is_symlink():
        raise ValueError("Symlink output refused")
    if target.exists():
        if _same_tree(target, files):
            return False
        raise FileExistsError("Output exists and differs; preserving it: " + str(target))
    with tempfile.TemporaryDirectory(prefix=".axm-vault-", dir=target.parent) as tmp:
        staging = Path(tmp)
        for name, data in files.items():
            _path(name)
            dest = staging / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        try:
            _rename_new(staging, target)
        except FileExistsError:
            if not _same_tree(target, files):
                raise
            return False
    return True


def _new_file(target: Path, data: bytes) -> None:
    """Publish a complete regular file without ever replacing an existing file."""
    _root(target.parent)
    if target.exists() or target.is_symlink():
        if _read(target, max(len(data), 1)) == data:
            return
        raise FileExistsError("Preserving existing output: " + str(target))
    fd, name = tempfile.mkstemp(prefix=".axm-file-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(name, target)
        except FileExistsError:
            if _read(target, max(len(data), 1)) != data:
                raise
    finally:
        Path(name).unlink(missing_ok=True)


def scan_vault(vault: str | Path) -> tuple[list[dict], dict[str, bytes], dict]:
    """Read selected Markdown with upstream conventions, never upstream traversal.

    The pinned upstream walker follows directory symlinks. This custody boundary
    instead refuses links/special files and bounds every source before parsing.
    """
    _memory_class()  # Enforce the already-qualified dependency version.
    from obsidian_wiki.memory import parse_frontmatter
    from obsidian_wiki.vault import is_instruction_file, okignore_patterns, okignored, skipped_dir, split_frontmatter
    root = _root(vault)
    ignore = _read(root / ".okignore", 65536) if (root / ".okignore").exists() or (root / ".okignore").is_symlink() else b""
    # Give upstream only the explicitly captured selection file.
    with tempfile.TemporaryDirectory(prefix="axm-obsidian-selection-") as tmp:
        p = Path(tmp)
        (p / ".okignore").write_bytes(ignore)
        patterns = okignore_patterns(p)
    notes, blobs, excluded, seen = [], {}, [], set()
    pending = [root]
    total = entries = 0
    while pending:
        directory = pending.pop()
        for child in sorted(directory.iterdir(), key=lambda x: x.name):
            entries += 1
            if entries > MAX_ENTRIES:
                raise ValueError("Vault entry limit exceeded")
            rel = child.relative_to(root)
            name = _path(rel.as_posix())
            mode = child.lstat().st_mode
            if child.is_symlink():
                raise ValueError("Vault symlink refused: " + name)
            if rel.parts[0] == MANAGED:
                if len(rel.parts) != 1 or not stat.S_ISDIR(mode):
                    raise ValueError("Reserved view directory is not owned by this adapter")
                marker = pack.loads(_read(child / "owner.json", 4096))
                if marker.get("schema") != WORKSPACE:
                    raise ValueError("Reserved view directory is not owned by this adapter")
                continue
            if skipped_dir(rel) or okignored(rel, patterns):
                excluded.append({"path": name, "reason": "upstream-selection"})
                continue
            if stat.S_ISDIR(mode):
                pending.append(child)
                continue
            if not stat.S_ISREG(mode):
                raise ValueError("Vault special file refused: " + name)
            if child.suffix.lower() != ".md" or is_instruction_file(rel):
                excluded.append({"path": name, "reason": "not-a-knowledge-page"})
                continue
            portable = unicodedata.normalize("NFC", name).casefold()
            if portable in seen:
                raise ValueError("Case or Unicode-colliding note path: " + name)
            seen.add(portable)
            raw = _read(child, MAX_NOTE)
            total += len(raw)
            if total > MAX_VAULT or len(notes) >= MAX_NOTES:
                raise ValueError("Vault snapshot limit exceeded (128 notes / 2 MiB)")
            text = raw.decode("utf-8", errors="strict")
            header, _ = split_frontmatter(text.removeprefix("\ufeff"))
            title = str(parse_frontmatter(header).get("title") or child.stem)
            blob = "notes/" + _sha(name.encode("utf-8")) + ".md"
            notes.append({"path": name, "blob": blob, "sha256": _sha(raw), "bytes": len(raw), "title": title})
            blobs[blob] = raw
    notes.sort(key=lambda x: x["path"])
    selection = {"backend": {"package": PACKAGE, "version": PACKAGE_VERSION},
                 "kind": "Markdown knowledge pages", "okignore": ignore.decode("utf-8"),
                 "excluded": sorted(excluded, key=lambda x: x["path"]),
                 "managed_directory_excluded": MANAGED}
    return notes, blobs, selection


def _inventory(notes: list[dict]) -> dict[str, str]:
    return {n["path"]: n["sha256"] for n in notes}


def _changes(old: list[dict], new: list[dict]) -> list[dict]:
    before, after = _inventory(old), _inventory(new)
    changes = []
    removed, added = set(before) - set(after), set(after) - set(before)
    # Infer a rename only for an unambiguous byte-identical one-to-one pair.
    for src in sorted(tuple(removed)):
        candidates = [dst for dst in added if after[dst] == before[src]]
        if len(candidates) == 1 and sum(before[p] == before[src] for p in removed) == 1:
            dst = candidates[0]
            changes.append({"kind": "renamed", "from": src, "path": dst, "sha256": after[dst]})
            removed.remove(src)
            added.remove(dst)
    changes += [{"kind": "deleted", "path": p, "before": before[p]} for p in sorted(removed)]
    changes += [{"kind": "added", "path": p, "after": after[p]} for p in sorted(added)]
    changes += [{"kind": "modified", "path": p, "before": before[p], "after": after[p]}
                for p in sorted(before.keys() & after.keys()) if before[p] != after[p]]
    return sorted(changes, key=lambda x: (x["path"], x["kind"]))


def read_snapshot(data: bytes, key: bytes, go_binary: str | None = None) -> tuple[dict, dict, dict[str, bytes]]:
    verdict = pack.inspect_pack(data, key, go_binary)
    if verdict["status"] != "PASS":
        raise ValueError("Snapshot failed independent pack verification")
    files = pack.unpack(data)
    prefix = f"shards/{verdict['head']}/content/"
    index = pack.loads(files.get(prefix + "vault-index.json", b"{}"))
    if index.get("schema") != SNAPSHOT or not ID.fullmatch(index.get("vault_id", "")):
        raise ValueError("Pack head is not an Obsidian vault snapshot")
    approved_raw = files.get(prefix + "approved-plan.json", b"")
    if _sha(approved_raw) != index.get("approved_plan_sha256"):
        raise ValueError("Snapshot approval digest does not match its retained plan")
    approved = pack.loads(approved_raw)
    if approved.get("schema") != PLAN or any(approved.get(f) != index.get(f) for f in ("vault_id", "publisher", "notes", "selection", "changes")):
        raise ValueError("Snapshot inventory differs from the approved plan")
    if _sha(files.get(prefix + "review.md", b"")) != approved.get("review_sha256"):
        raise ValueError("Snapshot review does not match its approved presentation")
    record = next(r for r in verdict["records"] if r["id"] == verdict["head"])
    if (record["manifest"]["publisher"] != index["publisher"]
            or record["manifest"]["metadata"]["namespace"] != "axm/obsidian/" + index["vault_id"]
            or record["supersedes"] != ([approved["previous_head"]] if approved["previous_head"] else [])):
        raise ValueError("Snapshot publisher, namespace or lineage differs from its approved plan")
    notes, originals, seen = index.get("notes", []), {}, set()
    if not isinstance(notes, list) or len(notes) > MAX_NOTES:
        raise ValueError("Invalid snapshot inventory")
    for item in notes:
        name = _path(item["path"])
        blob = "notes/" + _sha(name.encode("utf-8")) + ".md"
        if name in seen or item["blob"] != blob:
            raise ValueError("Ambiguous snapshot inventory")
        seen.add(name)
        raw = files[prefix + blob]
        if len(raw) != item["bytes"] or _sha(raw) != item["sha256"]:
            raise ValueError("Snapshot original does not match its inventory")
        originals[name] = raw
    if sum(map(len, originals.values())) > MAX_VAULT:
        raise ValueError("Snapshot exceeds the custody limit")
    expected_files = {prefix + n["blob"] for n in notes}
    if expected_files != {name for name in files if name.startswith(prefix + "notes/")}:
        raise ValueError("Snapshot note inventory is incomplete")
    from axm_build.common import normalize_source_text
    expected_claims = {(n["path"], "records_note", normalize_source_text(originals[n["path"]].decode("utf-8")).strip())
                       for n in notes if normalize_source_text(originals[n["path"]].decode("utf-8")).strip()}
    inventory = json.dumps({"vault_id": index["vault_id"], "notes": notes}, ensure_ascii=True, sort_keys=True)
    expected_claims.add((index["vault_id"], "records_inventory", inventory))
    actual_claims = {(c["subject_label"], c["predicate"], c["object_label"]) for c in record["claims"]}
    if actual_claims != expected_claims or len(record["claims"]) != len(expected_claims):
        raise ValueError("Snapshot note captures differ from retained original bytes")
    return verdict, index, originals


def plan_vault(vault: str | Path, destination: str | Path, key: bytes, *, vault_id: str,
               publisher_id: str, publisher_name: str, previous: bytes | None = None,
               go_binary: str | None = None, created_at: str | None = None) -> dict:
    root = _root(vault)
    target = Path(os.path.abspath(destination))
    if target == root or root in target.parents:
        raise ValueError("Keep review plans outside the source vault")
    if len(key) != 1344 or not ID.fullmatch(vault_id):
        raise ValueError("Supply an external hybrid public key and a stable vault identifier")
    if any(not isinstance(x, str) or not x.strip() or len(x) > 180 for x in (publisher_id, publisher_name)):
        raise ValueError("Specify publisher id and name (1 to 180 characters)")
    notes, blobs, selection = scan_vault(root)
    baseline, old_notes, old_sources = None, [], {}
    if previous is not None:
        baseline, index, old_sources = read_snapshot(previous, key, go_binary)
        if index["vault_id"] != vault_id or index["publisher"] != {"id": publisher_id, "name": publisher_name}:
            raise ValueError("Successor must retain its vault and publisher identity")
        old_notes = index["notes"]
    changes = _changes(old_notes, notes)
    plan = {"schema": PLAN, "vault_id": vault_id,
            "publisher": {"id": publisher_id, "name": publisher_name},
            "created_at": created_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "trusted_key_sha256": _sha(key), "previous_sha256": _sha(previous) if previous is not None else None,
            "previous_head": baseline["head"] if baseline else None,
            "notes": notes, "selection": selection, "changes": changes, "authority": AUTHORITY}
    by_name = {n["path"]: blobs[n["blob"]] for n in notes}
    review = ["# Obsidian snapshot review", "", AUTHORITY, "", json.dumps(changes, ensure_ascii=False, indent=2), ""]
    for change in changes:
        name, old_name = change["path"], change.get("from", change["path"])
        diff = "".join(difflib.unified_diff(old_sources.get(old_name, b"").decode("utf-8").splitlines(True),
                                          by_name.get(name, b"").decode("utf-8").splitlines(True),
                                          fromfile=old_name, tofile=name))
        review += ["## " + json.dumps(name, ensure_ascii=False), "", diff[:32768],
                   "[Diff truncated; inspect the complete staged note.]" if len(diff) > 32768 else ""]
    review_raw = "\n".join(review).encode("utf-8")
    plan["review_sha256"] = _sha(review_raw)
    raw = _json_bytes(plan)
    _publish({**blobs, "plan.json": raw, "review.md": review_raw}, target)
    return {"status": "REVIEW_REQUIRED", "plan": str(target), "approval_sha256": _sha(raw),
            "notes": len(notes), "changes": changes, "previous_head": plan["previous_head"],
            "excluded": selection["excluded"], "authority": AUTHORITY}


def _approved(vault: str | Path, directory: str | Path, approval: str, key: bytes,
              previous: bytes | None, go_binary: str | None) -> tuple[dict, dict[str, bytes], bytes, bytes]:
    stage = _root(directory)
    raw = _read(stage / "plan.json", 2 * MAX_VAULT)
    if not isinstance(approval, str) or not hmac.compare_digest(_sha(raw), approval):
        raise ValueError("Explicit approval must match the exact review plan digest")
    plan = pack.loads(raw)
    if plan.get("schema") != PLAN or plan["trusted_key_sha256"] != _sha(key):
        raise ValueError("Plan schema or trust anchor changed")
    if plan["previous_sha256"] != (_sha(previous) if previous is not None else None):
        raise ValueError("Previous pack changed after review")
    if previous is not None:
        verdict, index, _ = read_snapshot(previous, key, go_binary)
        if verdict["head"] != plan["previous_head"] or index["vault_id"] != plan["vault_id"]:
            raise ValueError("Reviewed lineage no longer matches")
    review_raw = _read(stage / "review.md", 2 * MAX_VAULT + MAX_NOTE)
    if _sha(review_raw) != plan["review_sha256"]:
        raise ValueError("Review presentation changed after capture")
    live, live_blobs, selection = scan_vault(vault)
    if live != plan["notes"] or selection != plan["selection"]:
        raise ValueError("Vault changed after review; capture and approve a fresh plan")
    for blob, value in live_blobs.items():
        if _read(stage / blob, MAX_NOTE) != value:
            raise ValueError("Staged source changed after capture")
    return plan, live_blobs, raw, review_raw


def seal_vault(vault: str | Path, directory: str | Path, approval: str, secret_key: bytes,
               key: bytes, *, previous: bytes | None = None, go_binary: str | None = None) -> tuple[bytes, dict]:
    plan, blobs, approved_raw, review_raw = _approved(vault, directory, approval, key, previous, go_binary)
    if previous is not None and not plan["changes"]:
        verdict, old, _ = read_snapshot(previous, key, go_binary)
        if old["selection"] == plan["selection"]:
            return previous, {"status": "UNCHANGED", "head": verdict["head"], "archive_sha256": _sha(previous), "new_edition": False}
    from axm_build.common import normalize_source_text
    from axm_build.compiler_generic import CompilerConfig, compile_generic_shard
    from axm_verify.crypto import derive_shard_id
    snapshot = {"schema": SNAPSHOT, "vault_id": plan["vault_id"], "publisher": plan["publisher"],
                "notes": plan["notes"], "selection": plan["selection"], "changes": plan["changes"],
                "approved_plan_sha256": approval, "authority": AUTHORITY,
                "evidence_coordinates": "Claims address normalized aggregate content/source.txt; original per-note bytes are separately retained and hashed in content/notes/*.md."}
    inventory = json.dumps({"vault_id": plan["vault_id"], "notes": plan["notes"]}, ensure_ascii=True, sort_keys=True)
    chunks = [inventory]
    candidates = [{"subject": plan["vault_id"], "predicate": "records_inventory", "object": inventory,
                   "object_type": "literal:string", "tier": 1, "evidence": inventory}]
    for note in plan["notes"]:
        text = normalize_source_text(blobs[note["blob"]].decode("utf-8")).strip()
        token = Path(note["blob"]).stem
        block = f"AXM_NOTE {token} {json.dumps(note['path'], ensure_ascii=True)}\nSHA256 {note['sha256']}\n{text}\nEND_AXM_NOTE {token}"
        chunks.append(block)
        if text:
            candidates.append({"subject": note["path"], "predicate": "records_note", "object": text,
                               "object_type": "literal:string", "tier": 1, "evidence": block})
    files = pack.unpack(previous) if previous is not None else {}
    with tempfile.TemporaryDirectory(prefix="axm-vault-compile-") as tmp:
        root = Path(tmp)
        source = root / "aggregate.txt"
        source.write_text("\n\n".join(chunks) + "\n", encoding="utf-8", newline="\n")
        c = root / "candidates.jsonl"
        c.write_bytes(b"".join(json.dumps(x, ensure_ascii=False).encode("utf-8") + b"\n" for x in candidates))
        extras = {**blobs, "vault-index.json": _json_bytes(snapshot),
                  "approved-plan.json": approved_raw, "review.md": review_raw}
        extra_paths = []
        for name, value in extras.items():
            p = root / "retained" / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(value)
            extra_paths.append((name, p))
        out = root / "sealed"
        cfg = CompilerConfig(source_path=source, candidates_path=c, out_dir=out,
                             private_key=secret_key, publisher_id=plan["publisher"]["id"],
                             publisher_name=plan["publisher"]["name"], namespace="axm/obsidian/" + plan["vault_id"],
                             created_at=plan["created_at"], title="Obsidian vault: " + plan["vault_id"],
                             supersedes=(plan["previous_head"],) if plan["previous_head"] else (),
                             lineage_action="amend", lineage_note="Explicitly approved source snapshot; predecessor bytes retained.",
                             extra_content=tuple(extra_paths))
        with contextlib.redirect_stdout(io.StringIO()):
            if not compile_generic_shard(cfg):
                raise ValueError("Genesis rejected the vault snapshot")
        sid = derive_shard_id((out / "manifest.json").read_bytes())
        if len(pack.rows((out / "graph/claims.jsonl").read_bytes())) != len(candidates):
            raise ValueError("Compiler did not retain every expected note capture")
        for path in out.rglob("*"):
            if path.is_file():
                files[f"shards/{sid}/{path.relative_to(out).as_posix()}"] = path.read_bytes()
    ids = sorted({n.split("/")[1] for n in files if n.startswith("shards/")})
    files["pack.json"] = _json_bytes({"format": pack.FORMAT, "head": sid, "shards": ids})
    result = pack.pack_files(files)
    verdict, _, _ = read_snapshot(result, key, go_binary)
    return result, {"status": "SEALED", "head": verdict["head"], "archive_sha256": _sha(result),
                    "new_edition": True, "notes": len(plan["notes"]), "claims": len(candidates),
                    "editions": len(ids), "approval_sha256": approval, "changes": plan["changes"]}


def _projection(data: bytes, key: bytes, prefix: str, go_binary: str | None) -> tuple[dict[str, bytes], dict]:
    files, metadata = render_vault(data, key, go_binary)
    canvas = pack.loads(files["Knowledge.canvas"])
    for node in canvas["nodes"]:
        node["file"] = prefix + "/" + node["file"]
    files["Knowledge.canvas"] = _json_bytes(canvas)
    base = pack.loads(files["Evidence.base"])
    for view, role in zip(base["views"], ("current", "history")):
        view["filters"] = {"and": [f'file.inFolder("{prefix}/{role}")']}
    files["Evidence.base"] = _json_bytes(base)
    # Rebase only links resolving to our generated files, not arbitrary note text.
    links = re.compile(r"\[\[([^\]\n|]+)\|([^\]\n]*)\]\]")
    for name, value in list(files.items()):
        if not name.endswith(".md") or name.startswith("_axm/"):
            continue
        def rebase(match):
            target = posixpath.normpath(posixpath.join(posixpath.dirname(name), match[1]))
            target = target if target in files else target + ".md"
            return f"[[{prefix}/{target.removesuffix('.md')}|{match[2]}]]" if target in files else match[0]
        files[name] = links.sub(rebase, value.decode("utf-8")).encode("utf-8")
    return files, metadata


def _dirty(target: Path, expected: dict[str, bytes]) -> list[str]:
    dirty = []
    for name, value in expected.items():
        try:
            if _read(target / name, max(len(value) + MAX_NOTE, MAX_NOTE)) != value:
                dirty.append(name)
        except (OSError, ValueError):
            dirty.append(name)
    return dirty


def vault_status(vault: str | Path, data: bytes, key: bytes, go_binary: str | None = None) -> dict:
    verdict, index, _ = read_snapshot(data, key, go_binary)
    notes, _, _ = scan_vault(vault)
    changes = _changes(index["notes"], notes)
    return {"status": "WORKING_COPY_CHANGED" if changes else "MATCHES_CAPTURED_NOTES", "head": verdict["head"],
            "changes": changes, "authority": AUTHORITY}


def refresh_vault(vault: str | Path, data: bytes, key: bytes, go_binary: str | None = None) -> dict:
    root = _root(vault)
    verdict, index, _ = read_snapshot(data, key, go_binary)
    state = vault_status(root, data, key, go_binary)
    owner = {"schema": WORKSPACE, "vault_id": index["vault_id"], "trusted_key_sha256": _sha(key)}
    managed = root / MANAGED
    if not managed.exists():
        _publish({"owner.json": _json_bytes(owner)}, managed)
    if pack.loads(_read(managed / "owner.json", 4096)) != owner:
        raise ValueError("Existing evidence workspace belongs to another vault or key")
    # A small filesystem ownership lock refuses concurrent publishers. It never
    # auto-breaks a stale lock: interrupted publication can be inspected safely.
    lock = managed / ".refresh-lock"
    lock.mkdir()
    try:
        current_path = managed / "current.json"
        current_raw = _read(current_path, 65536) if current_path.exists() or current_path.is_symlink() else None
        current = pack.loads(current_raw) if current_raw else None
        dirty = []
        if current:
            old_head = current["head"]
            if not pack.SHARD.fullmatch(old_head) or not re.fullmatch(r"[0-9a-f]{64}", current["archive_sha256"]):
                raise ValueError("Malformed current-view receipt")
            old_data = _read(managed / "packs" / (current["archive_sha256"] + ".zip"), pack.MAX_ARCHIVE)
            if _sha(old_data) != current["archive_sha256"]:
                raise ValueError("Stored pack differs from the current-view archive digest")
            old_verdict, old_index, _ = read_snapshot(old_data, key, go_binary)
            if old_verdict["head"] != old_head or old_index["vault_id"] != index["vault_id"]:
                raise ValueError("Current-view receipt differs from its signed pack")
            if old_head not in {r["id"] for r in verdict["records"]}:
                raise ValueError("Refusing rollback or a divergent view; supplied pack must include the published head")
            old_prefix = MANAGED + "/editions/" + old_head
            expected, _ = _projection(old_data, key, old_prefix, go_binary)
            dirty = _dirty(root / old_prefix, expected)
        for sub in ("editions", "packs", "updates"):
            p = managed / sub
            if p.is_symlink():
                raise ValueError("View subdirectory symlink refused")
            p.mkdir(exist_ok=True)
            _root(p)
        head = verdict["head"]
        prefix = MANAGED + "/editions/" + head
        generated, _ = _projection(data, key, prefix, go_binary)
        target = root / prefix
        if target.is_symlink():
            raise ValueError("Projection directory symlink refused")
        if target.exists():
            modified = _dirty(target, generated)
        else:
            _publish(generated, target)
            modified = []
        _new_file(managed / "packs" / (_sha(data) + ".zip"), data)
        update_name = head + ".md"
        update = ("---\n" + f"title: Evidence edition {len(verdict['records'])}\n"
                  + f"generation: {len(verdict['records'])}\n" + f"axm_head: {head}\n---\n\n"
                  + "# Accepted source snapshot\n\n" + AUTHORITY + "\n\n"
                  + f"[[{prefix}/index|Evidence and exact sources]]\n\n"
                  + f"[[{prefix}/Knowledge.canvas|Knowledge map]]\n\n"
                  + f"[[{prefix}/Evidence.base|Claims table]]\n\n"
                  + "Authoring notes remain in their original locations. Use the status command to compare them with this captured edition.\n").encode("utf-8")
        # User-facing navigation is append-only. Edited layouts/notes survive.
        if not (managed / "updates" / update_name).exists():
            _new_file(managed / "updates" / update_name, update)
        view = {"filters": {"and": [f'file.inFolder("{MANAGED}/updates")']},
                "views": [{"type": "table", "name": "Evidence editions", "order": ["file.name", "note.title", "note.generation", "note.axm_head"],
                           "sort": [{"property": "note.generation", "direction": "DESC"}]}]}
        if not (managed / "Evidence editions.base").exists():
            _new_file(managed / "Evidence editions.base", _json_bytes(view))
        if not (managed / "Notes.md").exists():
            _new_file(managed / "Notes.md", b"# Personal annotations\n\nThis file belongs to you. Refresh preserves it. These annotations are not signed or ingested; put source revisions in your ordinary vault notes and approve a fresh capture.\n")
        receipt = {"schema": WORKSPACE, "head": head, "archive_sha256": _sha(data), "projection": prefix,
                   "authority": "Local navigation only; always verify the referenced pack against an external key."}
        if current_raw is not None and _read(current_path, 65536) != current_raw:
            raise ValueError("Current view changed during refresh; immutable outputs retained")
        fd, tmp = tempfile.mkstemp(prefix=".current-", dir=managed)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(_json_bytes(receipt))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, current_path)
        finally:
            Path(tmp).unlink(missing_ok=True)
        return {"status": "VIEW_REFRESHED", "head": head, "projection": str(target),
                "open_in_obsidian": str(managed / "Evidence editions.base"), "working_copy": state,
                "preserved_previous_view_edits": dirty, "current_view_edits": modified,
                "source_files_written": 0, "human_annotations_written": 0}
    finally:
        lock.rmdir()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="Capture notes and review a prospective signed edition")
    seal = commands.add_parser("seal", help="Seal the exact approved snapshot through Genesis")
    refresh = commands.add_parser("refresh", help="Add evidence views to the existing vault without overwriting edits")
    status = commands.add_parser("status", help="Compare mutable source notes with an independently verified capture")
    for p in (plan, seal, refresh, status):
        p.add_argument("vault", type=Path)
        p.add_argument("--trusted-key", type=Path, required=True)
        p.add_argument("--go-verifier")
    for p in (plan, seal):
        p.add_argument("--previous", type=Path)
    plan.add_argument("--out", type=Path, required=True)
    plan.add_argument("--vault-id", required=True)
    plan.add_argument("--publisher-id", required=True)
    plan.add_argument("--publisher-name", required=True)
    seal.add_argument("--plan", type=Path, required=True)
    seal.add_argument("--approve", required=True, help="Exact SHA-256 printed by the plan command")
    seal.add_argument("--private-key", type=Path, required=True)
    seal.add_argument("--output", type=Path, required=True)
    for p in (refresh, status):
        p.add_argument("--pack", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        key = _read(args.trusted_key, 1344)
        previous_path = getattr(args, "previous", None)
        previous = _read(previous_path, pack.MAX_ARCHIVE) if previous_path else None
        if args.command == "plan":
            result = plan_vault(args.vault, args.out, key, vault_id=args.vault_id,
                                publisher_id=args.publisher_id, publisher_name=args.publisher_name,
                                previous=previous, go_binary=args.go_verifier)
        elif args.command == "seal":
            # Check approval and live sources before opening the signing key.
            _approved(args.vault, args.plan, args.approve, key, previous, args.go_verifier)
            data, result = seal_vault(args.vault, args.plan, args.approve, _read(args.private_key, 3904), key,
                                      previous=previous, go_binary=args.go_verifier)
            _new_file(args.output, data)
            result["output"] = str(args.output)
        else:
            data = _read(args.pack, pack.MAX_ARCHIVE)
            result = (refresh_vault if args.command == "refresh" else vault_status)(args.vault, data, key, args.go_verifier)
    except (ValueError, OSError, RuntimeError, ImportError, KeyError, TypeError, zipfile.BadZipFile, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
