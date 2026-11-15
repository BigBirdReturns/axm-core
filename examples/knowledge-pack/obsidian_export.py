"""Read-only Obsidian projections of independently verified AXM knowledge packs.

The exported notes, Canvas and Base are disposable views. The original archive
remains byte-exact, and verification always requires a separately supplied key.
"""

from __future__ import annotations

import ctypes
import errno
import json
import os
from pathlib import Path
import posixpath
import re
import stat
import sys
import tempfile

import pack


SCHEMA = "axm-obsidian-projection/0.1"
SCOPE = (
    "PASS establishes artifact integrity and evidence binding under the separately "
    "supplied trust anchor. Claim truth, source completeness, real-world currentness "
    "and publisher identity require separate evidence. Current means the head "
    "edition of this supplied pack; history contains its predecessor editions."
)
_TICK = chr(96)


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _frontmatter(values: dict) -> str:
    # The pinned reader understands folded presentation scalars, but does not
    # JSON-decode quoted strings. One indented line preserves literal Unicode,
    # quotes and backslashes while preventing frontmatter key injection.
    lines = ["---"]
    for name, value in values.items():
        if name in {"title", "summary"}:
            lines.extend((f"{name}: >-", "  " + _one_line(value)))
        else:
            lines.append(f"{name}: {json.dumps(value, ensure_ascii=True, sort_keys=True)}")
    return "\n".join(lines) + "\n---\n\n"


def _one_line(value: object) -> str:
    return " ".join(str(value).split())


def _escape(value: object) -> str:
    text = _one_line(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for char in "\\" + _TICK + "*_[]{}()#+-.!|~":
        text = text.replace(char, "\\" + char)
    return text


def _inline(value: object) -> str:
    text = _one_line(value)
    size = max((len(run) for run in re.findall(_TICK + "+", text)), default=0) + 1
    mark = _TICK * size
    padding = " " if text.startswith(_TICK) or text.endswith(_TICK) else ""
    return f"{mark}{padding}{text}{padding}{mark}"


def _quote(value: str) -> str:
    size = max(3, max((len(run) for run in re.findall(_TICK + "+", value)), default=0) + 1)
    fence = _TICK * size
    return fence + "text\n" + value + ("" if value.endswith("\n") else "\n") + fence + "\n"


def _note_path(shard_id: str, head: str, claim_id: str | None = None) -> str:
    role = "current" if shard_id == head else "history"
    stem = f"record-{shard_id}" if claim_id is None else f"claim-{shard_id}-{claim_id}"
    return f"{role}/{stem}.md"


def _link(origin: str, target: str, label: str) -> str:
    relative = posixpath.relpath(target, posixpath.dirname(origin) or ".")
    return "[[" + relative.removesuffix(".md") + "|" + label + "]]"


def _unchecked(verdict: dict, shard_id: str | None = None) -> list[str]:
    return sorted({
        profile
        for check in verdict.get("checks", [])
        if shard_id is None or check["shard_id"] == shard_id
        for profile in check.get("profiles_unchecked", [])
    })


def _edition(role: str) -> str:
    return "Current edition (head of the supplied pack)" if role == "current" else "Historical predecessor edition"


def _profiles_text(profiles: list[str]) -> str:
    return ", ".join(_inline(profile) for profile in profiles) if profiles else "none reported"


def _base() -> dict:
    # JSON is a YAML-compatible serialization of the native .base format.
    order = [
        "file.name", "note.title", "note.axm_tier", "note.created",
        "note.axm_shard_id", "note.axm_claim_id",
    ]
    return {
        "filters": {"and": ['file.hasTag("axm-claim")']},
        "properties": {
            "note.title": {"displayName": "Claim"},
            "note.axm_tier": {"displayName": "AXM extraction tier"},
            "note.created": {"displayName": "Signed creation time"},
            "note.axm_shard_id": {"displayName": "Shard"},
            "note.axm_claim_id": {"displayName": "Claim ID"},
        },
        "views": [
            {
                "type": "table", "name": name,
                "filters": {"and": [f'file.inFolder("{role}")']},
                "order": list(order),
            }
            for role, name in (
                ("current", "Current pack claims"),
                ("history", "Historical claims"),
            )
        ],
    }


def render_vault(
    data: bytes, trusted_key: bytes, go_binary: str | None = None
) -> tuple[dict[str, bytes], dict]:
    """Return deterministic vault bytes and metadata after the existing verifier passes.

    No path is written by this function. All note identities include their shard,
    so a repeated canonical claim in a later edition remains a distinct page.
    """
    verdict = pack.inspect_pack(data, trusted_key, go_binary)
    if verdict["status"] != "PASS":
        raise ValueError("Obsidian export requires a PASS from pack.inspect_pack")
    raw = pack.unpack(data)
    head = verdict["head"]
    records = verdict["records"]
    files: dict[str, bytes] = {"_axm/source.axmpack.zip": data}
    pages: dict[str, dict] = {}
    nodes: list[dict] = []
    edges: list[dict] = []
    head_record = next(record for record in records if record["id"] == head)

    for column, record in enumerate(records):
        sid = record["id"]
        role = "current" if sid == head else "history"
        record_path = _note_path(sid, head)
        record_node = f"record-{sid}"
        profiles = _unchecked(verdict, sid)
        title = record["title"]
        record_fields = {
            "title": title,
            "summary": f"{_edition(role)}; {len(record['claims'])} recorded claims.",
            "tags": ["axm", "axm-record", f"axm-{role}"],
            "axm_shard_id": sid,
            "axm_role": role,
            "created": record["created_at"],
            "axm_supersedes": record["supersedes"],
            "axm_profiles_unchecked": profiles,
        }
        lines = [
            "# " + _escape(title), "", _edition(role) + ".", "", SCOPE, "",
            "## Record identity", "",
            "- Shard: " + _inline(sid),
            "- Signed creation time: " + _inline(record["created_at"]),
            "- Cryptographic suite: " + _inline(record["suite"]),
            "- Profiles reported unchecked: " + _profiles_text(profiles), "",
            "## Claims", "",
        ]
        nodes.append({
            "id": record_node, "type": "file", "file": record_path,
            "x": column * 620, "y": 0, "width": 540, "height": 200,
            "color": "4" if role == "current" else "2",
        })
        pages[record_path] = {
            "kind": "record", "shard_id": sid, "role": role, "title": title,
            "supersedes": list(record["supersedes"]),
            "profiles_unchecked": profiles,
        }

        for row, claim in enumerate(record["claims"]):
            cid = claim["claim_id"]
            claim_path = _note_path(sid, head, cid)
            claim_node = f"claim-{sid}-{cid}"
            claim_title = _one_line(claim["object_label"])[:200] or cid
            summary = _one_line(
                f"{claim['subject_label']} {claim['predicate']} {claim['object_label']}"
            )
            fields = {
                "title": claim_title,
                "summary": summary,
                "tags": ["axm", "axm-claim", f"axm-{role}"],
                "axm_shard_id": sid,
                "axm_claim_id": cid,
                "axm_role": role,
                "created": record["created_at"],
                "axm_tier": claim["tier"],
                "axm_profiles_unchecked": profiles,
            }
            claim_lines = [
                "# " + _escape(claim_title), "", _edition(role) + ".", "", SCOPE, "",
                "## Claim identity", "",
                "- Shard: " + _inline(sid),
                "- Claim: " + _inline(cid),
                "- Record: " + _link(claim_path, record_path, "record"),
                "- Signed creation time: " + _inline(record["created_at"]),
                "- AXM extraction tier: " + _inline(claim["tier"]),
                "- Subject identity: " + _inline(claim["subject"]),
                "- Predicate: " + _inline(claim["predicate"]),
                "- Object type: " + _inline(claim["object_type"]),
                "- Profiles reported unchecked: " + _profiles_text(profiles), "",
                "## Recorded assertion", "", "Subject label:", "",
                _quote(str(claim["subject_label"])), "Object value:", "",
                _quote(str(claim["object_label"])), "## Evidence", "",
            ]
            evidence = []
            for item in claim["evidence"]:
                source = item["source"]
                source_bytes = raw[f"shards/{sid}/{source}"]
                start, end = item["byte_start"], item["byte_end"]
                # The verifier already bound this exact range to its source hash.
                quote = source_bytes[start:end].decode("utf-8")
                projected_source = f"_axm/sources/{sid}/{source}"
                link = posixpath.relpath(projected_source, posixpath.dirname(claim_path))
                evidence.append({
                    "source_hash": item["source_hash"], "source": source,
                    "byte_start": start, "byte_end": end,
                    "provenance_id": item["provenance_id"], "text": quote,
                })
                claim_lines.extend([
                    "### " + _escape(item["provenance_id"]), "",
                    "- Source: [" + source + "](" + link + ")",
                    "- Source SHA-256: " + _inline(item["source_hash"]),
                    f"- Exact byte range: [{start}, {end}) (start inclusive; end exclusive).",
                    "- Provenance: " + _inline(item["provenance_id"]), "",
                    _quote(quote),
                ])
            files[claim_path] = (_frontmatter(fields) + "\n".join(claim_lines).rstrip() + "\n").encode("utf-8")
            pages[claim_path] = {
                "kind": "claim", "shard_id": sid, "claim_id": cid, "role": role,
                "title": claim_title, "axm_tier": claim["tier"], "evidence": evidence,
                "profiles_unchecked": profiles,
            }
            lines.append("- " + _link(record_path, claim_path, cid))
            nodes.append({
                "id": claim_node, "type": "file", "file": claim_path,
                "x": column * 620, "y": 260 + row * 240, "width": 540, "height": 180,
                "color": "4" if role == "current" else "2",
            })
            edges.append({
                "id": f"contains-{sid}-{cid}", "fromNode": record_node,
                "toNode": claim_node, "toEnd": "arrow", "label": "contains",
            })

        lines.extend(["", "## Predecessors", ""])
        if record["supersedes"]:
            for previous in record["supersedes"]:
                lines.append("- Supersedes " + _link(record_path, _note_path(previous, head), previous))
                edges.append({
                    "id": f"supersedes-{sid}-{previous}", "fromNode": record_node,
                    "toNode": f"record-{previous}", "toEnd": "arrow", "label": "supersedes",
                })
        else:
            lines.append("This record declares no predecessor in the supplied pack.")
        lines.extend(["", "## Exact source copies", ""])
        for source in record["sources"]:
            source_path = source["path"]
            projected_source = f"_axm/sources/{sid}/{source_path}"
            files[projected_source] = raw[f"shards/{sid}/{source_path}"]
            relative = posixpath.relpath(projected_source, posixpath.dirname(record_path))
            lines.append(
                "- [" + source_path + "](" + relative + "): SHA-256 "
                + _inline(source["hash"]) + f"; {len(files[projected_source])} bytes."
            )
        files[record_path] = (_frontmatter(record_fields) + "\n".join(lines).rstrip() + "\n").encode("utf-8")

    profiles = _unchecked(verdict)
    index_fields = {
        "title": head_record["title"],
        "summary": "Rebuildable Obsidian projection of a verified AXM knowledge pack.",
        "tags": ["axm", "axm-index"],
        "axm_head": head,
        "created": head_record["created_at"],
        "axm_profiles_unchecked": profiles,
    }
    index_lines = [
        "# AXM knowledge pack", "", SCOPE, "",
        "- Pack head: " + _inline(head),
        "- Archive SHA-256: " + _inline(verdict["archive_sha256"]),
        "- Separately supplied trust-anchor SHA-256: " + _inline(verdict["trusted_key_sha256"]),
        "- Profiles reported unchecked: " + _profiles_text(profiles), "",
        "[[Knowledge.canvas|Knowledge map]] · [[Evidence.base|Evidence tables]]", "",
        "## Editions", "",
    ]
    for record in records:
        role = "current" if record["id"] == head else "history"
        index_lines.append(
            "- " + _edition(role) + ": "
            + _link("index.md", _note_path(record["id"], head), record["id"])
        )
    index_lines.extend([
        "", "## Source and authority", "",
        "The [original archive](_axm/source.axmpack.zip) is copied byte for byte. "
        "Exact source files live under _axm/sources. These notes, their metadata, "
        "the Base and the Canvas are derived views. Their edits do not change "
        "the signed archive. Verification requires an independently supplied "
        "trusted public key; no exported file supplies that authority.", "",
    ])
    files["index.md"] = (_frontmatter(index_fields) + "\n".join(index_lines)).encode("utf-8")
    files["Knowledge.canvas"] = _json_bytes({"nodes": nodes, "edges": edges})
    files["Evidence.base"] = _json_bytes(_base())
    metadata = {
        "schema": SCHEMA, "archive_sha256": verdict["archive_sha256"],
        "trusted_key_sha256": verdict["trusted_key_sha256"], "head": head,
        "profiles_unchecked": profiles, "scope": SCOPE,
        "pages": dict(sorted(pages.items())),
        "counts": {
            "records": len(records),
            "claims": sum(len(record["claims"]) for record in records),
            "files": len(files) + 1,
        },
    }
    files["_axm/projection.json"] = _json_bytes(metadata)
    return dict(sorted(files.items())), metadata


def _same_tree(destination: Path, files: dict[str, bytes]) -> bool:
    """Compare the complete owned tree, refusing links, special files and extras."""
    if destination.is_symlink() or not destination.is_dir():
        return False
    expected_dirs = {
        parent.as_posix()
        for name in files
        for parent in Path(name).parents
        if parent != Path(".")
    }
    found_files: set[str] = set()
    found_dirs: set[str] = set()
    pending = [destination]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                path = Path(entry.path)
                relative = path.relative_to(destination).as_posix()
                mode = entry.stat(follow_symlinks=False).st_mode
                if stat.S_ISDIR(mode):
                    if relative not in expected_dirs:
                        return False
                    found_dirs.add(relative)
                    pending.append(path)
                elif stat.S_ISREG(mode):
                    if relative not in files or path.read_bytes() != files[relative]:
                        return False
                    found_files.add(relative)
                else:
                    return False
    return found_files == set(files) and found_dirs == expected_dirs


def _rename_new(source: Path, destination: Path) -> None:
    """Atomically install a directory without replacing even an empty target."""
    if os.name == "nt":
        # Windows os.rename already refuses an existing destination.
        os.rename(source, destination)
        return
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        operation = libc.renameat2
        operation.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        operation.restype = ctypes.c_int
        result = operation(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    elif sys.platform == "darwin" and hasattr(libc, "renamex_np"):
        operation = libc.renamex_np
        operation.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        operation.restype = ctypes.c_int
        result = operation(os.fsencode(source), os.fsencode(destination), 4)
    else:
        raise OSError(errno.ENOSYS, "Atomic directory creation without replacement is unavailable")
    if result:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))


def export_vault(
    data: bytes, trusted_key: bytes, destination: str | os.PathLike,
    go_binary: str | None = None,
) -> dict:
    """Publish a fresh verified projection atomically, or accept an identical tree.

    The destination parent must exist. Existing content is never merged or
    overwritten, including an empty directory or an edited prior projection.
    """
    files, metadata = render_vault(data, trusted_key, go_binary)
    target = Path(os.path.abspath(os.fspath(destination)))
    if target.is_symlink():
        raise FileExistsError(f"Refusing a symlink destination: {target}")
    if target.exists():
        if _same_tree(target, files):
            return {**metadata, "destination": str(target), "idempotent": True}
        raise FileExistsError(f"Destination is not an identical AXM projection: {target}")
    if not target.parent.is_dir():
        raise FileNotFoundError(f"Destination parent must already exist: {target.parent}")
    if any(parent.is_symlink() for parent in (target.parent, *target.parent.parents)):
        raise ValueError("Destination ancestors must not be symlinks")
    with tempfile.TemporaryDirectory(prefix=".axm-projection-", dir=target.parent) as temporary:
        staging = Path(temporary)
        for name, content in files.items():
            output = staging / name
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(content)
        try:
            _rename_new(staging, target)
        except FileExistsError:
            if target.is_symlink() or not _same_tree(target, files):
                raise FileExistsError(f"Destination appeared and differs from the AXM projection: {target}") from None
            return {**metadata, "destination": str(target), "idempotent": True}
    return {**metadata, "destination": str(target), "idempotent": False}
