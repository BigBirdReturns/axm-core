from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any, Dict, List, Mapping, MutableMapping, Sequence

from .bundle import validate_aperture_extension_bundle
from .contracts import (
    APERTURE_EXTENSION_RUNTIME_FORMAT,
    APERTURE_EXTENSION_SPECS,
    SPECS,
    ApertureExtensionError,
    ApertureExtensionMount,
)


def _quote_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, 1):
            line = raw.rstrip("\n")
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ApertureExtensionError(f"{path}:{line_number}: invalid JSON") from exc
            if not isinstance(value, dict):
                raise ApertureExtensionError(f"{path}:{line_number}: row is not an object")
            canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if line != canonical:
                raise ApertureExtensionError(f"{path}:{line_number}: row is not canonical JSON")
            rows.append(value)
    return rows


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _verify_genesis(shard_dir: Path, trusted_key: Path) -> Mapping[str, Any]:
    from axm_verify.logic import verify_shard

    return verify_shard(shard_dir, trusted_key)


class ApertureExtensionRuntime:
    """Mount and remove disposable Aperture tables in an existing Spectra engine."""

    def __init__(self, spectra_engine: Any) -> None:
        if not hasattr(spectra_engine, "con"):
            raise TypeError("spectra_engine must expose its DuckDB connection as .con")
        self._engine = spectra_engine
        self._lock = threading.RLock()
        self._mounts: MutableMapping[str, ApertureExtensionMount] = {}

    def mount_verified_shard(
        self,
        shard_path: str | Path,
        trusted_key_path: str | Path,
    ) -> ApertureExtensionMount:
        shard_dir = Path(shard_path).expanduser().resolve(strict=True)
        trusted_key = Path(trusted_key_path).expanduser().resolve(strict=True)
        if not shard_dir.is_dir() or not trusted_key.is_file():
            raise ApertureExtensionError("shard and trusted key must exist")
        if _is_within(trusted_key, shard_dir):
            raise ApertureExtensionError("trusted publisher key must be held outside the shard")
        verification = _verify_genesis(shard_dir, trusted_key)
        if verification.get("status") != "PASS":
            raise ApertureExtensionError(f"Genesis verification failed: {verification}")

        manifest_path = shard_dir / "manifest.json"
        manifest_bytes = manifest_path.read_bytes()
        try:
            manifest = json.loads(manifest_bytes.decode("utf-8"))
        except Exception as exc:
            raise ApertureExtensionError("manifest.json is unreadable") from exc
        manifest_extensions = manifest.get("extensions") or []
        if not isinstance(manifest_extensions, list) or any(
            not isinstance(item, str) for item in manifest_extensions
        ):
            raise ApertureExtensionError("manifest.extensions must be a string array")

        raw_bundle: Dict[str, Sequence[Mapping[str, Any]]] = {}
        for extension_id in APERTURE_EXTENSION_SPECS:
            path = shard_dir / "ext" / f"{extension_id}.jsonl"
            declared = extension_id in manifest_extensions
            if declared != path.is_file():
                raise ApertureExtensionError(
                    f"manifest/file parity failure for registered extension {extension_id}"
                )
            if declared:
                raw_bundle[extension_id] = _read_jsonl(path)
        bundle = validate_aperture_extension_bundle(raw_bundle)

        manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
        mount_id = "apxmount1_" + hashlib.sha256(
            (manifest_sha256 + "|aperture@1").encode()
        ).hexdigest()
        suffix = mount_id[-16:]
        table_names: List[str] = []
        con = self._engine.con

        with self._lock:
            if mount_id in self._mounts:
                return self._mounts[mount_id]
            try:
                con.execute("BEGIN TRANSACTION")
                for extension_id in sorted(bundle):
                    spec = APERTURE_EXTENSION_SPECS[extension_id]
                    table_name = f"{spec.table_name}__{suffix}"
                    columns_sql = ", ".join(
                        f"{_quote_ident(column)} VARCHAR" for column in spec.columns
                    )
                    con.execute(f"CREATE TABLE {_quote_ident(table_name)} ({columns_sql})")
                    rows = bundle[extension_id]
                    if rows:
                        placeholders = ", ".join("?" for _ in spec.columns)
                        con.executemany(
                            f"INSERT INTO {_quote_ident(table_name)} VALUES ({placeholders})",
                            [[row[column] for column in spec.columns] for row in rows],
                        )
                    table_names.append(table_name)
                candidate = ApertureExtensionMount(
                    format=APERTURE_EXTENSION_RUNTIME_FORMAT,
                    mount_id=mount_id,
                    manifest_sha256=manifest_sha256,
                    source_path=str(shard_dir),
                    extension_ids=tuple(sorted(bundle)),
                    tables=tuple(sorted(table_names)),
                )
                self._mounts[mount_id] = candidate
                self._rebuild_union_views()
                con.execute("COMMIT")
                return candidate
            except Exception:
                try:
                    con.execute("ROLLBACK")
                finally:
                    self._mounts.pop(mount_id, None)
                raise

    def _rebuild_union_views(self) -> None:
        con = self._engine.con
        all_tables = {table for mount in self._mounts.values() for table in mount.tables}
        for spec in SPECS:
            con.execute(f"DROP VIEW IF EXISTS {_quote_ident(spec.table_name)}")
            parts = [
                f"SELECT * FROM {_quote_ident(table)}"
                for table in sorted(all_tables)
                if table.startswith(f"{spec.table_name}__")
            ]
            if parts:
                con.execute(
                    f"CREATE VIEW {_quote_ident(spec.table_name)} AS {' UNION ALL '.join(parts)}"
                )

    def unmount(self, mount_id: str) -> None:
        with self._lock:
            mount = self._mounts.pop(mount_id, None)
            if mount is None:
                return
            con = self._engine.con
            con.execute("BEGIN TRANSACTION")
            try:
                for table in mount.tables:
                    con.execute(f"DROP TABLE IF EXISTS {_quote_ident(table)}")
                self._rebuild_union_views()
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                self._mounts[mount_id] = mount
                raise

    def catalog(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "format": APERTURE_EXTENSION_RUNTIME_FORMAT,
                "authority": "rebuildable_query_cache_only",
                "mounts": [
                    {
                        "mount_id": mount.mount_id,
                        "manifest_sha256": mount.manifest_sha256,
                        "source_path": mount.source_path,
                        "extension_ids": list(mount.extension_ids),
                        "tables": list(mount.tables),
                    }
                    for mount in sorted(self._mounts.values(), key=lambda item: item.mount_id)
                ],
            }
