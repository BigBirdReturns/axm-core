"""
axm-core/registry/resolve.py

Maps human artifact refs to Genesis shard_ids.
Registry can move pointers. Registry never rewrites shards.

Usage:
    from registry.resolve import Registry
    reg = Registry()
    shard_id = reg.resolve("fm21-11/hemorrhage")
    shard_id = reg.resolve("fm21-11:latest")       # alias
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import jsonschema
    _JSONSCHEMA_AVAILABLE = True
except ImportError:
    _JSONSCHEMA_AVAILABLE = False

REGISTRY_PATH = Path(__file__).parent / "artifacts.json"
SCHEMA_PATH = Path(__file__).parent / "schema.json"
SHARD_ID_RE = re.compile(r"^shard_blake3_[a-f0-9]+$")


def _load_schema() -> Optional[dict]:
    if not _JSONSCHEMA_AVAILABLE:
        return None
    if not SCHEMA_PATH.exists():
        return None
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


_SCHEMA = _load_schema()


class RegistryError(Exception):
    pass


class Registry:
    def __init__(self, path: Path = REGISTRY_PATH):
        self.path = path
        self._data: dict = {}
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(self, ref: str) -> str:
        """
        Resolve a human ref to a shard_id.

        Accepts:
          - canonical name:  "fm21-11/hemorrhage"
          - alias:           "fm21-11:latest"
          - bare shard_id:   "shard_blake3_abc..."  (pass-through)
        """
        if SHARD_ID_RE.match(ref):
            return ref  # already a shard_id, pass through

        name, entry = self._find_with_name(ref)
        if entry is None:
            raise RegistryError(f"Unknown artifact ref: {ref!r}")
        return entry["current"]

    def resolve_with_meta(self, ref: str) -> dict:
        """
        Resolve a ref and return canonical name + shard_id.
        Use this when the caller needs audit clarity or UI display.

        Returns:
            {"name": <canonical_name>, "shard_id": <shard_id>, "resolved_from": <ref>}
        """
        if SHARD_ID_RE.match(ref):
            return {"name": ref, "shard_id": ref, "resolved_from": ref}

        name, entry = self._find_with_name(ref)
        if entry is None:
            raise RegistryError(f"Unknown artifact ref: {ref!r}")
        return {
            "name": name,
            "shard_id": entry["current"],
            "resolved_from": ref,
        }

    def set_current(self, name: str, shard_id: str, reason: str,
                    compiler: Optional[str] = None,
                    spec_version: Optional[str] = None) -> None:
        """
        Point an artifact at a new shard. Appends to history.
        Never removes old entries.
        """
        if not SHARD_ID_RE.match(shard_id):
            raise RegistryError(f"Invalid shard_id format: {shard_id!r}")

        _, entry = self._find_with_name(name)
        if entry is None:
            raise RegistryError(f"Unknown artifact name: {name!r}")

        entry["current"] = shard_id
        hist: dict = {
            "shard_id": shard_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
        }
        if compiler:
            hist["compiler"] = compiler
        if spec_version:
            hist["spec_version"] = spec_version
        entry["history"].append(hist)
        self._save()

    def add_alias(self, name: str, alias: str) -> None:
        """
        Add an alias to an artifact.
        Raises RegistryError if the alias is already claimed by any artifact.
        """
        # Check for collision across all artifacts before appending
        existing_name, existing_entry = self._find_with_name(alias)
        if existing_entry is not None:
            if existing_name != name:
                raise RegistryError(
                    f"Alias {alias!r} is already in use by artifact {existing_name!r}"
                )
            return  # alias already points to this artifact, no-op

        _, entry = self._find_with_name(name)
        if entry is None:
            raise RegistryError(f"Unknown artifact name: {name!r}")

        entry.setdefault("aliases", []).append(alias)
        self._save()

    def list_history(self, ref: str) -> list[dict]:
        _, entry = self._find_with_name(ref)
        if entry is None:
            raise RegistryError(f"Unknown artifact ref: {ref!r}")
        return list(entry["history"])

    def list_artifacts(self) -> list[str]:
        return list(self._data.get("artifacts", {}).keys())

    def export_lockfile(self, refs: list[str]) -> dict:
        """
        Returns a pinned mapping of ref -> shard_id.
        Suitable for reproducible runs - snapshot the current state.
        """
        return {ref: self.resolve(ref) for ref in refs}

    def add_artifact(self, name: str, shard_id: str, reason: str,
                     aliases: Optional[list[str]] = None,
                     tags: Optional[list[str]] = None,
                     trust_key: Optional[str] = None,
                     compiler: Optional[str] = None,
                     spec_version: Optional[str] = None) -> None:
        """
        Register a brand new artifact.
        compiler and spec_version flow into the initial history entry.
        """
        if not SHARD_ID_RE.match(shard_id):
            raise RegistryError(f"Invalid shard_id format: {shard_id!r}")
        if name in self._data.setdefault("artifacts", {}):
            raise RegistryError(f"Artifact already exists: {name!r}. Use set_current() to update.")

        hist: dict = {
            "shard_id": shard_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
        }
        if compiler:
            hist["compiler"] = compiler
        if spec_version:
            hist["spec_version"] = spec_version

        entry: dict = {
            "name": name,
            "aliases": aliases or [],
            "tags": tags or [],
            "current": shard_id,
            "history": [hist],
        }
        if trust_key:
            entry["policy"] = {"trust_key": trust_key, "require_verified": True}

        self._data["artifacts"][name] = entry
        self._save()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _find_with_name(self, ref: str) -> tuple[Optional[str], Optional[dict]]:
        """
        Find entry by canonical name or alias.
        Returns (canonical_name, entry) so callers always know the source name.
        Replaces the old _find() which dropped name context.
        """
        artifacts = self._data.get("artifacts", {})

        # direct name match
        if ref in artifacts:
            return ref, artifacts[ref]

        # alias scan
        for name, entry in artifacts.items():
            if ref in entry.get("aliases", []):
                return name, entry

        return None, None

    def _validate(self, data: dict) -> None:
        """
        Validate against schema.json on every load and save.
        Fails loudly rather than silently loading corrupt state.
        Soft-fails if jsonschema is not installed.
        """
        if not _JSONSCHEMA_AVAILABLE or _SCHEMA is None:
            return
        try:
            jsonschema.validate(instance=data, schema=_SCHEMA)
        except jsonschema.ValidationError as e:
            raise RegistryError(f"Registry schema validation failed: {e.message}") from e

    def _load(self) -> None:
        if not self.path.exists():
            self._data = {"artifacts": {}}
            return
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._validate(data)
        self._data = data

    def _save(self) -> None:
        self._validate(self._data)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, sort_keys=True, ensure_ascii=False)
            f.write("\n")
