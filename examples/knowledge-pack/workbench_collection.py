"""Closed portable collections of unchanged Genesis shards.

This is an application transport profile, not a Genesis format change or the
single-head Knowledge Pack transport. Verification always requires a detached
public key supplied independently of the archive.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import zipfile

from workbench_store import Workbench, SID, checkpoint, json_bytes, process_lock
import obsidian_vault as vault
import pack
from axm_verify.logic import verify_shard
from axm_verify.crypto import derive_shard_id

FORMAT = "axm-vault-collection-archive/1"
MAX_ARCHIVE = 256 * 1024 * 1024
MAX_EXPANDED = 512 * 1024 * 1024
MAX_MEMBER = 32 * 1024 * 1024
MAX_FILES = 250000
SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@-]*\Z")


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def closure(workbench: Workbench, identifier: str, cancel=None) -> set[str]:
    history = workbench.history(identifier)["editions"]
    objects, pending, note_entries = set(), [], []
    for edition in history:
        checkpoint(cancel)
        catalog = workbench.read_catalog(identifier, edition["head"])
        objects.add(edition["head"])
        pending.extend(entry["shard_id"] for entry in catalog["entries"])
        note_entries.extend(catalog["entries"])
    manifests = {}
    while pending:
        checkpoint(cancel)
        sid = pending.pop()
        if sid in manifests:
            continue
        if sid in objects:
            raise ValueError("A catalog cannot masquerade as a note record")
        root, manifest = workbench._verify(identifier, sid)
        note = pack.loads(vault._read(root / "content/note.json", 65536))
        original = vault._read(
            root / "content/original.md", workbench.limits["note_bytes"]
        )
        if (
            note.get("schema") != "axm-vault-note/1"
            or note.get("vault_id") != identifier
        ):
            raise ValueError("Unexpected note profile in the collection")
        if hashlib.sha256(original).hexdigest() != note["raw_sha256"]:
            raise ValueError("Retained original differs from signed note metadata")
        from axm_build.common import normalize_source_text

        expected_source = normalize_source_text(original.decode("utf-8"))
        if not expected_source.strip():
            expected_source = (
                "Empty note "
                + json.dumps(note["path"])
                + " SHA256 "
                + note["raw_sha256"]
                + "\n"
            )
        spec = workbench.spec(identifier)
        if (
            expected_source.encode("utf-8")
            != (root / "content/source.txt").read_bytes()
            or manifest["metadata"]["namespace"] != "axm/vault/" + identifier
            or manifest["publisher"]
            != {"id": spec["publisher_id"], "name": spec["publisher_name"]}
        ):
            raise ValueError(
                "Note evidence, namespace or publisher differs from its profile"
            )
        note["verified_claim_count"] = manifest["statistics"]["claims"]
        manifests[sid] = note
        pending.extend(manifest.get("supersedes", []))
        if len(manifests) + len(objects) > MAX_FILES:
            raise ValueError("Collection closure exceeds the object bound")
    for entry in note_entries:
        note = manifests[entry["shard_id"]]
        if (
            note["path"] != entry["path"]
            or note["raw_sha256"] != entry["sha256"]
            or note["verified_claim_count"] != entry["claims"]
        ):
            raise ValueError("Signed catalog entry differs from its retained note")
    return objects | set(manifests)


def verify_collection(
    archive: str | Path, trusted_key: str | Path, cancel=None
) -> dict:
    archive = Path(os.path.abspath(archive))
    vault._root(archive.parent)
    key = vault._read(Path(trusted_key), 1344)
    if (
        len(key) != 1344
        or archive.is_symlink()
        or not archive.is_file()
        or archive.stat().st_size > MAX_ARCHIVE
    ):
        raise ValueError(
            "Use a bounded collection archive and an external Genesis public key"
        )
    with (
        zipfile.ZipFile(archive) as zipped,
        tempfile.TemporaryDirectory(prefix="axm-collection-check-") as temp,
    ):
        infos = zipped.infolist()
        if (
            not infos
            or len(infos) > MAX_FILES
            or sum(i.file_size for i in infos) > MAX_EXPANDED
        ):
            raise ValueError("Collection archive exceeds its expansion/member bound")
        names = set()
        for info in infos:
            name = info.filename
            parts = name.split("/")
            mode = info.external_attr >> 16
            if (
                name in names
                or info.is_dir()
                or info.flag_bits & 1
                or stat.S_IFMT(mode) not in (0, stat.S_IFREG)
                or info.file_size > MAX_MEMBER
                or "\\" in name
                or any(not SEGMENT.fullmatch(part) for part in parts)
            ):
                raise ValueError("Unsafe or duplicate collection member")
            if name != "collection.json" and (
                len(parts) < 3 or parts[0] != "objects" or not SID.fullmatch(parts[1])
            ):
                raise ValueError("Unexpected collection member path")
            names.add(name)
        if "collection.json" not in names:
            raise ValueError("Missing collection descriptor")
        descriptor = pack.loads(zipped.read("collection.json"))
        ids = descriptor.get("objects")
        if (
            descriptor.get("format") != FORMAT
            or not isinstance(ids, list)
            or not all(isinstance(s, str) and SID.fullmatch(s) for s in ids)
            or ids != sorted(set(ids))
            or descriptor.get("head") not in ids
        ):
            raise ValueError("Malformed collection descriptor")
        found = {name.split("/")[1] for name in names if name.startswith("objects/")}
        if found != set(ids):
            raise ValueError("Declared object inventory is incomplete or excessive")
        root = Path(temp)
        state = root / "state"
        workbench = Workbench(state)
        for info in infos:
            if info.filename == "collection.json":
                continue
            checkpoint(cancel)
            output = state / info.filename
            output.parent.mkdir(parents=True, exist_ok=True)
            with zipped.open(info) as source, output.open("xb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
            if output.stat().st_size != info.file_size:
                raise ValueError(
                    "Collection member size differs from its directory record"
                )
        key_path = root / "detached.pub"
        key_path.write_bytes(key)
        head_root = state / "objects" / descriptor["head"]
        if verify_shard(head_root, key_path).get("status") != "PASS":
            raise ValueError("Selected catalog failed external-key verification")
        manifest = pack.loads((head_root / "manifest.json").read_bytes())
        if (
            derive_shard_id((head_root / "manifest.json").read_bytes())
            != descriptor["head"]
        ):
            raise ValueError("Catalog identity differs from the descriptor")
        catalog = pack.loads((head_root / "content/catalog.json").read_bytes())
        if catalog.get("vault_id") != descriptor.get("vault_id"):
            raise ValueError("Descriptor vault differs from the signed catalog")
        source = root / "empty-reader-vault"
        source.mkdir()
        workbench.register(
            catalog["vault_id"],
            str(source),
            str(key_path),
            publisher_id=manifest["publisher"]["id"],
            publisher_name=manifest["publisher"]["name"],
        )
        with workbench.db() as con:
            con.execute(
                "INSERT INTO heads VALUES(?,?,?)",
                (catalog["vault_id"], descriptor["head"], catalog["generation"]),
            )
        required = closure(workbench, catalog["vault_id"], cancel)
        if required != set(ids):
            raise ValueError("Archive is not exactly the closed signed collection")
        editions = workbench.history(catalog["vault_id"])["editions"]
        result = {
            "status": "PASS",
            "format": FORMAT,
            "head": descriptor["head"],
            "vault_id": catalog["vault_id"],
            "editions": len(editions),
            "objects": len(ids),
            "current_notes": len(catalog["entries"]),
            "archive_sha256": digest(archive),
            "trusted_key_sha256": hashlib.sha256(key).hexdigest(),
            "authority": "Externally keyed Genesis verification, signed lineage, complete object closure and original-note custody; semantic truth and real-world publisher identity remain separate.",
        }
        workbench.close()
        return result


def export_collection(
    workbench: Workbench, identifier: str, destination: str | Path, cancel=None
) -> dict:
    spec = workbench.spec(identifier)
    output = Path(os.path.abspath(destination))
    vault._root(output.parent)
    if output.exists() or output.is_symlink():
        raise FileExistsError("Preserving the existing collection output")
    with process_lock(workbench.state / "locks" / (identifier + ".lock")):
        head = workbench.head(identifier)
        if not head:
            raise ValueError("Approve an edition before exporting a collection")
        ids = closure(workbench, identifier, cancel)
        descriptor = {
            "format": FORMAT,
            "head": head["head"],
            "vault_id": identifier,
            "objects": sorted(ids),
            "trust": "Supply an independently obtained public key; no archive member grants that authority.",
        }
        fd, temp = tempfile.mkstemp(prefix=".collection-", dir=output.parent)
        os.close(fd)
        try:
            size = count = 0
            with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
                info = zipfile.ZipInfo(
                    "collection.json", date_time=(1980, 1, 1, 0, 0, 0)
                )
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                zipped.writestr(info, json_bytes(descriptor))
                for sid in sorted(ids):
                    for path in sorted((workbench.state / "objects" / sid).rglob("*")):
                        checkpoint(cancel)
                        if path.is_symlink():
                            raise ValueError("Symlink inside the selected object store")
                        if not path.is_file():
                            continue
                        length = path.stat().st_size
                        size += length
                        count += 1
                        if (
                            length > MAX_MEMBER
                            or size > MAX_EXPANDED
                            or count + 1 > MAX_FILES
                        ):
                            raise ValueError(
                                "Collection exceeds the portable transport bound"
                            )
                        name = (
                            "objects/"
                            + sid
                            + "/"
                            + path.relative_to(
                                workbench.state / "objects" / sid
                            ).as_posix()
                        )
                        info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                        info.compress_type = zipfile.ZIP_DEFLATED
                        info.external_attr = 0o100644 << 16
                        with (
                            path.open("rb") as source,
                            zipped.open(info, "w") as target,
                        ):
                            shutil.copyfileobj(source, target, length=1024 * 1024)
            result = verify_collection(temp, spec["public_key"], cancel)
            checkpoint(cancel)
            os.link(temp, output)
            return {
                **result,
                "status": "EXPORTED",
                "output": str(output),
                "bytes": output.stat().st_size,
                "private_key_included": False,
            }
        finally:
            Path(temp).unlink(missing_ok=True)
