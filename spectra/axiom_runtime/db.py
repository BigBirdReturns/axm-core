import base64
import json
import os
import sqlite3
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

# Cryptography check for Posture 2
try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    _HAS_CRYPTO = True
except ImportError:
    _HAS_CRYPTO = False

SCHEMA_V1 = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    role TEXT DEFAULT 'user',
    created_at REAL NOT NULL,
    is_active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS api_keys (
    key_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    key_hash TEXT NOT NULL,
    label TEXT,
    created_at REAL NOT NULL,
    expires_at REAL,
    last_used_at REAL,
    is_revoked INTEGER DEFAULT 0,
    FOREIGN KEY(user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS mounts (
    mount_id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL,
    shard_path TEXT NOT NULL,
    enc_secret BLOB,

    mount_config JSON,
    auto_mount INTEGER DEFAULT 1,

    expected_topology_hash TEXT,
    owner_user_id TEXT,

    status TEXT DEFAULT 'stopped',
    last_error TEXT,
    last_attempt_ts REAL,
    last_ok_ts REAL,
    retry_count INTEGER DEFAULT 0,

    created_at REAL NOT NULL,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS system_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    event_type TEXT NOT NULL,
    actor_id TEXT,
    details JSON
);
"""


class SystemVault:
    def __init__(self, db_conn: sqlite3.Connection):
        if not _HAS_CRYPTO:
            raise ImportError("Spectra requires 'cryptography'. Run: pip install cryptography")

        key_raw = os.environ.get("SPECTRA_SYSTEM_KEY")
        dev_mode = os.environ.get("SPECTRA_DEV_MODE") == "1"

        if not key_raw:
            if dev_mode:
                print("[WARN] SPECTRA_SYSTEM_KEY not set. Using UNSAFE dev key.", file=sys.stderr)
                key_raw = "spectra-dev-insecure-key-do-not-use-in-prod"
            else:
                print("[FATAL] SPECTRA_SYSTEM_KEY is required. Set it or use SPECTRA_DEV_MODE=1.", file=sys.stderr)
                sys.exit(1)

        salt_b64 = self._get_or_create_salt(db_conn)
        salt = base64.b64decode(salt_b64)

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=600000,
        )
        self._fernet = Fernet(base64.urlsafe_b64encode(kdf.derive(key_raw.encode())))

    def _get_or_create_salt(self, conn: sqlite3.Connection) -> str:
        cur = conn.execute("SELECT value FROM meta WHERE key='vault_salt'")
        row = cur.fetchone()
        if row:
            return row[0]

        salt = os.urandom(16)
        salt_b64 = base64.b64encode(salt).decode("ascii")
        conn.execute(
            "INSERT INTO meta (key, value, updated_at) VALUES (?, ?, ?)",
            ("vault_salt", salt_b64, time.time()),
        )
        conn.commit()
        return salt_b64

    def encrypt(self, secret: str) -> bytes:
        return self._fernet.encrypt(secret.encode("utf-8"))

    def decrypt(self, token: bytes) -> str:
        return self._fernet.decrypt(token).decode("utf-8")


class SystemCatalog:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()
        with self.get_connection() as conn:
            self.vault = SystemVault(conn)

    def get_connection(self) -> sqlite3.Connection:
        # Concurrency robustness
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def _init_db(self) -> None:
        # Apply timeouts here too for safe startup
        with sqlite3.connect(self.db_path, timeout=5.0) as conn:
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.executescript(SCHEMA_V1)

            cur = conn.execute("SELECT value FROM meta WHERE key='schema_version'")
            if not cur.fetchone():
                conn.execute(
                    "INSERT INTO meta (key, value, updated_at) VALUES (?, ?, ?)",
                    ("schema_version", "1", time.time()),
                )

    def upsert_mount(
        self,
        mount_id: str,
        doc_id: str,
        path: str,
        secret: Optional[str],
        topo_hash: str,
        mount_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Persist mount state.

        secret may be None for public Genesis mounts.
        mount_config stores transport settings (example: {"transport": "clarion"}).
        """
        enc = self.vault.encrypt(secret) if secret else None
        ts = time.time()
        config_json = json.dumps(mount_config) if mount_config else None

        with self.get_connection() as conn:
            # FIX: 10 placeholders, 10 values (status and retry_count are literals)
            conn.execute(
                """
                INSERT INTO mounts (
                    mount_id, doc_id, shard_path, enc_secret, expected_topology_hash, mount_config,
                    created_at, updated_at, status, last_ok_ts, last_attempt_ts, retry_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, 0)
                ON CONFLICT(mount_id) DO UPDATE SET
                    status='active',
                    enc_secret=excluded.enc_secret,
                    mount_config=excluded.mount_config,
                    expected_topology_hash=excluded.expected_topology_hash,
                    last_ok_ts=excluded.last_ok_ts,
                    last_attempt_ts=excluded.last_attempt_ts,
                    retry_count=0,
                    updated_at=excluded.updated_at
                """,
                (mount_id, doc_id, path, enc, topo_hash, config_json, ts, ts, ts, ts),
            )
            conn.commit()

    def set_mount_error(self, mount_id: str, error: str) -> None:
        ts = time.time()
        with self.get_connection() as conn:
            conn.execute(
                """
                UPDATE mounts SET
                    status='error',
                    last_error=?,
                    last_attempt_ts=?,
                    retry_count = retry_count + 1,
                    updated_at=?
                WHERE mount_id=?
                """,
                (str(error), ts, ts, mount_id),
            )
            conn.commit()

    def set_mount_stopped(self, mount_id: str) -> None:
        with self.get_connection() as conn:
            conn.execute(
                "UPDATE mounts SET status='stopped', updated_at=? WHERE mount_id=?",
                (time.time(), mount_id),
            )
            conn.commit()

    def get_active_mounts(self) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            rows = conn.execute("SELECT * FROM mounts WHERE auto_mount=1").fetchall()

        results: List[Dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            mc = d.get("mount_config")
            if mc:
                try:
                    d["mount_config"] = json.loads(mc)
                except Exception:
                    d["mount_config"] = {}
            else:
                d["mount_config"] = {}
            results.append(d)
        return results

    def decrypt_secret(self, enc_secret: Any) -> str:
        if enc_secret is None:
            raise ValueError("No secret found for mount")
        if isinstance(enc_secret, memoryview):
            enc_secret = enc_secret.tobytes()
        return self.vault.decrypt(enc_secret)

    def log_system_event(
        self,
        event_type: str,
        actor_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO system_events (ts, event_type, actor_id, details)
                VALUES (?, ?, ?, ?)
                """,
                (time.time(), event_type, actor_id, json.dumps(details or {})),
            )
            conn.commit()

    def check_health(self) -> Tuple[bool, Optional[str]]:
        try:
            with self.get_connection() as conn:
                conn.execute("SELECT 1").fetchone()
            return True, None
        except Exception as e:
            return False, str(e)
