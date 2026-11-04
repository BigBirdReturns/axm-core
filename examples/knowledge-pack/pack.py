"""Experimental ZIP transport around unchanged AXM Genesis v1 shards.

The trusted public key is an explicit, separate input. No archive member is a
trust anchor. Transport metadata carries no semantic or recency authority.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from typing import Any

FORMAT = "axm-pack-preview/0.1"
MAX_ARCHIVE = 8 * 1024 * 1024
MAX_TOTAL = 32 * 1024 * 1024
MAX_MEMBER = 8 * 1024 * 1024
MAX_MEMBERS = 1024
SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]*$")
SHARD = re.compile(r"^sh1_[0-9a-f]{64}$")
DEVICE = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.I)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def loads(raw: bytes | str) -> Any:
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError("Duplicate JSON key: " + key)
            out[key] = value
        return out
    return json.loads(raw, object_pairs_hook=unique)


def decode(value: str, cap: int = MAX_ARCHIVE) -> bytes:
    if not isinstance(value, str) or len(value) > ((cap + 2) // 3) * 4:
        raise ValueError("Input exceeds the byte limit")
    data = base64.b64decode(value, validate=True)
    if len(data) > cap:
        raise ValueError("Input exceeds the byte limit")
    return data


def unpack(data: bytes) -> dict[str, bytes]:
    """Validate names, collisions and expansion limits before creating files."""
    if len(data) > MAX_ARCHIVE:
        raise ValueError("Archive exceeds 8 MiB")
    files: dict[str, bytes] = {}
    folded: set[str] = set()
    total = 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if not entries or len(entries) > MAX_MEMBERS:
            raise ValueError("Archive must contain 1 to 1024 members")
        for entry in entries:
            name = entry.filename
            parts = name.split("/")
            if (not all(SEGMENT.fullmatch(p) and not p.endswith(".") and not DEVICE.match(p) for p in parts)
                    or len(parts) > 8 or len(name) > 220):
                raise ValueError("Unsafe archive member: " + name[:100])
            if name.casefold() in folded:
                raise ValueError("Duplicate or case-colliding archive member")
            folded.add(name.casefold())
            mode = (entry.external_attr >> 16) & 0o170000
            if mode not in (0, stat.S_IFREG) or entry.is_dir():
                raise ValueError("Only regular archive files are accepted")
            if entry.flag_bits & 1 or entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise ValueError("Encrypted or unsupported ZIP member")
            total += entry.file_size
            if entry.file_size > MAX_MEMBER or total > MAX_TOTAL:
                raise ValueError("Expanded archive exceeds limit")
            if entry.file_size > max(entry.compress_size, 1) * 200:
                raise ValueError("Archive expansion ratio exceeds limit")
            if name != "pack.json" and not (
                len(parts) >= 3 and parts[0] == "shards" and SHARD.fullmatch(parts[1])
            ):
                raise ValueError("Unexpected transport member: " + name)
            with archive.open(entry) as stream:
                value = stream.read(MAX_MEMBER + 1)
            if len(value) != entry.file_size or len(value) > MAX_MEMBER:
                raise ValueError("ZIP member size mismatch")
            files[name] = value
    if "pack.json" not in files:
        raise ValueError("Missing experimental transport manifest")
    metadata = loads(files["pack.json"])
    if not isinstance(metadata, dict) or set(metadata) != {"format", "head", "shards"}:
        raise ValueError("Unexpected transport manifest fields")
    if metadata["format"] != FORMAT:
        raise ValueError("Unsupported experimental transport version")
    ids = metadata["shards"]
    if (not isinstance(ids, list) or not ids or len(ids) > 32
            or any(not isinstance(s, str) or not SHARD.fullmatch(s) for s in ids)
            or len(set(ids)) != len(ids) or ids != sorted(ids)):
        raise ValueError("Invalid transport shard inventory")
    present = sorted({name.split("/")[1] for name in files if name.startswith("shards/")})
    if present != ids or metadata["head"] not in ids:
        raise ValueError("Transport inventory does not match members")
    for sid in ids:
        if f"shards/{sid}/manifest.json" not in files:
            raise ValueError("Missing shard manifest")
    # Files cannot simultaneously serve as parent directories, on any platform.
    names = set(files)
    for name in names:
        parts = name.split("/")
        if any("/".join(parts[:i]).casefold() in folded for i in range(1, len(parts))):
            raise ValueError("File/directory collision")
    return files


def pack_files(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, data)
    result = output.getvalue()
    if len(result) > MAX_ARCHIVE:
        raise ValueError("Result exceeds archive limit")
    return result


def rows(raw: bytes) -> list[dict]:
    return [loads(line) for line in raw.splitlines() if line]


def inspect_pack(data: bytes, trusted_key: bytes, go_binary: str | None = None) -> dict:
    from axm_verify.logic import verify_shard
    from axm_verify.crypto import derive_shard_id
    if len(trusted_key) != 1344:
        raise ValueError("Supply a separate 1344-byte trusted hybrid public key")
    started = time.perf_counter()
    files = unpack(data)
    transport = loads(files["pack.json"])
    manifests: dict[str, dict] = {}
    checks: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="axm-pack-read-") as tmp:
        root = Path(tmp)
        anchor = root / "trusted.pub"
        anchor.write_bytes(trusted_key)
        for name, content in files.items():
            if name == "pack.json":
                continue
            path = root.joinpath(*name.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        for sid in transport["shards"]:
            path = root / "shards" / sid
            mark = time.perf_counter()
            result = verify_shard(path, trusted_key_path=anchor)
            checks.append({"implementation": "Python / Genesis", "shard_id": sid,
                           "status": result["status"], "errors": result.get("errors", []),
                           "profiles_unchecked": result.get("profiles_unchecked", []),
                           "milliseconds": round((time.perf_counter() - mark) * 1000, 3)})
            if go_binary:
                mark = time.perf_counter()
                process = subprocess.run([go_binary, "shard", str(path), "--trusted-key", str(anchor)],
                                         capture_output=True, text=True, timeout=15)
                native = loads(process.stdout)
                if process.returncode not in (0, 1, 2):
                    raise ValueError("Independent verifier process failed")
                checks.append({"implementation": "Go / independent verifier", "shard_id": sid,
                               "status": native["status"], "errors": native.get("errors", []),
                               "profiles_unchecked": native.get("profiles_unchecked", []),
                               "milliseconds": round((time.perf_counter() - mark) * 1000, 3)})
            if result["status"] == "PASS":
                if derive_shard_id((path / "manifest.json").read_bytes()) != sid:
                    raise ValueError("Directory identity differs from canonical manifest identity")
                manifests[sid] = loads((path / "manifest.json").read_bytes())
    result = {"format": FORMAT, "status": "FAIL", "archive_sha256": digest(data),
              "archive_bytes": len(data), "trusted_key_sha256": digest(trusted_key),
              "head": transport["head"], "checks": checks, "records": [],
              "scope": "Artifact integrity and evidence binding. Claim truth, source completeness, currentness and publisher identity require separate evidence."}
    if any(c["status"] != "PASS" for c in checks):
        result["milliseconds"] = round((time.perf_counter() - started) * 1000, 3)
        return result
    references: set[str] = set()
    for sid, manifest in manifests.items():
        for previous in manifest.get("supersedes", []):
            if previous not in manifests or previous == sid:
                raise ValueError("History is incomplete or self-referential")
            references.add(previous)
    if set(manifests) - references != {transport["head"]}:
        raise ValueError("Transport must have one unambiguous history head")
    ordered: list[str] = []
    active: set[str] = set()
    def visit(sid: str):
        if sid in active:
            raise ValueError("History contains a cycle")
        if sid in ordered:
            return
        active.add(sid)
        for previous in manifests[sid].get("supersedes", []):
            visit(previous)
        active.remove(sid)
        ordered.append(sid)
    visit(transport["head"])
    if set(ordered) != set(manifests):
        raise ValueError("Unconnected records in transport")
    for sid in ordered:
        prefix = f"shards/{sid}/"
        m = manifests[sid]
        entities = {x["entity_id"]: x["label"] for x in rows(files[prefix + "graph/entities.jsonl"])}
        spans = rows(files[prefix + "evidence/spans.jsonl"])
        provenance = rows(files[prefix + "graph/provenance.jsonl"])
        claims = []
        for c in rows(files[prefix + "graph/claims.jsonl"]):
            evidence = []
            for p in provenance:
                if p["claim_id"] != c["claim_id"]:
                    continue
                source = next(s["path"] for s in m["sources"] if s["hash"] == p["source_hash"])
                value = files[prefix + source][p["byte_start"]:p["byte_end"]].decode("utf-8")
                # The UI joins canonical tables after verification; it never uses a generated answer as evidence.
                evidence.append({**p, "source": source, "text": value})
            claims.append({**c, "subject_label": entities.get(c["subject"], c["subject"]),
                           "object_label": entities.get(c["object"], c["object"]), "evidence": evidence})
        sources = [{**s, "bytes": len(files[prefix + s["path"]]),
                    "text": files[prefix + s["path"]].decode("utf-8", errors="replace")}
                   for s in m["sources"]]
        result["records"].append({"id": sid, "title": m["metadata"].get("title", "Untitled"),
                                  "created_at": m["metadata"]["created_at"], "claims": claims,
                                  "sources": sources, "supersedes": m.get("supersedes", []),
                                  "suite": m["suite"], "manifest": m})
    result["status"] = "PASS"
    result["milliseconds"] = round((time.perf_counter() - started) * 1000, 3)
    return result


def challenge(data: bytes, trusted_key: bytes, mode: str, go_binary: str | None = None) -> dict:
    original = digest(data)
    files = unpack(data)
    if mode == "wrong-key":
        other = bytearray(trusted_key)
        other[0] ^= 1
        result = inspect_pack(data, bytes(other), go_binary)
        changed = "Detached trust anchor, byte 0"
    else:
        head = loads(files["pack.json"])["head"]
        if mode == "source":
            name, position = f"shards/{head}/content/source.txt", 0
        elif mode in ("ed25519", "mldsa"):
            name = f"shards/{head}/sig/manifest.sig"
            position = 0 if mode == "ed25519" else 64
        else:
            raise ValueError("Unknown challenge")
        value = bytearray(files[name])
        value[position] ^= 1
        files[name] = bytes(value)
        result = inspect_pack(pack_files(files), trusted_key, go_binary)
        changed = f"{name}, byte {position}"
    result["challenge"] = {"mode": mode, "changed": changed,
                            "original_sha256": original, "original_preserved": digest(data) == original}
    return result


def compile_pack(title: str, source: str, quotes: list[str], secret_key: bytes,
                 trusted_key: bytes, previous: bytes | None = None,
                 go_binary: str | None = None, created_at: str | None = None) -> tuple[bytes, dict]:
    from axm_build.common import normalize_source_text
    from axm_build.compiler_generic import CompilerConfig, compile_generic_shard
    from axm_verify.crypto import derive_shard_id
    if not isinstance(title, str) or not title.strip() or len(title) > 180:
        raise ValueError("Provide a title of 1 to 180 characters")
    if not isinstance(source, str) or not source.strip() or len(source.encode("utf-8")) > 1024 * 1024:
        raise ValueError("Provide nonempty UTF-8 source text, at most 1 MiB")
    if not isinstance(quotes, list) or not 1 <= len(quotes) <= 20 or any(not isinstance(q, str) for q in quotes):
        raise ValueError("Choose 1 to 20 exact evidence excerpts")
    normalized = normalize_source_text(source)
    canonical_quotes = [normalize_source_text(q).rstrip("\n") for q in quotes]
    if len(set(canonical_quotes)) != len(canonical_quotes):
        raise ValueError("Duplicate evidence excerpts")
    for quote in canonical_quotes:
        if not quote or normalized.count(quote) != 1:
            raise ValueError("Each excerpt must occur exactly once in the normalized source")
    files = {}
    predecessors: tuple[str, ...] = ()
    if previous:
        verdict = inspect_pack(previous, trusted_key, go_binary)
        if verdict["status"] != "PASS":
            raise ValueError("Previous pack failed verification")
        files = unpack(previous)
        predecessors = (loads(files["pack.json"])["head"],)
    with tempfile.TemporaryDirectory(prefix="axm-pack-build-") as tmp:
        root = Path(tmp)
        raw = root / "original.txt"
        raw.write_bytes(source.encode("utf-8"))
        candidates = root / "candidates.jsonl"
        candidates.write_text("".join(json.dumps({"subject": title, "predicate": "records_excerpt",
            "object": q, "object_type": "literal:string", "tier": 1, "evidence": q},
            ensure_ascii=False) + "\n" for q in canonical_quotes), encoding="utf-8")
        out = root / "sealed"
        cfg = CompilerConfig(source_path=raw, candidates_path=candidates, out_dir=out,
            private_key=secret_key, publisher_id="@axm_pack_studio_demo",
            publisher_name="AXM Pack Studio demonstration identity", namespace="axm/pack-studio",
            title=title.strip(), created_at=created_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            supersedes=predecessors, lineage_action="amend", lineage_note="A new edition; predecessors remain intact.",
            extra_content=(("original.txt", raw),))
        with contextlib.redirect_stdout(io.StringIO()):
            if not compile_generic_shard(cfg):
                raise ValueError("Genesis rejected the compilation")
        sid = derive_shard_id((out / "manifest.json").read_bytes())
        if len(rows((out / "graph/claims.jsonl").read_bytes())) != len(quotes):
            raise ValueError("Compiler retained a different number of excerpts than requested")
        for p in out.rglob("*"):
            if p.is_file():
                files[f"shards/{sid}/{p.relative_to(out).as_posix()}"] = p.read_bytes()
    ids = sorted({p.split("/")[1] for p in files if p.startswith("shards/")})
    files["pack.json"] = json.dumps({"format": FORMAT, "head": sid, "shards": ids}, sort_keys=True, separators=(",", ":")).encode()
    archive = pack_files(files)
    verdict = inspect_pack(archive, trusted_key, go_binary)
    if verdict["status"] != "PASS":
        raise ValueError("Produced pack failed independent verification")
    return archive, verdict


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--trusted-key", required=True, type=Path)
    parser.add_argument("--go-verifier")
    args = parser.parse_args()
    try:
        result = inspect_pack(args.archive.read_bytes(), args.trusted_key.read_bytes(), args.go_verifier)
        print(json.dumps(result, ensure_ascii=False))
        raise SystemExit(0 if result["status"] == "PASS" else 1)
    except (ValueError, OSError, zipfile.BadZipFile) as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
