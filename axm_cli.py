"""
axm_cli.py

The unified AXM orchestration layer.
Translates human intent (names) into cryptographic reality (shards),
and routes them to the correct subsystem (Forge, Genesis, Spectra)
without violating their boundaries.

Contract: cli/CONTRACT.md
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import click
import requests

from registry.resolve import Registry, RegistryError

# ---------------------------------------------------------------------------
# Configuration resolution: CLI flag > env var > config file > default
# ---------------------------------------------------------------------------

def _load_config_file() -> dict:
    config_path = Path.home() / ".axm" / "config.json"
    if config_path.exists():
        try:
            with open(config_path) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


_CONFIG = _load_config_file()


def _resolve_config(cli_val: Optional[str], env_key: str, config_key: str, default: str) -> str:
    if cli_val is not None:
        return cli_val
    env = os.environ.get(env_key)
    if env:
        return env
    cfg = _CONFIG.get(config_key)
    if cfg:
        return cfg
    return default


def _default_shards_dir() -> str:
    local = Path("./shards")
    if local.exists():
        return str(local)
    return str(Path.home() / ".axm" / "shards")


def _default_registry() -> str:
    return os.environ.get("AXM_REGISTRY",
           _CONFIG.get("registry", "./registry/artifacts.json"))


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _out(as_json: bool, ok: bool, data: Optional[dict] = None,
         error_code: int = 1, error_msg: str = "") -> None:
    if as_json:
        if ok:
            click.echo(json.dumps({"ok": True, "data": data or {}}, indent=2))
        else:
            click.echo(json.dumps({"ok": False, "error": {"code": error_code, "message": error_msg}}, indent=2))
    # human output is handled inline by each command


def _fail(as_json: bool, msg: str, exit_code: int = 1, error_code: Optional[int] = None) -> None:
    if as_json:
        _out(as_json, False, error_code=error_code or exit_code, error_msg=msg)
    else:
        click.secho(f"✗ {msg}", fg="red", err=True)
    sys.exit(exit_code)


# ---------------------------------------------------------------------------
# CLI root
# ---------------------------------------------------------------------------

@click.group()
def cli():
    """AXM Protocol CLI — verifiable execution provenance."""
    pass


# ---------------------------------------------------------------------------
# axm resolve
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("ref")
@click.option("--lock", default=None, metavar="PATH", help="Resolve from lockfile, ignore registry state.")
@click.option("--registry", default=None, envvar="AXM_REGISTRY")
@click.option("--json", "as_json", is_flag=True)
def resolve(ref: str, lock: Optional[str], registry: Optional[str], as_json: bool):
    """Resolve a human ref to a shard_id. No side effects."""
    if lock:
        shard_id = _resolve_from_lock(lock, ref, as_json)
    else:
        reg_path = registry or _default_registry()
        reg = Registry(Path(reg_path))
        try:
            shard_id = reg.resolve(ref)
        except RegistryError as e:
            _fail(as_json, str(e), exit_code=2, error_code=2)
            return

    if as_json:
        _out(as_json, True, {"ref": ref, "shard_id": shard_id})
    else:
        click.echo(shard_id)


# ---------------------------------------------------------------------------
# axm verify
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("ref")
@click.option("--trusted-key", default=None, help="Override policy trust key.")
@click.option("--shards-dir", default=None, envvar="AXM_SHARDS_DIR")
@click.option("--registry", default=None, envvar="AXM_REGISTRY")
@click.option("--json", "as_json", is_flag=True)
def verify(ref: str, trusted_key: Optional[str], shards_dir: Optional[str],
           registry: Optional[str], as_json: bool):
    """Resolve ref and run Genesis hard verifier against the shard."""
    shard_id, shard_path = _resolve_to_path(ref, shards_dir, registry, as_json)

    if not as_json:
        click.echo(f"Resolved '{ref}' -> {shard_id}")
        click.echo("Passing to Genesis verifier...")

    trusted_key = trusted_key or _get_trust_key(registry or _default_registry(), ref)
    exit_code, _ = _run_verifier(shard_path, trusted_key)

    if exit_code == 0:
        if as_json:
            _out(as_json, True, {"shard_id": shard_id, "verified": True})
        else:
            click.secho("✓ Cryptographic verification passed.", fg="green")
    else:
        _fail(as_json, "Verification failed. Shard is invalid or tampered.", exit_code=3, error_code=3)


# ---------------------------------------------------------------------------
# axm mount
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("ref")
@click.option("--no-verify", is_flag=True, help="Skip verification. Dev/offline only.")
@click.option("--lock", default=None, metavar="PATH")
@click.option("--spectra-url", default=None, envvar="AXM_SPECTRA_URL")
@click.option("--shards-dir", default=None, envvar="AXM_SHARDS_DIR")
@click.option("--registry", default=None, envvar="AXM_REGISTRY")
@click.option("--json", "as_json", is_flag=True)
def mount(ref: str, no_verify: bool, lock: Optional[str], spectra_url: Optional[str],
          shards_dir: Optional[str], registry: Optional[str], as_json: bool):
    """Resolve, verify, then mount into Spectra. Verify is mandatory by default."""
    if lock:
        shard_id = _resolve_from_lock(lock, ref, as_json)
        shard_path = _shard_path(shards_dir, shard_id)
    else:
        shard_id, shard_path = _resolve_to_path(ref, shards_dir, registry, as_json)

    verified = False
    if not no_verify:
        if not as_json:
            click.echo(f"Resolved '{ref}' -> {shard_id}")
            click.echo("Verifying before mount...")
        trusted_key = _get_trust_key(registry or _default_registry(), ref)
        exit_code, _ = _run_verifier(shard_path, trusted_key)
        if exit_code != 0:
            _fail(as_json, "Verification failed. Refusing to mount.", exit_code=3, error_code=3)
        verified = True
    else:
        if not as_json:
            click.secho("⚠ Skipping verification (--no-verify). Dev only.", fg="yellow", err=True)

    url = _resolve_config(spectra_url, "AXM_SPECTRA_URL", "spectra_url", "http://localhost:8080")
    if not as_json:
        click.echo(f"Requesting Spectra mount at {url}...")

    try:
        response = requests.post(f"{url}/mount", json={"path": str(shard_path)}, timeout=10)
        response.raise_for_status()
        mount_id = response.json().get("mount_id", "")
    except requests.exceptions.RequestException as e:
        _fail(as_json, f"Failed to reach Spectra runtime: {e}", exit_code=5, error_code=5)
        return

    if as_json:
        _out(as_json, True, {"shard_id": shard_id, "mount_id": mount_id, "verified": verified})
    else:
        click.secho(f"✓ Shard mounted in Spectra. mount_id={mount_id}", fg="green")


# ---------------------------------------------------------------------------
# axm build
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("source_doc")
@click.option("--name", required=True, help="Canonical name e.g. medical/fm21-11")
@click.option("--reason", default="cli build", help="Audit log reason.")
@click.option("--shards-dir", default=None, envvar="AXM_SHARDS_DIR")
@click.option("--registry", default=None, envvar="AXM_REGISTRY")
@click.option("--json", "as_json", is_flag=True)
def build(source_doc: str, name: str, reason: str, shards_dir: Optional[str],
          registry: Optional[str], as_json: bool):
    """Extract, compile, verify, and register a new knowledge shard."""
    sd = _resolve_config(shards_dir, "AXM_SHARDS_DIR", "shards_dir", _default_shards_dir())
    reg_path = registry or _default_registry()

    if not as_json:
        click.echo(f"Building shard from {source_doc}...")

    # Call Forge with --json output. No stdout scraping.
    result = subprocess.run(
        ["axm-forge", "build", source_doc, "--out", sd, "--json"],
        capture_output=True, text=True
    )

    if result.returncode != 0:
        _fail(as_json, f"Forge build failed:\n{result.stderr}", exit_code=6, error_code=6)
        return

    try:
        forge_out = json.loads(result.stdout.strip())
        shard_id = forge_out["shard_id"]
        shard_path = Path(forge_out["path"])
    except (json.JSONDecodeError, KeyError) as e:
        _fail(as_json, f"Could not parse Forge JSON output: {e}\nRaw: {result.stdout}", exit_code=6, error_code=6)
        return

    # Verify before registering
    if not as_json:
        click.echo("Verifying compiled shard...")
    exit_code, _ = _run_verifier(shard_path, trusted_key=None)
    if exit_code != 0:
        _fail(as_json, "Compiled shard failed verification. Not registering.", exit_code=3, error_code=3)
        return

    # Register
    reg = Registry(Path(reg_path))
    try:
        reg.add_artifact(name, shard_id, reason=reason)
    except RegistryError:
        # Already exists — update current instead
        reg.set_current(name, shard_id, reason=reason)

    if as_json:
        _out(as_json, True, {"name": name, "shard_id": shard_id, "verified": True})
    else:
        click.secho(f"✓ Registered: {name} -> {shard_id}", fg="green")


# ---------------------------------------------------------------------------
# axm pin
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("refs", nargs=-1, required=True)
@click.option("--out", default="axm.lock.json", help="Lockfile path.")
@click.option("--registry", default=None, envvar="AXM_REGISTRY")
@click.option("--json", "as_json", is_flag=True)
def pin(refs: tuple, out: str, registry: Optional[str], as_json: bool):
    """Snapshot current registry state into a lockfile for reproducible runs."""
    reg_path = registry or _default_registry()
    reg = Registry(Path(reg_path))

    pins = {}
    for ref in refs:
        try:
            pins[ref] = reg.resolve(ref)
        except RegistryError as e:
            _fail(as_json, str(e), exit_code=2, error_code=2)
            return

    lockfile = {
        "pinned_at": datetime.now(timezone.utc).isoformat(),
        "pins": pins,
    }

    out_path = Path(out)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(lockfile, f, indent=2, sort_keys=True)
        f.write("\n")

    if as_json:
        _out(as_json, True, {"lockfile": str(out_path), "pins": pins})
    else:
        click.secho(f"✓ Lockfile written: {out_path}", fg="green")
        for name, shard_id in pins.items():
            click.echo(f"  {name} -> {shard_id}")


# ---------------------------------------------------------------------------
# axm alias
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("ref")
@click.argument("alias_str", metavar="ALIAS")
@click.option("--registry", default=None, envvar="AXM_REGISTRY")
@click.option("--json", "as_json", is_flag=True)
def alias(ref: str, alias_str: str, registry: Optional[str], as_json: bool):
    """Add a human-readable alias to an existing artifact."""
    reg = Registry(Path(registry or _default_registry()))
    try:
        reg.add_alias(ref, alias_str)
    except RegistryError as e:
        _fail(as_json, str(e), exit_code=2, error_code=2)
        return

    if as_json:
        _out(as_json, True, {"ref": ref, "alias": alias_str})
    else:
        click.secho(f"✓ Alias '{alias_str}' -> '{ref}'", fg="green")


# ---------------------------------------------------------------------------
# axm history
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("ref")
@click.option("--registry", default=None, envvar="AXM_REGISTRY")
@click.option("--json", "as_json", is_flag=True)
def history(ref: str, registry: Optional[str], as_json: bool):
    """Print the lineage of an artifact."""
    reg = Registry(Path(registry or _default_registry()))
    try:
        entries = reg.list_history(ref)
        current = reg.resolve(ref)
    except RegistryError as e:
        _fail(as_json, str(e), exit_code=2, error_code=2)
        return

    if as_json:
        _out(as_json, True, {"name": ref, "current": current, "history": entries})
    else:
        click.echo(ref)
        for i, entry in enumerate(entries):
            marker = "  ^ current" if entry["shard_id"] == current else ""
            click.echo(f"  [{i}] {entry['shard_id']}  {entry['timestamp']}  {entry['reason']}{marker}")


# ---------------------------------------------------------------------------
# axm list
# ---------------------------------------------------------------------------

@cli.command("list")
@click.option("--tag", default=None, help="Filter by tag.")
@click.option("--registry", default=None, envvar="AXM_REGISTRY")
@click.option("--json", "as_json", is_flag=True)
def list_artifacts(tag: Optional[str], registry: Optional[str], as_json: bool):
    """List all registered artifact names."""
    reg = Registry(Path(registry or _default_registry()))
    names = reg.list_artifacts()

    if tag:
        # Filter by tag — load raw data to check
        filtered = []
        for name in names:
            try:
                entry = reg._find(name)
                if entry and tag in entry.get("tags", []):
                    filtered.append(name)
            except Exception:
                pass
        names = filtered

    if as_json:
        _out(as_json, True, {"artifacts": names})
    else:
        for name in names:
            click.echo(name)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_to_path(ref: str, shards_dir: Optional[str], registry: Optional[str],
                     as_json: bool) -> tuple[str, Path]:
    reg_path = registry or _default_registry()
    reg = Registry(Path(reg_path))
    try:
        shard_id = reg.resolve(ref)
    except RegistryError as e:
        _fail(as_json, str(e), exit_code=2, error_code=2)
        raise  # unreachable but satisfies type checker

    shard_path = _shard_path(shards_dir, shard_id)
    if not shard_path.exists():
        _fail(as_json, f"Shard {shard_id} not found on disk at {shard_path}", exit_code=4, error_code=4)

    return shard_id, shard_path


def _shard_path(shards_dir: Optional[str], shard_id: str) -> Path:
    sd = _resolve_config(shards_dir, "AXM_SHARDS_DIR", "shards_dir", _default_shards_dir())
    return Path(sd) / shard_id


def _get_trust_key(registry_path: str, ref: str) -> Optional[str]:
    """Pull trust_key from artifact policy if present."""
    try:
        reg = Registry(Path(registry_path))
        entry = reg._find(ref)
        if entry:
            return entry.get("policy", {}).get("trust_key")
    except Exception:
        pass
    return None


def _run_verifier(shard_path: Path, trusted_key: Optional[str]) -> tuple[int, str]:
    cmd = ["axm-verify", "shard", str(shard_path)]
    if trusted_key:
        cmd += ["--trusted-key", trusted_key]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout + result.stderr


def _resolve_from_lock(lock_path: str, ref: str, as_json: bool) -> str:
    try:
        with open(lock_path) as f:
            lockfile = json.load(f)
        pins = lockfile.get("pins", {})
        if ref not in pins:
            _fail(as_json, f"Ref '{ref}' not found in lockfile {lock_path}", exit_code=2, error_code=2)
        return pins[ref]
    except FileNotFoundError:
        _fail(as_json, f"Lockfile not found: {lock_path}", exit_code=1)
    except json.JSONDecodeError as e:
        _fail(as_json, f"Invalid lockfile JSON: {e}", exit_code=1)
    raise RuntimeError("unreachable")


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cli()
