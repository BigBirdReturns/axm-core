"""Durable, incrementally sealed collections for AXM Vault Workbench.

State/catalog locators and SQL indexes are local derived state. Every accepted
collection is a Genesis-signed catalog referencing independently signed notes.
The compiler and verification format are unchanged. Mutable notes never enter
accepted retrieval until an explicit digest-bound approval succeeds.
"""

from __future__ import annotations

import contextlib
import difflib
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import tempfile
import threading
import uuid


import workbench_paths  # noqa: F401 -- bootstrap repository or packaged runtime roots
import obsidian_vault as vault
from obsidian_export import _frontmatter, _quote, _escape
from obsidian_bridge import _memory_class
import pack
from axm_forge.vault_extraction import (
    Extractor,
    canonical,
    deterministic_metadata,
    validate_proposals,
)
from axm_build.common import normalize_source_text
from axm_build.compiler_generic import CompilerConfig, compile_generic_shard
from axm_verify.logic import verify_shard
from axm_verify.crypto import derive_shard_id

VERSION = "0.4.0"
CATALOG = "axm-vault-collection/1"
PLAN = "axm-vault-workbench-plan/1"
VIEWS = "_AXM-Workbench"
VIEW_SCHEMA = "axm-vault-workbench-view/1"
DEFAULT_LIMITS = {
    "notes": 10000,
    "note_bytes": 1024 * 1024,
    "vault_bytes": 128 * 1024 * 1024,
    "entries": 100000,
}
SID = re.compile(r"sh1_[0-9a-f]{64}\Z")
PLAN_ID = re.compile(r"r_[0-9a-f]{32}\Z")
AUTHORITY = "Signed source custody and reviewed assertions. Exact evidence binding does not prove semantic truth; working edits and proposals remain separate."


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def json_bytes(value) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def atomic_json(path: Path, value) -> None:
    vault._root(path.parent)
    if path.is_symlink():
        raise ValueError("Refusing a symlink state file")
    fd, temp = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(json_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


@contextlib.contextmanager
def process_lock(path: Path):
    """OS ownership releases on process death; no time-based lock stealing."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    handle = os.fdopen(fd, "r+b")
    try:
        if os.name == "nt":
            import msvcrt

            if os.fstat(fd).st_size == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        handle.close()


class Cancelled(RuntimeError):
    pass


def checkpoint(cancel):
    if cancel is not None and cancel.is_set():
        raise Cancelled(
            "Operation cancelled; previously accepted artifacts remain unchanged"
        )


class Workbench:
    def __init__(self, state: str | Path):
        self.state = Path(os.path.abspath(state))
        if self.state.exists():
            vault._root(self.state)
            if not (self.state / "config.json").is_file() and any(self.state.iterdir()):
                raise ValueError("State must be new and empty or an owned workbench state")
        else:
            self.state.mkdir(parents=True, mode=0o700)
        vault._root(self.state)
        if os.name != "nt":
            self.state.chmod(0o700)
        for name in ("plans", "objects", "cache", "locks", "query"):
            (self.state / name).mkdir(exist_ok=True)
            vault._root(self.state / name)
        self.config_path = self.state / "config.json"
        if not self.config_path.exists():
            vault._new_file(
                self.config_path,
                json_bytes(
                    {
                        "schema": "axm-vault-workbench-config/1",
                        "vaults": [],
                        "provider": {"type": "none"},
                        "limits": DEFAULT_LIMITS,
                        "max_model_calls": 32,
                    }
                ),
            )
        self.config = pack.loads(vault._read(self.config_path, 1024 * 1024))
        if self.config.get("schema") != "axm-vault-workbench-config/1" or not isinstance(self.config.get("vaults"), list):
            raise ValueError("Unrecognized workbench state configuration")
        self.extractor = Extractor(self.config.get("provider"))
        self.limits = dict(DEFAULT_LIMITS, **self.config.get("limits", {}))
        self.max_model_calls = self.config.get("max_model_calls", 32)
        if (
            type(self.max_model_calls) is not int
            or not 0 <= self.max_model_calls <= 1000
        ):
            raise ValueError("Explicit model-call bound must be 0..1000")
        self.db_path = self.state / "state.sqlite"
        if self.db_path.is_symlink():
            raise ValueError("State database cannot be a symlink")
        with self.db() as con:
            con.executescript("""PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS heads(vault_id TEXT PRIMARY KEY,head TEXT,generation INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY,vault_id TEXT,status TEXT,digest TEXT,summary TEXT,result TEXT);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,state TEXT,operation TEXT,detail TEXT,error TEXT,created TEXT);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,at TEXT,kind TEXT,body TEXT);
            """)
        self.index = None
        self.index_lock = threading.RLock()

    @contextlib.contextmanager
    def db(self):
        con = sqlite3.connect(self.db_path, timeout=20)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def event(self, kind, body):
        with self.db() as con:
            con.execute(
                "INSERT INTO events(at,kind,body) VALUES(?,?,?)",
                (now(), kind, json.dumps(body)),
            )

    def spec(self, identifier: str) -> dict:
        if not isinstance(identifier, str) or not vault.ID.fullmatch(identifier):
            raise ValueError("Choose a registered vault identifier")
        found = [v for v in self.config["vaults"] if v["id"] == identifier]
        if len(found) != 1:
            raise ValueError("Unknown or ambiguous vault identifier")
        return dict(found[0])

    def register(
        self,
        identifier: str,
        path: str,
        public_key: str,
        private_key: str | None = None,
        *,
        label: str | None = None,
        publisher_id="local-vault-publisher",
        publisher_name="Local vault publisher",
    ):
        if not vault.ID.fullmatch(identifier):
            raise ValueError("Vault id must be a portable identifier")
        root = vault._root(path)
        if (
            self.state == root
            or root in self.state.parents
            or self.state in root.parents
        ):
            raise ValueError("Source vault and workbench state must be separate trees")
        public = Path(os.path.abspath(public_key))
        private = Path(os.path.abspath(private_key)) if private_key else None
        if len(vault._read(public, 1344)) != 1344:
            raise ValueError("Use a detached 1344-byte Genesis hybrid public key")
        for other in self.config["vaults"]:
            old = Path(other["path"])
            if (
                other["id"] == identifier
                or root == old
                or root in old.parents
                or old in root.parents
            ):
                raise ValueError(
                    "Vault identifiers and source trees must be distinct and non-overlapping"
                )
        for other in self.config["vaults"]:
            for key_name in ("public_key", "private_key"):
                if other.get(key_name) and root in Path(other[key_name]).parents:
                    raise ValueError(
                        "A new source tree cannot contain an existing signing identity"
                    )
        roots = [root, *[Path(v["path"]) for v in self.config["vaults"]]]
        for key in (public, private):
            if key and any(key == r or r in key.parents for r in roots):
                raise ValueError("Keys must stay outside every source vault")
        if private and len(vault._read(private, 3904)) != 3904:
            raise ValueError("Use a 3904-byte Genesis hybrid private key")
        if any(
            not isinstance(x, str) or not x.strip() or len(x) > 180
            for x in (publisher_id, publisher_name)
        ):
            raise ValueError("Publisher identity must be explicit nonempty text")
        item = {
            "id": identifier,
            "label": label or identifier,
            "path": str(root),
            "public_key": str(public),
            "trusted_key_sha256": sha(vault._read(public, 1344)),
            "private_key": str(private) if private else None,
            "publisher_id": publisher_id,
            "publisher_name": publisher_name,
        }
        self.config["vaults"].append(item)
        atomic_json(self.config_path, self.config)
        return {"id": identifier, "path": str(root), "label": item["label"]}

    def configure_provider(self, provider: dict):
        extractor = Extractor(provider)
        self.config["provider"] = dict(provider)
        atomic_json(self.config_path, self.config)
        self.extractor = extractor
        return extractor.describe()

    def _verify(self, identifier: str, sid: str) -> tuple[Path, dict]:
        if not isinstance(sid, str) or not SID.fullmatch(sid):
            raise ValueError("Malformed selected shard identity")
        spec = self.spec(identifier)
        root = self.state / "objects" / sid
        vault._root(root)
        if any(p.is_symlink() for p in root.rglob("*")):
            raise ValueError("Symlink inside immutable shard store")
        if (
            sha(vault._read(Path(spec["public_key"]), 1344))
            != spec["trusted_key_sha256"]
        ):
            raise ValueError("Detached key changed from its registered fingerprint")
        verdict = verify_shard(root, Path(spec["public_key"]))
        if verdict.get("status") != "PASS":
            raise ValueError("Genesis verification rejected selected artifact " + sid)
        raw = (root / "manifest.json").read_bytes()
        if derive_shard_id(raw) != sid:
            raise ValueError("Object path differs from derived shard identity")
        return root, pack.loads(raw)

    def read_catalog(self, identifier: str, sid: str) -> dict:
        root, manifest = self._verify(identifier, sid)
        value = pack.loads(vault._read(root / "content/catalog.json", 16 * 1024 * 1024))
        spec = self.spec(identifier)
        if (
            value.get("schema") != CATALOG
            or value.get("vault_id") != identifier
            or manifest["publisher"]
            != {"id": spec["publisher_id"], "name": spec["publisher_name"]}
            or manifest["metadata"]["namespace"] != "axm/vault-catalog/" + identifier
            or type(value.get("generation")) is not int
            or value["generation"] < 1
            or manifest.get("supersedes", [])
            != ([value["previous"]] if value.get("previous") else [])
        ):
            raise ValueError("Signed collection metadata is inconsistent")
        if (
            not isinstance(value.get("entries"), list)
            or len(value["entries"]) > self.limits["notes"]
        ):
            raise ValueError("Collection inventory exceeds the admitted bound")
        names = set()
        for entry in value["entries"]:
            path = vault._path(entry["path"])
            if (
                path in names
                or not SID.fullmatch(entry["shard_id"])
                or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
            ):
                raise ValueError("Invalid or duplicate collection entry")
            names.add(path)
        approval = pack.loads((root / "content/approval.json").read_bytes())
        raw_plan = (root / "content/plan.json").read_bytes()
        if (
            sha(raw_plan) != approval["plan_digest"]
            or sha(canonical(approval)) != value["approval_digest"]
        ):
            raise ValueError("Collection approval binding is invalid")
        reviewed = pack.loads(raw_plan)
        approved_ids = approval.get("accepted", [])
        offered_ids = {
            candidate["id"]
            for file in reviewed["files"]
            for candidate in file["candidates"]
        }
        reviewed_inventory = {
            (n["path"], n["sha256"], n["bytes"]) for n in reviewed["notes"]
        }
        catalog_inventory = {
            (n["path"], n["sha256"], n["bytes"]) for n in value["entries"]
        }
        if (
            reviewed.get("schema") != PLAN
            or reviewed["id"] != approval["plan_id"]
            or reviewed["vault_id"] != identifier
            or reviewed["publisher"] != manifest["publisher"]
            or reviewed["base_head"] != value["previous"]
            or reviewed["base_generation"] + 1 != value["generation"]
            or reviewed["created_at"] != value["created_at"]
            or reviewed["trusted_key_sha256"] != spec["trusted_key_sha256"]
            or reviewed_inventory != catalog_inventory
            or not isinstance(approved_ids, list)
            or approved_ids != sorted(set(approved_ids))
            or not set(approved_ids) <= offered_ids
        ):
            raise ValueError("Signed catalog differs from the exact approved review")
        retained_catalog = (root / "content/catalog.json").read_text(encoding="utf-8")
        if (
            normalize_source_text(retained_catalog).encode("utf-8")
            != (root / "content/source.txt").read_bytes()
        ):
            raise ValueError("Catalog evidence differs from its retained original")
        value = dict(value)
        value["head"] = sid
        return value

    def head(self, identifier: str) -> dict | None:
        self.spec(identifier)
        with self.db() as con:
            row = con.execute(
                "SELECT * FROM heads WHERE vault_id=?", (identifier,)
            ).fetchone()
        if row is None:
            return None
        value = self.read_catalog(identifier, row["head"])
        if value["generation"] != row["generation"]:
            raise ValueError("Local head locator disagrees with its signed generation")
        return value

    def history(self, identifier: str) -> dict:
        current = self.head(identifier)
        editions, seen = [], set()
        while current:
            if current["head"] in seen or len(seen) >= 10000:
                raise ValueError("Invalid or excessive catalog history")
            seen.add(current["head"])
            editions.append(
                {
                    "head": current["head"],
                    "generation": current["generation"],
                    "note_count": len(current["entries"]),
                    "created_at": current["created_at"],
                    "previous": current["previous"],
                }
            )
            previous = current["previous"]
            if previous:
                parent = self.read_catalog(identifier, previous)
                if parent["generation"] + 1 != current["generation"]:
                    raise ValueError("Signed collection history is not contiguous")
                current = parent
            else:
                if current["generation"] != 1:
                    raise ValueError("Signed collection history is incomplete")
                current = None
        return {"editions": editions}

    def state_json(self):
        result = []
        for spec in self.config["vaults"]:
            try:
                head = self.head(spec["id"])
                result.append(
                    {
                        "id": spec["id"],
                        "label": spec["label"],
                        "path": spec["path"],
                        "head": head["head"] if head else None,
                        "generation": head["generation"] if head else 0,
                        "notes": len(head["entries"]) if head else 0,
                        "claims": sum(e.get("claims", 0) for e in head["entries"])
                        if head
                        else 0,
                    }
                )
            except (ValueError, OSError) as exc:
                result.append(
                    {
                        "id": spec["id"],
                        "label": spec["label"],
                        "path": spec["path"],
                        "head": None,
                        "generation": 0,
                        "notes": 0,
                        "claims": 0,
                        "error": str(exc),
                    }
                )
        with self.db() as con:
            plans = [
                {**json.loads(r["summary"]), "status": r["status"]}
                for r in con.execute("SELECT * FROM plans ORDER BY rowid DESC LIMIT 40")
            ]
            jobs = [
                dict(r)
                for r in con.execute("SELECT * FROM jobs ORDER BY rowid DESC LIMIT 30")
            ]
        return {
            "version": VERSION,
            "provider": self.extractor.describe(),
            "vaults": result,
            "plans": plans,
            "jobs": jobs,
            "limits": self.limits,
            "authority": AUTHORITY,
        }

    def _scan(self, identifier):
        return vault.scan_vault(
            self.spec(identifier)["path"], limits=self.limits, managed_dirs=(VIEWS,)
        )

    def plan(
        self, identifier: str, *, extract=False, force_extract=False, cancel=None
    ) -> dict:
        spec = self.spec(identifier)
        if type(extract) is not bool or type(force_extract) is not bool:
            raise ValueError("Extraction selection must be explicit booleans")
        if extract and not self.extractor.enabled:
            raise ValueError(
                "Choose an explicitly configured extraction provider before enabling extraction"
            )
        checkpoint(cancel)
        previous = self.head(identifier)
        old = {e["path"]: e for e in previous["entries"]} if previous else {}
        notes, blobs, selection = self._scan(identifier)
        present_paths = {n["path"] for n in notes}
        removed = set(old) - present_paths
        renamed = {}
        for note in notes:
            if note["path"] in old:
                continue
            matches = [p for p in removed if old[p]["sha256"] == note["sha256"]]
            additions = [
                n
                for n in notes
                if n["path"] not in old and n["sha256"] == note["sha256"]
            ]
            if len(matches) == len(additions) == 1:
                renamed[note["path"]] = matches[0]
        identifier_plan = "r_" + uuid.uuid4().hex
        files, model_calls, cache_hits = [], 0, 0
        staged = {}
        for note in notes:
            checkpoint(cancel)
            before = old.get(renamed.get(note["path"], note["path"]))
            same = (
                before
                and before["sha256"] == note["sha256"]
                and note["path"] not in renamed
            )
            enrich = extract and (
                force_extract
                or not before
                or before.get("extractor") != self.extractor.fingerprint
            )
            if same and not enrich:
                continue
            source_raw = blobs[note["blob"]]
            staged[note["blob"]] = source_raw
            source = normalize_source_text(source_raw.decode("utf-8"))
            proposals = deterministic_metadata(source, note["path"])
            failures, extracted = [], None
            if extract:
                cache_id = sha(
                    canonical(
                        {
                            "source": sha(source.encode()),
                            "path": note["path"],
                            "provider": self.extractor.fingerprint,
                        }
                    )
                )
                cache_path = self.state / "cache" / (cache_id + ".json")
                try:
                    if cache_path.exists() and not force_extract:
                        cached = pack.loads(vault._read(cache_path, 2 * 1024 * 1024))
                        extracted = validate_proposals(
                            source, cached["raw_response"], origin=cached["origin"]
                        )
                        extracted["receipt"] = cached.get("receipt")
                        cache_hits += 1
                    else:
                        if model_calls >= self.max_model_calls:
                            raise ValueError(
                                "Configured per-scan model-call budget exhausted; remaining sources are captured without semantic proposals"
                            )
                        model_calls += 1
                        extracted = self.extractor.extract(source, note["path"])
                        origin = extracted.get(
                            "origin",
                            self.extractor.kind
                            + "/"
                            + self.extractor.config.get("model", "operator-command"),
                        )
                        atomic_json(
                            cache_path,
                            {
                                "raw_response": extracted["raw_response"],
                                "origin": origin,
                                "receipt": extracted.get("receipt"),
                            },
                        )
                    proposals.extend(extracted["candidates"])
                    failures.extend(extracted["failures"])
                except (
                    ValueError,
                    RuntimeError,
                    OSError,
                    KeyError,
                    subprocess_error(),
                ) as exc:
                    failures.append(
                        {"reason": type(exc).__name__ + ": " + str(exc)[:500]}
                    )
            bound = []
            for proposal in proposals:
                body = {k: v for k, v in proposal.items() if k != "id"}
                proposal_id = "p_" + sha(
                    canonical(
                        {
                            "vault": identifier,
                            "path": note["path"],
                            "source": note["sha256"],
                            "proposal": body,
                        }
                    )
                )
                bound.append({"id": proposal_id, **body})
            entry = {
                **note,
                "action": "renamed"
                if note["path"] in renamed
                else "enriched"
                if same
                else "modified"
                if before
                else "added",
                "renamed_from": renamed.get(note["path"]),
                "previous": before["shard_id"] if before else None,
                "candidates": bound,
                "failures": failures,
                "extractor": self.extractor.fingerprint
                if extract and extracted is not None
                else None,
            }
            if extracted is not None:
                response_name = "responses/" + sha(note["path"].encode()) + ".json"
                staged[response_name] = extracted["raw_response"].encode("utf-8")
                entry["response"] = response_name
                entry["response_sha256"] = sha(staged[response_name])
                entry["extraction_receipt"] = extracted.get("receipt")
            files.append(entry)
        present = {n["path"] for n in notes}
        plan = {
            "schema": PLAN,
            "producer_version": VERSION,
            "id": identifier_plan,
            "vault_id": identifier,
            "created_at": now(),
            "base_head": previous["head"] if previous else None,
            "base_generation": previous["generation"] if previous else 0,
            "trusted_key_sha256": sha(vault._read(Path(spec["public_key"]), 1344)),
            "publisher": {"id": spec["publisher_id"], "name": spec["publisher_name"]},
            "notes": notes,
            "selection": selection,
            "files": files,
            "deleted_paths": sorted(set(old) - present),
            "changed_notes": len(files),
            "unchanged_notes": len(notes) - len(files),
            "model_calls": model_calls,
            "cache_hits": cache_hits,
            "authority": AUTHORITY,
        }
        raw = json_bytes(plan)
        checkpoint(cancel)
        vault._publish(
            {**staged, "plan.json": raw}, self.state / "plans" / identifier_plan
        )
        summary = {
            k: plan[k]
            for k in (
                "id",
                "vault_id",
                "changed_notes",
                "unchanged_notes",
                "model_calls",
            )
        }
        with self.db() as con:
            con.execute(
                "INSERT INTO plans VALUES(?,?,?,?,?,NULL)",
                (identifier_plan, identifier, "draft", sha(raw), json.dumps(summary)),
            )
        self.event("plan_created", {**summary, "digest": sha(raw)})
        return {**plan, "digest": sha(raw), "status": "draft"}

    def read_plan(self, identifier: str) -> dict:
        if not isinstance(identifier, str) or not PLAN_ID.fullmatch(identifier):
            raise ValueError("Invalid review identity")
        with self.db() as con:
            record = con.execute(
                "SELECT * FROM plans WHERE id=?", (identifier,)
            ).fetchone()
        if record is None:
            raise ValueError("Unknown review")
        raw = vault._read(
            self.state / "plans" / identifier / "plan.json", 64 * 1024 * 1024
        )
        if sha(raw) != record["digest"]:
            raise ValueError("Review plan changed on disk; capture a fresh review")
        value = pack.loads(raw)
        if (
            value.get("schema") != PLAN
            or value.get("id") != identifier
            or value["vault_id"] != record["vault_id"]
        ):
            raise ValueError("Review record does not match the durable plan")
        return {
            **value,
            "digest": record["digest"],
            "status": record["status"],
            "result": json.loads(record["result"]) if record["result"] else None,
        }

    def source_review(self, identifier: str, path: str) -> dict:
        plan = self.read_plan(identifier)
        selected = [f for f in plan["files"] if f["path"] == path]
        if len(selected) != 1:
            raise ValueError("Source path is not a changed note in this review")
        file = selected[0]
        raw = vault._read(
            self.state / "plans" / identifier / file["blob"], self.limits["note_bytes"]
        )
        if sha(raw) != file["sha256"]:
            raise ValueError("Staged source changed after capture")
        before = b""
        if file.get("previous"):
            previous, _ = self._verify(plan["vault_id"], file["previous"])
            before = vault._read(
                previous / "content/original.md", self.limits["note_bytes"]
            )
        original, proposed = before.decode("utf-8"), raw.decode("utf-8")
        diff = "".join(
            difflib.unified_diff(
                original.splitlines(True),
                proposed.splitlines(True),
                fromfile=file.get("renamed_from") or path,
                tofile=path,
            )
        )
        return {
            "path": path,
            "sha256": file["sha256"],
            "original": original,
            "proposed": proposed,
            "diff": diff[:262144],
            "diff_truncated": len(diff) > 262144,
            "scope": "Byte-exact staged original decoded as UTF-8; canonical claim spans address normalized source text.",
        }

    def reject(self, identifier: str, digest: str):
        plan = self.read_plan(identifier)
        if digest != plan["digest"] or plan["status"] not in {"draft", "rejected"}:
            raise ValueError("Review changed or has already been committed")
        with self.db() as con:
            con.execute(
                "UPDATE plans SET status='rejected' WHERE id=? AND digest=? AND status='draft'",
                (identifier, digest),
            )
        return {"status": "rejected", "id": identifier}

    def _compile(
        self,
        spec: dict,
        source: str,
        candidates: list,
        extras: dict[str, bytes],
        private: bytes,
        created: str,
        namespace: str,
        title: str,
        previous: str | None,
    ) -> tuple[str, int]:
        with tempfile.TemporaryDirectory(prefix=".compile-", dir=self.state) as temp:
            root = Path(temp)
            (root / "source.txt").write_bytes(source.encode("utf-8"))
            (root / "candidates.jsonl").write_bytes(
                b"".join(canonical(c) + b"\n" for c in candidates)
            )
            extra = []
            for name, raw in extras.items():
                path = root / "retained" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
                extra.append((name, path))
            output = root / "sealed"
            cfg = CompilerConfig(
                source_path=root / "source.txt",
                candidates_path=root / "candidates.jsonl",
                out_dir=output,
                private_key=private,
                publisher_id=spec["publisher_id"],
                publisher_name=spec["publisher_name"],
                namespace=namespace,
                title=title,
                created_at=created,
                supersedes=(previous,) if previous else (),
                lineage_action="amend",
                lineage_note="Digest-approved incremental source revision.",
                extra_content=tuple(extra),
            )
            with contextlib.redirect_stdout(io.StringIO()):
                if not compile_generic_shard(cfg):
                    raise ValueError(
                        "Genesis compilation failed: "
                        + str(verify_shard(output, Path(spec["public_key"])))
                    )
            if verify_shard(output, Path(spec["public_key"])).get("status") != "PASS":
                raise ValueError(
                    "Signing identity does not match the independent trust anchor"
                )
            sid = derive_shard_id((output / "manifest.json").read_bytes())
            count = len(pack.rows((output / "graph/claims.jsonl").read_bytes()))
            if count != len(
                {
                    (c["subject"], c["predicate"], c["object"], c["object_type"])
                    for c in candidates
                }
            ):
                raise ValueError("Compiler omitted a reviewed assertion")
            data = {
                p.relative_to(output).as_posix(): p.read_bytes()
                for p in output.rglob("*")
                if p.is_file()
            }
            vault._publish(data, self.state / "objects" / sid)
            return sid, count

    def approve(
        self, identifier: str, digest: str, accepted: list[str], cancel=None
    ) -> dict:
        plan = self.read_plan(identifier)
        if (
            digest != plan["digest"]
            or not isinstance(accepted, list)
            or len(accepted) > 100000
            or any(not isinstance(x, str) for x in accepted)
            or len(set(accepted)) != len(accepted)
        ):
            raise ValueError(
                "Approval must name the exact plan and a unique candidate selection"
            )
        all_ids = {c["id"] for f in plan["files"] for c in f["candidates"]}
        if not set(accepted) <= all_ids:
            raise ValueError("Approval contains a candidate outside this review")
        approval = {
            "plan_id": identifier,
            "plan_digest": digest,
            "accepted": sorted(accepted),
            "authority": "Explicit operator selection; semantic correctness is not established by a signature.",
        }
        approval_digest = sha(canonical(approval))
        if plan["status"] == "accepted":
            if plan["result"]["approval_digest"] != approval_digest:
                raise ValueError("This review already committed a different selection")
            return {**plan["result"], "idempotent": True}
        if plan["status"] != "draft":
            raise ValueError("Only a draft review can be committed")
        spec = self.spec(plan["vault_id"])
        with process_lock(self.state / "locks" / (spec["id"] + ".lock")):
            checkpoint(cancel)
            current = self.head(spec["id"])
            if (current["head"] if current else None) != plan["base_head"]:
                raise ValueError(
                    "Accepted head changed after review; rescan before approving"
                )
            notes, blobs, selection = self._scan(spec["id"])
            if notes != plan["notes"] or selection != plan["selection"]:
                raise ValueError(
                    "Working source changed after review; rescan before approving"
                )
            if (
                sha(vault._read(Path(spec["public_key"]), 1344))
                != plan["trusted_key_sha256"]
            ):
                raise ValueError("Detached trust anchor changed after review")
            if {"id": spec["publisher_id"], "name": spec["publisher_name"]} != plan[
                "publisher"
            ]:
                raise ValueError("Publisher identity changed after review")
            staged = self.state / "plans" / identifier
            for file in plan["files"]:
                if (
                    vault._read(staged / file["blob"], self.limits["note_bytes"])
                    != blobs[file["blob"]]
                ):
                    raise ValueError("Staged original changed after review")
                if (
                    file.get("response")
                    and sha(vault._read(staged / file["response"], 1024 * 1024))
                    != file["response_sha256"]
                ):
                    raise ValueError("Recorded model response changed after review")
            if (
                current
                and not plan["files"]
                and not plan["deleted_paths"]
                and selection == current["selection"]
            ):
                result = {
                    "status": "UNCHANGED",
                    "head": current["head"],
                    "generation": current["generation"],
                    "compiled_notes": 0,
                    "compiled_catalogs": 0,
                    "reused_notes": len(notes),
                    "approval_digest": approval_digest,
                    "new_edition": False,
                }
            else:
                if not spec["private_key"]:
                    raise ValueError(
                        "This vault is read-only; configure its signing key outside the source vault"
                    )
                private = vault._read(Path(spec["private_key"]), 3904)
                old = {e["path"]: e for e in current["entries"]} if current else {}
                next_entries = {
                    p: e for p, e in old.items() if p not in plan["deleted_paths"]
                }
                compiled = 0
                for file in plan["files"]:
                    checkpoint(cancel)
                    raw = blobs[file["blob"]]
                    source = normalize_source_text(raw.decode("utf-8"))
                    if source.strip():
                        capture = {
                            "subject": file["path"],
                            "predicate": "records_note",
                            "object": source.strip(),
                            "object_type": "literal:string",
                            "tier": 1,
                            "evidence": source.strip(),
                        }
                    else:
                        source = (
                            "Empty note "
                            + json.dumps(file["path"])
                            + " SHA256 "
                            + file["sha256"]
                            + "\n"
                        )
                        capture = {
                            "subject": file["path"],
                            "predicate": "records_empty_source",
                            "object": file["sha256"],
                            "object_type": "literal:string",
                            "tier": 1,
                            "evidence": source.strip(),
                        }
                    candidates = [capture]
                    for proposal in file["candidates"]:
                        if proposal["id"] not in accepted:
                            continue
                        quote = proposal["evidence"]
                        encoded = source.encode("utf-8")
                        if (
                            proposal["tier"] not in (0, 2)
                            or source.count(quote) != 1
                            or encoded[
                                proposal["byte_start"] : proposal["byte_end"]
                            ].decode()
                            != quote
                        ):
                            raise ValueError(
                                "Reviewed candidate lost its exact source binding"
                            )
                        c = {
                            k: proposal[k]
                            for k in (
                                "subject",
                                "predicate",
                                "object",
                                "object_type",
                                "tier",
                                "evidence",
                            )
                        }
                        c["locator"] = {"kind": "text", "file_path": file["path"]}
                        candidates.append(c)
                    metadata = {
                        "schema": "axm-vault-note/1",
                        "vault_id": spec["id"],
                        "path": file["path"],
                        "raw_sha256": file["sha256"],
                        "title": file["title"],
                        "approval_digest": approval_digest,
                        "coordinate_system": "Normalized UTF-8 content/source.txt; byte-exact original separately retained.",
                        "extractor": file["extractor"],
                    }
                    extras = {
                        "original.md": raw,
                        "note.json": json_bytes(metadata),
                        "approval.json": json_bytes(
                            {
                                "plan_id": identifier,
                                "plan_digest": digest,
                                "selection_digest": approval_digest,
                                "accepted_for_note": [
                                    c["id"]
                                    for c in file["candidates"]
                                    if c["id"] in accepted
                                ],
                            }
                        ),
                    }
                    if file.get("response"):
                        extras["model-response.json"] = vault._read(
                            staged / file["response"], 1024 * 1024
                        )
                    if file.get("extraction_receipt"):
                        extras["extraction-receipt.json"] = json_bytes(
                            file["extraction_receipt"]
                        )
                    sid, count = self._compile(
                        spec,
                        source,
                        candidates,
                        extras,
                        private,
                        plan["created_at"],
                        "axm/vault/" + spec["id"],
                        file["title"][:180],
                        file["previous"],
                    )
                    next_entries[file["path"]] = {
                        "path": file["path"],
                        "sha256": file["sha256"],
                        "bytes": len(raw),
                        "title": file["title"],
                        "shard_id": sid,
                        "claims": count,
                        "extractor": file["extractor"],
                    }
                    compiled += 1
                checkpoint(cancel)
                catalog = {
                    "schema": CATALOG,
                    "vault_id": spec["id"],
                    "generation": plan["base_generation"] + 1,
                    "previous": plan["base_head"],
                    "created_at": plan["created_at"],
                    "entries": sorted(next_entries.values(), key=lambda e: e["path"]),
                    "selection": selection,
                    "deleted_paths": plan["deleted_paths"],
                    "approval_digest": approval_digest,
                    "authority": AUTHORITY,
                }
                catalog_raw = json_bytes(catalog)
                source = normalize_source_text(catalog_raw.decode())
                candidate = {
                    "subject": spec["id"],
                    "predicate": "records_collection",
                    "object": source.strip(),
                    "object_type": "literal:string",
                    "tier": 0,
                    "evidence": source.strip(),
                }
                sid, _ = self._compile(
                    spec,
                    source,
                    [candidate],
                    {
                        "catalog.json": catalog_raw,
                        "approval.json": json_bytes(approval),
                        "plan.json": vault._read(
                            staged / "plan.json", 64 * 1024 * 1024
                        ),
                    },
                    private,
                    plan["created_at"],
                    "axm/vault-catalog/" + spec["id"],
                    "Vault collection " + spec["id"],
                    plan["base_head"],
                )
                self.read_catalog(spec["id"], sid)
                # Recheck the live capture before the sole authority-pointer mutation.
                live, _, live_selection = self._scan(spec["id"])
                if live != notes or live_selection != selection:
                    raise ValueError(
                        "Source changed during sealing; immutable candidates retained, accepted head unchanged"
                    )
                checkpoint(cancel)
                result = {
                    "status": "ACCEPTED",
                    "head": sid,
                    "generation": catalog["generation"],
                    "compiled_notes": compiled,
                    "compiled_catalogs": 1,
                    "reused_notes": len(notes) - compiled,
                    "notes": len(notes),
                    "claims": sum(e["claims"] for e in catalog["entries"]),
                    "approval_digest": approval_digest,
                    "new_edition": True,
                }
            with self.db() as con:
                con.execute("BEGIN IMMEDIATE")
                live = con.execute(
                    "SELECT head FROM heads WHERE vault_id=?", (spec["id"],)
                ).fetchone()
                if (live[0] if live else None) != plan["base_head"]:
                    raise ValueError(
                        "Concurrent head update rejected; immutable candidates retained"
                    )
                con.execute(
                    "INSERT INTO heads VALUES(?,?,?) ON CONFLICT(vault_id) DO UPDATE SET head=excluded.head,generation=excluded.generation",
                    (spec["id"], result["head"], result["generation"]),
                )
                con.execute(
                    "UPDATE plans SET status='accepted',result=? WHERE id=? AND digest=? AND status='draft'",
                    (json.dumps(result), identifier, digest),
                )
                con.execute(
                    "INSERT INTO events(at,kind,body) VALUES(?,?,?)",
                    (now(), "approved", json.dumps(result)),
                )
            return result

    def selected_records(self, scope="current", identifier="") -> list[dict]:
        if scope not in {"current", "history"}:
            raise ValueError("Scope must be current or history")
        specs = [self.spec(identifier)] if identifier else self.config["vaults"]
        records = []
        for spec in specs:
            current = self.head(spec["id"])
            if not current:
                continue
            active = {e["shard_id"] for e in current["entries"]}
            selected = current["entries"] if scope == "current" else []
            if scope == "history":
                seen = set(active)
                for edition in self.history(spec["id"])["editions"][1:]:
                    previous = self.read_catalog(spec["id"], edition["head"])
                    for entry in previous["entries"]:
                        if entry["shard_id"] not in seen:
                            selected.append(entry)
                            seen.add(entry["shard_id"])
            records.extend(
                {
                    **entry,
                    "vault_id": spec["id"],
                    "root": str(self.state / "objects" / entry["shard_id"]),
                    "trusted_key": spec["public_key"],
                }
                for entry in selected
            )
        return records

    def _index(self):
        if self.index is None:
            from axiom_runtime.vault_index import VaultIndex

            service_key = self.state / "query" / "service.key"
            if not service_key.exists():
                vault._new_file(service_key, secrets.token_hex(32).encode())
                service_key.chmod(0o600)
            # This key protects Spectra's local catalog, never publisher authority.
            os.environ.setdefault(
                "SPECTRA_SYSTEM_KEY", vault._read(service_key, 128).decode()
            )
            self.index = VaultIndex(self.state / "query")
        return self.index

    def search(self, query: str, scope="current", identifier="", limit=30):
        with self.index_lock:
            return self._index().search(
                self.selected_records(scope, identifier), query, limit
            )

    def context(self, query: str, scope="current", identifier="", budget=2000):
        if type(budget) is not int or not 256 <= budget <= 16000:
            raise ValueError("Estimated context budget must be 256..16000")
        result = self.search(query, scope, identifier, 50)
        Memory = _memory_class()
        with tempfile.TemporaryDirectory(prefix="axm-reviewed-context-") as temp:
            root = Path(temp)
            mapping = {}
            for number, hit in enumerate(result["hits"]):
                name = f"claim-{number:04}.md"
                mapping[name] = hit
                fields = {
                    "title": hit["subject"] + " " + hit["predicate"],
                    "summary": hit["object"][:1000],
                }
                content = (
                    _frontmatter(fields)
                    + _quote(hit["object"])
                    + "\nExact evidence:\n\n"
                    + _quote(hit["evidence"])
                )
                (root / name).write_text(content, encoding="utf-8")
            text = (
                Memory(str(root), create=False).context(
                    query, budget=budget, recent=False
                )
                if mapping
                else ""
            )
            paths = re.findall(r"^Source: `([^`\r\n]+)`\r?$", text, re.MULTILINE)
            refs = [mapping[p] for p in dict.fromkeys(paths) if p in mapping]
            return {
                "context": text,
                "references": refs,
                "estimated_tokens": (len(text) + 3) // 4,
                "budget": budget,
                "budget_method": "upstream character estimate; reference metadata is additional",
                "authority": AUTHORITY,
            }

    def conflicts(self, identifier=""):
        with self.index_lock:
            return self._index().competing_assertions(
                self.selected_records("current", identifier)
            )

    def graph(self, identifier: str, limit=200):
        # Link syntax is handled by upstream; resolution remains vault-local and
        # rejects ambiguous aliases rather than inventing graph edges.
        from obsidian_wiki import graphrag

        records = self.selected_records("current", identifier)
        truncated = len(records) > limit
        selected = records[:limit]
        names = {r["path"]: r for r in records}
        nodes = [{"id": r["path"], "label": r["title"]} for r in selected]
        edges, unresolved = [], []
        pattern = graphrag._WIKILINK_RE  # qualified pinned upstream link syntax
        for record in selected:
            root, _ = self._verify(identifier, record["shard_id"])
            text = (root / "content/original.md").read_text(encoding="utf-8")
            for match in pattern.finditer(text):
                target = match.group(1).split("|", 1)[0].split("#", 1)[0].strip()
                if not target:
                    continue
                possible = [
                    p
                    for p in names
                    if p.removesuffix(".md") == target or Path(p).stem == target
                ]
                if len(possible) == 1 and possible[0] in {n["id"] for n in nodes}:
                    edge = {
                        "source": record["path"],
                        "target": possible[0],
                        "label": "wikilink in retained source",
                    }
                    if edge not in edges:
                        edges.append(edge)
                else:
                    unresolved.append(
                        {
                            "source": record["path"],
                            "target": target,
                            "matches": len(possible),
                        }
                    )
        return {
            "nodes": nodes,
            "edges": edges,
            "unresolved": unresolved,
            "truncated": truncated,
            "meaning": "Derived source-link graph; links are not independent semantic proof.",
        }

    def refresh(self, identifier: str, cancel=None):
        spec = self.spec(identifier)
        current = self.head(identifier)
        if not current:
            raise ValueError("Approve a first edition before refreshing native views")
        root = vault._root(spec["path"])
        owned = root / VIEWS
        owner = {
            "schema": VIEW_SCHEMA,
            "vault_id": identifier,
            "trusted_key_sha256": sha(vault._read(Path(spec["public_key"]), 1344)),
        }
        if not owned.exists():
            vault._publish({"owner.json": json_bytes(owner)}, owned)
        if pack.loads(vault._read(owned / "owner.json", 4096)) != owner:
            raise ValueError("Existing native-view workspace belongs to another owner")
        with process_lock(self.state / "locks" / (identifier + "-views.lock")):
            for name in ("editions", "updates"):
                (owned / name).mkdir(exist_ok=True)
                vault._root(owned / name)
            files, nodes, edges = {}, [], []
            prefix = VIEWS + "/editions/" + current["head"]
            links = []
            for number, entry in enumerate(current["entries"]):
                checkpoint(cancel)
                shard, _ = self._verify(identifier, entry["shard_id"])
                name = "note-" + sha(entry["path"].encode())[:24] + ".md"
                claims = pack.rows((shard / "graph/claims.jsonl").read_bytes())
                entities = {
                    e["entity_id"]: e["label"]
                    for e in pack.rows((shard / "graph/entities.jsonl").read_bytes())
                }
                fields = {
                    "title": entry["title"],
                    "summary": entry["path"],
                    "axm_shard_id": entry["shard_id"],
                    "axm_generation": current["generation"],
                    "tags": ["axm-workbench"],
                }
                text = (
                    _frontmatter(fields)
                    + "# "
                    + _escape(entry["title"])
                    + "\n\n"
                    + AUTHORITY
                    + "\n\n"
                )
                text += "Original path: " + _escape(entry["path"]) + "\n\n"
                text += "Original SHA-256: " + entry["sha256"] + "\n\n"
                for claim in claims:
                    text += (
                        "## "
                        + _escape(claim["predicate"])
                        + "\n\n"
                        + _quote(entities.get(claim["object"], claim["object"]))
                    )
                    text += (
                        "Claim: "
                        + claim["claim_id"]
                        + "; extraction tier: "
                        + str(claim["tier"])
                        + "\n\n"
                    )
                files[name] = text.encode("utf-8")
                links.append(
                    f"[[{prefix}/{name.removesuffix('.md')}|{entry['shard_id']}]]"
                )
                if number < 200:
                    nodes.append(
                        {
                            "id": entry["shard_id"],
                            "type": "file",
                            "file": prefix + "/" + name,
                            "x": (number % 4) * 420,
                            "y": (number // 4) * 240,
                            "width": 380,
                            "height": 180,
                        }
                    )
            files["index.md"] = (
                f"# {identifier}: accepted edition {current['generation']}\n\n"
                + AUTHORITY
                + "\n\n"
                + "\n".join(links)
                + "\n"
            ).encode()
            files["Knowledge.canvas"] = json_bytes({"nodes": nodes, "edges": edges})
            files["Evidence.base"] = json_bytes(
                {
                    "filters": {
                        "and": [
                            f'file.inFolder("{prefix}")',
                            'file.hasTag("axm-workbench")',
                        ]
                    },
                    "views": [
                        {
                            "type": "table",
                            "name": "Accepted source records",
                            "order": ["file.name", "note.title", "note.axm_shard_id"],
                        }
                    ],
                }
            )
            target = owned / "editions" / current["head"]
            preserved = []
            if target.is_symlink():
                raise ValueError("Native projection directory symlink refused")
            if target.exists():
                preserved = vault._dirty(target, files)
            else:
                vault._publish(files, target)
            update = (
                f"---\ntitle: {identifier} edition {current['generation']}\ngeneration: {current['generation']}\n---\n\n"
                f"[[{prefix}/index|Accepted sources]]\n\n[[{prefix}/Evidence.base|Evidence table]]\n\n[[{prefix}/Knowledge.canvas|Source map]]\n"
            ).encode()
            update_path = owned / "updates" / (current["head"] + ".md")
            if not update_path.exists():
                vault._new_file(update_path, update)
            base = owned / "Editions.base"
            if not base.exists():
                vault._new_file(
                    base,
                    json_bytes(
                        {
                            "filters": {"and": [f'file.inFolder("{VIEWS}/updates")']},
                            "views": [
                                {
                                    "type": "table",
                                    "name": "Accepted editions",
                                    "order": ["file.name", "note.generation"],
                                    "sort": [
                                        {
                                            "property": "note.generation",
                                            "direction": "DESC",
                                        }
                                    ],
                                }
                            ],
                        }
                    ),
                )
            return {
                "status": "VIEWS_REFRESHED",
                "head": current["head"],
                "notes": len(current["entries"]),
                "source_files_written": 0,
                "preserved_view_edits": preserved,
                "open_in_obsidian": str(base),
            }

    def close(self):
        if self.index:
            self.index.close()
            self.index = None


def subprocess_error():
    import subprocess

    return subprocess.SubprocessError
