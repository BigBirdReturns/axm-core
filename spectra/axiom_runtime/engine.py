from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import duckdb

# In-process Genesis verification (preferred over shelling out)
try:
    from axm_verify.logic import verify_shard as genesis_verify_shard  # type: ignore
except Exception:
    genesis_verify_shard = None  # type: ignore

from .audit import AuditLogger
from .chat import ChatEngine
from .db import SystemCatalog
from .retrieval import Embedder, VectorIndex
from .sqlgate import is_read_only_sql
from .transport import TransportAdapter
from .util import choose_temp_root, quote_ident, sanitize_identifier, sha256_hex


@dataclass(frozen=True)
class MountSpec:
    mount_id: str
    shard_id: str
    merkle_root: str
    spec_version: str
    source_path: str
    transport: str
    tables: Tuple[str, ...]


class SpectraEngine:
    """Spectra Runtime Kernel.

    v0.3.1 (Strict):
    - Genesis is the only constitution.
    - Clarion is transport only.
    - Every mount runs axm-verify (unless explicit dev override).
    """

    _NAMESPACE = uuid.UUID("3b1c7a74-0f9a-4c61-9cc8-8f2a3f5e0f5f")

    def __init__(
        self,
        *,
        audit_path: Optional[str] = None,
        cache_path: Optional[str] = None,
        db_path: Optional[str] = None,
        temp_root: Optional[str] = None,
    ) -> None:
        self._start_time = time.time()
        self._lock = threading.RLock()
        self.con = duckdb.connect(":memory:")
        self._mount_dirs: Dict[str, Path] = {}
        self._mount_specs: Dict[str, MountSpec] = {}
        self._claims: Dict[str, List[Dict[str, Any]]] = {}

        raw_audit = audit_path or os.environ.get("SPECTRA_AUDIT_PATH", "spectra_audit.jsonl")
        raw_cache = cache_path or os.environ.get("SPECTRA_CACHE_PATH", "spectra_cache.jsonl")
        raw_db = db_path or os.environ.get("SPECTRA_DB_PATH", "spectra.db")

        pid = str(os.getpid())
        raw_audit = raw_audit.replace("{pid}", pid)
        raw_cache = raw_cache.replace("{pid}", pid)
        raw_db = raw_db.replace("{pid}", pid)

        self._audit_path = Path(raw_audit).expanduser().resolve(strict=False)
        self._cache_path = Path(raw_cache).expanduser().resolve(strict=False)
        self._db_path = Path(raw_db).expanduser().resolve(strict=False)

        self._audit = AuditLogger(str(self._audit_path))
        self.catalog = SystemCatalog(str(self._db_path))

        provider = os.environ.get("SPECTRA_EMBED_PROVIDER", "mock")
        model = os.environ.get("SPECTRA_EMBED_MODEL", "text-embedding-3-small")

        if os.environ.get("SPECTRA_CACHE_DEBUG") == "1":
            print(f"[Engine Init] PID: {pid}", file=sys.stderr)
            print(f"[Engine Init] DB Path: {self._db_path}", file=sys.stderr)
            print(f"[Engine Init] Audit Path: {self._audit_path}", file=sys.stderr)
            print(f"[Engine Init] Cache Path: {self._cache_path}", file=sys.stderr)

        self._embedder = Embedder(
            cache_path=str(self._cache_path),
            provider=provider,
            model=model,
            base_url=os.environ.get("SPECTRA_EMBED_BASE_URL"),
        )
        self._index = VectorIndex(self._embedder)
        self._chat = ChatEngine(
            self._index,
            provider=os.environ.get("SPECTRA_CHAT_PROVIDER", "openai"),
            model=os.environ.get("SPECTRA_CHAT_MODEL", "gpt-4o-mini"),
            base_url=os.environ.get("SPECTRA_CHAT_BASE_URL"),
        )

        self._temp_root_override = temp_root

    def _verify_constitution(self, shard_dir: Path) -> None:
        """Enforces Genesis Standard conformance using axm-verify.

        THE HARD GATE: axm-verify MUST pass. No exceptions in production.
        """
        dev_mode = os.environ.get("SPECTRA_DEV_MODE") == "1"

        # Resolve trusted key once for both paths.
        trusted_key_env = os.environ.get("SPECTRA_TRUSTED_PUBKEY")
        if trusted_key_env:
            trusted = Path(trusted_key_env).expanduser().resolve(strict=False)
        else:
            # Local/dev fallback only. Real deployments must pin a trusted publisher key.
            trusted = shard_dir / "sig" / "publisher.pub"
            if not dev_mode:
                print(
                    "[WARNING] SPECTRA_TRUSTED_PUBKEY not set. Falling back to shard's publisher.pub as trusted anchor.",
                    file=sys.stderr,
                )

        # Prefer in-process verification (faster, no subprocess overhead).
        if genesis_verify_shard is not None:
            result = genesis_verify_shard(shard_dir, trusted)
            if result.get("status") != "PASS":
                raise ValueError(f"Constitution check failed (in-process verify): {result}")
            return

        # Fall back to CLI.
        try:
            cmd = ["axm-verify", "shard", str(shard_dir), "--trusted-key", str(trusted)]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
            )
            
            if result.returncode != 0:
                raise ValueError(f"Constitution check failed (axm-verify): {result.stderr or result.stdout}")
            return
                
        except FileNotFoundError:
            pass

        if dev_mode:
            # DEV MODE ONLY: Minimal layout check for local development.
            # This should NEVER be used in production.
            print("[WARNING] axm-verify not found, running in DEV MODE", file=sys.stderr)
            if not (shard_dir / "manifest.json").exists():
                raise ValueError("Constitution check failed: missing manifest.json")
            if not (shard_dir / "sig").exists():
                raise ValueError("Constitution check failed: missing sig/")
            return

        raise ValueError(
            "Constitution check failed: 'axm-verify' not found and in-process verifier unavailable.\n"
            "Install axm-genesis (pip install axm-genesis) or vendor genesis into Spectra."
        )

    def _verify_span_bounds(self, shard_dir: Path, manifest: Dict[str, Any]) -> None:
        """Verify that spans.parquet byte ranges stay within their referenced content files.

        Reject with PROVENANCE_OUT_OF_BOUNDS if any span exceeds document bounds.
        """
        sources = manifest.get("sources") or []
        hash_to_path: Dict[str, Path] = {}
        for s in sources:
            if not isinstance(s, dict):
                continue
            h = s.get("hash")
            rel = s.get("path")
            if isinstance(h, str) and isinstance(rel, str):
                hash_to_path[h] = (shard_dir / rel)

        spans_path = shard_dir / "evidence" / "spans.parquet"
        if not spans_path.exists():
            raise ValueError("PROVENANCE_OUT_OF_BOUNDS: missing evidence/spans.parquet")

        # Precompute file sizes.
        hash_to_size: Dict[str, int] = {}
        for h, fp in hash_to_path.items():
            if not fp.exists():
                raise ValueError(f"PROVENANCE_OUT_OF_BOUNDS: source file missing for hash {h}: {fp}")
            hash_to_size[h] = fp.stat().st_size

        # Read spans using DuckDB (no pyarrow dependency).
        con = duckdb.connect(":memory:")
        try:
            rows = con.execute(
                "SELECT source_hash, byte_start, byte_end FROM read_parquet(?)",
                [str(spans_path)],
            ).fetchall()
        finally:
            con.close()

        for source_hash, byte_start, byte_end in rows:
            if not isinstance(source_hash, str) or source_hash not in hash_to_size:
                raise ValueError(f"PROVENANCE_OUT_OF_BOUNDS: unknown source_hash in spans.parquet: {source_hash}")

            try:
                bs = int(byte_start)
                be = int(byte_end)
            except Exception:
                raise ValueError(
                    f"PROVENANCE_OUT_OF_BOUNDS: non-integer byte range for source_hash {source_hash}: "
                    f"{byte_start}..{byte_end}"
                )

            if bs < 0 or be < 0 or be < bs:
                raise ValueError(
                    f"PROVENANCE_OUT_OF_BOUNDS: invalid byte range for source_hash {source_hash}: {bs}..{be}"
                )

            size = hash_to_size[source_hash]
            if bs > size or be > size:
                raise ValueError(
                    f"PROVENANCE_OUT_OF_BOUNDS: span exceeds source bounds for source_hash {source_hash}: "
                    f"{bs}..{be} (size {size})"
                )

    def boot(self) -> Dict[str, Any]:
        """Rehydrate state from the System Catalog."""
        self.catalog.log_system_event("boot_start")
        active = self.catalog.get_active_mounts()
        results: Dict[str, Any] = {"attempted": len(active), "success": 0, "failed": 0, "details": []}

        print(f"[Boot] Rehydrating {len(active)} mounts from {self._db_path}...", file=sys.stderr)

        for row in active:
            mid = row.get("mount_id")
            try:
                cfg = row.get("mount_config") or {}
                transport = cfg.get("transport")
                if not transport:
                    # Back-compat: treat missing config as genesis.
                    transport = "genesis"

                secret_b64: Optional[str] = None
                if transport == "clarion":
                    if not row.get("enc_secret"):
                        raise ValueError("Missing secret for Clarion transport")
                    secret_b64 = self.catalog.decrypt_secret(row["enc_secret"])

                source_path = row.get("shard_path")
                if not source_path or not Path(source_path).exists():
                    raise ValueError(f"Shard path not found: {source_path}")

                self.mount_shard(
                    source_path,
                    secret_b64,
                    origin="boot",
                    forced_transport=transport,
                )

                results["success"] += 1
                results["details"].append({"mount_id": mid, "status": "ok"})
            except Exception as e:
                if mid:
                    self.catalog.set_mount_error(mid, str(e))
                self.catalog.log_system_event("boot_mount_fail", details={"mount_id": mid, "error": str(e)})
                results["failed"] += 1
                results["details"].append({"mount_id": mid, "status": "error", "msg": str(e)})
                print(f"[Boot] Failed to mount {mid}: {e}", file=sys.stderr)

        self.catalog.log_system_event("boot_complete", details=results)
        return results

    def health(self) -> Dict[str, Any]:
        ok, err = self.catalog.check_health()
        return {
            "status": "online" if ok else "degraded",
            "uptime_sec": int(time.time() - self._start_time),
            "catalog_connected": ok,
            "catalog_error": err,
            "active_mounts": len(self._mount_specs),
            "index_size": self.index_size(),
        }

    def index_size(self) -> int:
        with self._lock:
            return self._index.size()

    def mount_shard(
        self,
        path: str,
        secret_b64: Optional[str] = None,
        token_hash: Optional[str] = None,
        origin: str = "api",
        forced_transport: Optional[str] = None,
    ) -> MountSpec:
        start_ts = time.time()

        transport = forced_transport or TransportAdapter.detect_format(path)

        target_dir: Path
        temp_dir: Optional[Path] = None

        if transport == "clarion":
            if not secret_b64:
                raise ValueError("Secret required for Clarion transport")

            if self._temp_root_override:
                tr = str(Path(self._temp_root_override).expanduser().resolve(strict=False))
            else:
                tr_path, _ = choose_temp_root()
                tr = str(tr_path)

            target_dir = TransportAdapter.decrypt_envelope(path, secret_b64, temp_root=tr)
            temp_dir = target_dir
        elif transport == "genesis":
            target_dir = Path(path).expanduser().resolve(strict=False)
        else:
            raise ValueError(f"Unknown shard format: {transport}")

        try:
            # Constitution check is mandatory.
            self._verify_constitution(target_dir)

            manifest_path = target_dir / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

            spec_version = manifest.get("spec_version")
            if spec_version != "1.0.0":
                raise ValueError(f"Unsupported Genesis spec_version: {spec_version}")

            shard_id = manifest.get("shard_id")
            if not shard_id or not isinstance(shard_id, str):
                raise ValueError("Genesis manifest missing required field: shard_id")

            merkle_root = (manifest.get("integrity") or {}).get("merkle_root")
            if not merkle_root or not isinstance(merkle_root, str):
                raise ValueError("Genesis manifest missing required field: integrity.merkle_root")

            # Additional hard gate: provenance spans must stay within the bounds of their sources.
            self._verify_span_bounds(target_dir, manifest)

            mount_key = json.dumps({"shard_id": shard_id, "merkle_root": merkle_root}, sort_keys=True)
            mount_id = str(uuid.uuid5(self._NAMESPACE, mount_key))
            mount_prefix = mount_id.replace("-", "")[:12]

            with self._lock:
                if mount_id in self._mount_specs:
                    # Drop decrypted bytes from disk if we created them.
                    if temp_dir and temp_dir.exists():
                        shutil.rmtree(temp_dir)
                    return self._mount_specs[mount_id]

                # Load Parquet tables into DuckDB views.
                tables: List[str] = []
                claims_for_mount: List[Dict[str, Any]] = []

                # Register views for all standard shard tables.
                _SHARD_TABLES = [
                    ("graph/claims.parquet", "claims", True),
                    ("graph/entities.parquet", "entities", False),
                    ("graph/provenance.parquet", "provenance", False),
                    ("evidence/spans.parquet", "spans", False),
                ]

                for rel_path, table_name, required in _SHARD_TABLES:
                    pq_path = target_dir / rel_path
                    if not pq_path.exists():
                        if required:
                            raise ValueError(f"Genesis shard missing required file: {rel_path}")
                        continue
                    p = pq_path.as_posix().replace("'", "''")
                    view_name = f"{table_name}__{mount_prefix}__{sanitize_identifier(shard_id)}"
                    self.con.execute(
                        f"CREATE OR REPLACE VIEW {quote_ident(view_name)} AS SELECT * FROM read_parquet('{p}')"
                    )
                    tables.append(view_name)

                # Also register ext/ parquet files if present.
                ext_dir = target_dir / "ext"
                if ext_dir.is_dir():
                    for ext_file in sorted(ext_dir.iterdir()):
                        if ext_file.suffix == ".parquet" and ext_file.is_file():
                            p = ext_file.as_posix().replace("'", "''")
                            view_name = f"ext_{ext_file.stem}__{mount_prefix}__{sanitize_identifier(shard_id)}"
                            self.con.execute(
                                f"CREATE OR REPLACE VIEW {quote_ident(view_name)} AS SELECT * FROM read_parquet('{p}')"
                            )
                            tables.append(view_name)

                # For indexing, pull claim rows as dicts (bounded by shard size in practice).
                claims_view = f"claims__{mount_prefix}__{sanitize_identifier(shard_id)}"
                try:
                    rows = self.con.execute(f"SELECT * FROM {quote_ident(claims_view)}").fetchdf().to_dict("records")
                    for r in rows:
                        if isinstance(r, dict):
                            r.setdefault("shard_id", shard_id)
                            claims_for_mount.append(r)
                except Exception:
                    # Indexing is optional, SQL views remain valid.
                    pass

                spec = MountSpec(
                    mount_id=mount_id,
                    shard_id=shard_id,
                    merkle_root=merkle_root,
                    spec_version=spec_version,
                    source_path=str(path),
                    transport=transport,
                    tables=tuple(sorted(tables)),
                )

                if temp_dir:
                    self._mount_dirs[mount_id] = temp_dir

                self._mount_specs[mount_id] = spec
                self._claims[mount_id] = claims_for_mount

                # Persist to catalog.
                self.catalog.upsert_mount(
                    mount_id=mount_id,
                    doc_id=shard_id,
                    path=str(path),
                    secret=secret_b64,
                    topo_hash=merkle_root,
                    mount_config={"transport": transport},
                )

                if origin == "api":
                    self.catalog.log_system_event("mount_ok", details={"mount_id": mount_id, "transport": transport})

                self._audit.write_event(
                    {
                        "event": "mount_shard",
                        "token_hash": token_hash,
                        "mount_id": mount_id,
                        "shard_id": shard_id,
                        "transport": transport,
                        "tables_created": len(tables),
                        "latency_ms": int((time.time() - start_ts) * 1000),
                    }
                )

                return spec

        except Exception as e:
            if temp_dir and temp_dir.exists():
                shutil.rmtree(temp_dir)

            if origin == "api":
                self.catalog.log_system_event("mount_fail", details={"error": str(e), "path": path, "transport": transport})

            raise

    def mount(self, path: str, secret_b64: Optional[str], *, verify: bool = True, token_hash: Optional[str] = None) -> Dict[str, Any]:
        # verify flag remains for API compatibility. Constitution verification always runs.
        spec = self.mount_shard(path, secret_b64, token_hash=token_hash, origin="api")
        return {
            "status": "ok",
            "mount_id": spec.mount_id,
            "shard_id": spec.shard_id,
            "merkle_root": spec.merkle_root,
            "tables": list(spec.tables),
            "transport": spec.transport,
            "verify": {"status": "ok"} if verify else None,
        }

    def unmount(self, mount_id: str, token_hash: Optional[str] = None) -> None:
        with self._lock:
            spec = self._mount_specs.pop(mount_id, None)
            self._claims.pop(mount_id, None)
            if not spec:
                return

            for t in spec.tables:
                self.con.execute(f"DROP VIEW IF EXISTS {quote_ident(t)}")

            temp_dir = self._mount_dirs.pop(mount_id, None)
            if temp_dir and temp_dir.exists():
                shutil.rmtree(temp_dir)

            self.catalog.set_mount_stopped(mount_id)
            self.catalog.log_system_event("unmount", details={"mount_id": mount_id})
            self._audit.write_event({"event": "unmount", "token_hash": token_hash, "mount_id": mount_id})

    def catalog_json(self) -> Dict[str, Any]:
        with self._lock:
            mounts = []
            for s in sorted(self._mount_specs.values(), key=lambda m: (m.shard_id, m.mount_id)):
                mounts.append(
                    {
                        "mount_id": s.mount_id,
                        "shard_id": s.shard_id,
                        "merkle_root": s.merkle_root,
                        "spec_version": s.spec_version,
                        "transport": s.transport,
                        "tables": list(s.tables),
                    }
                )
            return {"mounts": mounts}

    def query_json(self, sql: str, *, token_hash: Optional[str] = None) -> Dict[str, Any]:
        start = time.perf_counter()
        if not is_read_only_sql(sql):
            raise ValueError("Query rejected. Read-only SQL only.")

        with self._lock:
            res = self.con.execute(sql)
            rows = res.fetchall()
            cols = [d[0] for d in (res.description or [])]

        elapsed_ms = int((time.perf_counter() - start) * 1000)
        self._audit.write_event(
            {
                "event": "sql_query",
                "token_hash": token_hash,
                "sql_hash": sha256_hex(sql)[:16],
                "row_count": len(rows),
                "elapsed_ms": elapsed_ms,
                "active_mounts": sorted(list(self._mount_specs.keys())),
            }
        )
        return {"columns": cols, "rows": rows}

    def index(self, mount_id: Optional[str] = None, token_hash: Optional[str] = None) -> Dict[str, Any]:
        start = time.time()
        with self._lock:
            targets = [mount_id] if mount_id else list(self._claims.keys())
            total_added = 0
            for mid in targets:
                claims = self._claims.get(mid, [])
                total_added += self._index.index_claims(claims)

        self._audit.write_event(
            {
                "event": "index_claims",
                "token_hash": token_hash,
                "targets": targets,
                "added_count": total_added,
                "latency_ms": int((time.time() - start) * 1000),
            }
        )
        return {"status": "ok", "indexed": total_added, "index_size": self._index.size()}

    def chat(self, question: str, top_k: int = 7, token_hash: Optional[str] = None) -> Dict[str, Any]:
        start = time.time()
        with self._lock:
            active_mounts = sorted(list(self._mount_specs.keys()))
            res = self._chat.ask(question, top_k=top_k)

        self._audit.write_event(
            {
                "event": "chat_query",
                "token_hash": token_hash,
                "active_mounts": active_mounts,
                "question_hash": sha256_hex(question)[:16],
                "citations_count": len(res.get("citations", [])),
                "latency_ms": int((time.time() - start) * 1000),
            }
        )
        return res
