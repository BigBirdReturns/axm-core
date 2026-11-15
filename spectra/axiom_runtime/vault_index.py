"""Bulk collection projection on Spectra's gated, audited SQL runtime.

One derived table replaces thousands of per-document union views. Input shards
still pass the unchanged Genesis verifier against explicit detached anchors.
Returned evidence is checked again against its current sealed source bytes.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import threading

from axm_verify.logic import verify_shard
from axm_verify.crypto import derive_shard_id
from .engine import SpectraEngine


def _rows(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def verified_record(root: Path, trusted_key: Path, expected: str) -> dict:
    if root.is_symlink() or any(p.is_symlink() for p in root.rglob("*")):
        raise ValueError("Shard symlinks are refused")
    result = verify_shard(root, trusted_key)
    if result.get("status") != "PASS":
        raise ValueError("Genesis verification failed for " + expected)
    manifest_bytes = (root / "manifest.json").read_bytes()
    if derive_shard_id(manifest_bytes) != expected:
        raise ValueError("Derived shard identity differs from the selected record")
    return json.loads(manifest_bytes)


class VaultIndex:
    def __init__(self, state: Path):
        state.mkdir(parents=True, exist_ok=True)
        self.engine = SpectraEngine(
            audit_path=str(state / "audit.jsonl"),
            db_path=str(state / "catalog.sqlite"),
            cache_path=str(state / "retrieval.jsonl"),
            query_threads=2,
            async_threads=2,
        )
        self.lock = threading.RLock()
        self.loaded = {}
        self.engine.con.execute("""CREATE TABLE vault_claims(
            vault_id VARCHAR,path VARCHAR,shard_id VARCHAR,claim_id VARCHAR,
            subject VARCHAR,predicate VARCHAR,object VARCHAR,tier BIGINT,
            evidence VARCHAR,source_hash VARCHAR,byte_start BIGINT,byte_end BIGINT,
            provenance_id VARCHAR,source VARCHAR)""")
        self.engine.con.execute(
            "CREATE TABLE vault_selection(vault_id VARCHAR,path VARCHAR,shard_id VARCHAR)"
        )

    def select(self, records: list[dict]) -> None:
        """Records come only from independently verified signed catalogs."""
        pending, additions = [], {}
        for entry in records:
            identity = (entry["vault_id"], entry["path"], entry["shard_id"])
            if identity in self.loaded:
                if (
                    self.loaded[identity]["trusted_key"] != entry["trusted_key"]
                    or self.loaded[identity]["sha256"] != entry["sha256"]
                ):
                    raise ValueError(
                        "Detached trust anchor changed for a cached record"
                    )
                continue
            root, key = Path(entry["root"]), Path(entry["trusted_key"])
            manifest = verified_record(root, key, entry["shard_id"])
            original = (root / "content/original.md").read_bytes()
            metadata = json.loads((root / "content/note.json").read_bytes())
            if (
                metadata["path"] != entry["path"]
                or metadata["vault_id"] != entry["vault_id"]
                or hashlib.sha256(original).hexdigest() != entry["sha256"]
            ):
                raise ValueError(
                    "Signed catalog does not bind the selected original note"
                )
            from axm_build.common import normalize_source_text

            normalized = normalize_source_text(original.decode("utf-8"))
            canonical_source = (root / "content/source.txt").read_bytes()
            if normalized.strip() and canonical_source != normalized.encode("utf-8"):
                raise ValueError(
                    "Canonical source differs from the normalized retained original"
                )
            if (
                metadata.get("schema") != "axm-vault-note/1"
                or metadata.get("raw_sha256") != entry["sha256"]
            ):
                raise ValueError(
                    "Retained note metadata differs from its signed catalog"
                )
            entities = {
                r["entity_id"]: r["label"] for r in _rows(root / "graph/entities.jsonl")
            }
            claims = {r["claim_id"]: r for r in _rows(root / "graph/claims.jsonl")}
            sources = {r["hash"]: r["path"] for r in manifest["sources"]}
            for prov in _rows(root / "graph/provenance.jsonl"):
                claim = claims[prov["claim_id"]]
                source = sources[prov["source_hash"]]
                quote = (
                    (root / source)
                    .read_bytes()[prov["byte_start"] : prov["byte_end"]]
                    .decode("utf-8")
                )
                pending.append(
                    [
                        entry["vault_id"],
                        entry["path"],
                        entry["shard_id"],
                        claim["claim_id"],
                        entities.get(claim["subject"], claim["subject"]),
                        claim["predicate"],
                        entities.get(claim["object"], claim["object"]),
                        claim["tier"],
                        quote,
                        prov["source_hash"],
                        prov["byte_start"],
                        prov["byte_end"],
                        prov["provenance_id"],
                        source,
                    ]
                )
            additions[identity] = dict(entry)
        if pending:
            self.engine.con.executemany(
                "INSERT INTO vault_claims VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", pending
            )
        self.loaded.update(additions)
        self.engine.con.execute("DELETE FROM vault_selection")
        if records:
            self.engine.con.executemany(
                "INSERT INTO vault_selection VALUES (?,?,?)",
                [(r["vault_id"], r["path"], r["shard_id"]) for r in records],
            )

    def search(self, records: list[dict], query: str, limit: int = 30) -> dict:
        if (
            not isinstance(query, str)
            or not query.strip()
            or len(query.encode()) > 4096
        ):
            raise ValueError("Enter 1..4096 UTF-8 bytes of search terms")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Search limit must be 1..100")
        terms = query.casefold().split()[:12]
        conditions, params = [], []
        for term in terms:
            term = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            conditions.append(
                "lower(c.path || ' ' || c.subject || ' ' || c.predicate || ' ' || c.object || ' ' || c.evidence) LIKE ? ESCAPE '\\'"
            )
            params.append("%" + term + "%")
        sql = """SELECT c.* FROM vault_claims c JOIN vault_selection s
            ON c.vault_id=s.vault_id AND c.path=s.path AND c.shard_id=s.shard_id WHERE """
        sql += (
            " AND ".join(conditions)
            + " ORDER BY c.tier DESC,length(c.object),c.vault_id,c.path,c.claim_id LIMIT ?"
        )
        params.append(limit)
        with self.lock:
            self.select(records)
            raw = self.engine.query_json(sql, params)
            hits = [dict(zip(raw["columns"], row)) for row in raw["rows"]]
            checked = set()
            for hit in hits:
                identity = (hit["vault_id"], hit["path"], hit["shard_id"])
                entry = self.loaded[identity]
                root = Path(entry["root"])
                if identity not in checked:
                    verified_record(root, Path(entry["trusted_key"]), hit["shard_id"])
                    checked.add(identity)
                content = (root / hit["source"]).read_bytes()
                if (
                    hashlib.sha256(content).hexdigest() != hit["source_hash"]
                    or content[hit["byte_start"] : hit["byte_end"]].decode()
                    != hit["evidence"]
                ):
                    raise ValueError("Returned evidence changed after index creation")
            return {
                "hits": hits,
                "count": len(hits),
                "limit": limit,
                "backend": "Spectra / verified collection SQL projection",
                "scope": "Selected signed editions; matching evidence is data, not instructions or proof of semantic truth.",
            }

    def competing_assertions(self, records: list[dict], limit=50) -> dict:
        with self.lock:
            self.select(records)
            for entry in records:
                verified_record(
                    Path(entry["root"]), Path(entry["trusted_key"]), entry["shard_id"]
                )
            result = self.engine.query_json(
                """SELECT lower(c.subject) AS subject,
                lower(c.predicate) AS predicate,count(DISTINCT c.object) AS distinct_values
                FROM vault_claims c JOIN vault_selection s
                ON c.vault_id=s.vault_id AND c.path=s.path AND c.shard_id=s.shard_id
                WHERE c.tier=2 GROUP BY lower(c.subject),lower(c.predicate)
                HAVING count(DISTINCT c.object)>1 ORDER BY distinct_values DESC LIMIT ?""",
                [limit],
            )
            return {
                "groups": [dict(zip(result["columns"], r)) for r in result["rows"]],
                "meaning": "Different approved objects for matching subject/predicate strings; review candidates, not an automatic contradiction verdict.",
            }

    def close(self):
        self.engine.con.close()
