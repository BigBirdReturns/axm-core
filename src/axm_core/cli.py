"""
axm_core.cli
────────────
The unified `axm` CLI host.

Discovery model
───────────────
Installed spokes register themselves via the `axm.spokes` entry-point group:

    [project.entry-points."axm.spokes"]
    chat = "axm_chat.cli:chat_group"
    show = "axm_show.cli:show_group"

On startup this module calls `importlib.metadata.entry_points(group="axm.spokes")`,
loads every registered group, and attaches them as sub-command groups on the root
`axm` command.  No spoke needs to be known to Core at import time.

Core commands
─────────────
Core itself registers a small set of commands that operate across all shards
and all mounted spokes:

    axm status          — health check: genesis version, spectra engine, mounted shards
    axm list            — list all known shards across all mounted spokes
    axm verify <shard>  — run axm-genesis verifier on a shard path or name
    axm spokes          — list discovered spokes and their version

Spoke commands are namespaced under their spoke name:

    axm chat import ./export.json
    axm chat distill
    axm chat query "what decisions have we made"
    axm show plan ./show.yaml
"""

from __future__ import annotations

import sys
from importlib.metadata import entry_points, version, PackageNotFoundError
from pathlib import Path
from typing import Optional

import click


# ─── Version helpers ──────────────────────────────────────────────────────────

def _pkg_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "not installed"


# ─── Spoke discovery ──────────────────────────────────────────────────────────

def _load_spokes() -> dict[str, click.Group]:
    """
    Load all registered axm.spokes entry points and return them as a
    {name: click.Group} dict.  Import errors are caught per-spoke so a broken
    spoke does not prevent the rest of the CLI from loading.
    """
    discovered: dict[str, click.Group] = {}
    eps = entry_points(group="axm.spokes")
    for ep in eps:
        try:
            obj = ep.load()
            if isinstance(obj, click.Group):
                discovered[ep.name] = obj
            else:
                click.echo(
                    f"[axm] WARNING: spoke '{ep.name}' entry point is not a "
                    f"click.Group (got {type(obj).__name__}) — skipped",
                    err=True,
                )
        except Exception as exc:  # noqa: BLE001
            click.echo(
                f"[axm] WARNING: could not load spoke '{ep.name}': {exc}",
                err=True,
            )
    return discovered


# ─── Core commands ────────────────────────────────────────────────────────────

@click.command("status")
def cmd_status() -> None:
    """Health check — genesis version, Spectra engine, shard count."""
    click.echo(f"axm-genesis   {_pkg_version('axm-genesis')}")
    click.echo(f"axm-core      {_pkg_version('axm-core')}")

    # Spectra engine probe
    try:
        from axiom_runtime.engine import SpectraEngine  # noqa: F401
        click.echo("spectra       ok")
    except ImportError:
        click.echo("spectra       NOT AVAILABLE  (install axm-core with spectra)")

    # Shard count from default store
    shard_root = Path.home() / ".axm" / "shards"
    if shard_root.exists():
        shards = [d for d in shard_root.iterdir() if d.is_dir()]
        click.echo(f"shards        {len(shards)} in {shard_root}")
    else:
        click.echo(f"shards        0  (store not yet created at {shard_root})")

    # Spoke inventory
    spokes = _load_spokes()
    if spokes:
        click.echo(f"spokes        {', '.join(sorted(spokes))}")
    else:
        click.echo("spokes        none installed")


@click.command("list")
@click.option("--decision", is_flag=True, default=False, help="Only decision shards.")
@click.option("--verified/--no-verified", default=None, help="Filter by verification status.")
def cmd_list(decision: bool, verified: Optional[bool]) -> None:
    """List all shards in the local store."""
    shard_root = Path.home() / ".axm" / "shards"
    if not shard_root.exists():
        click.echo("No shard store found.  Run `axm-chat import` to create one.")
        return

    import json

    shards = sorted(shard_root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    shown = 0
    for shard_dir in shards:
        if not shard_dir.is_dir():
            continue
        manifest_path = shard_dir / "manifest.json"
        if not manifest_path.exists():
            continue
        try:
            m = json.loads(manifest_path.read_text())
        except Exception:
            continue

        is_decision = m.get("shard_type") == "decision"
        if decision and not is_decision:
            continue

        sig_path = shard_dir / "sig" / "manifest.sig"
        is_verified = sig_path.exists()
        if verified is not None and is_verified != verified:
            continue

        flag = "✓" if is_verified else "✗"
        dtype = " [decision]" if is_decision else ""
        title = m.get("title", shard_dir.name)[:60]
        click.echo(f"  {flag}  {shard_dir.name:<36}  {title}{dtype}")
        shown += 1

    if shown == 0:
        click.echo("No shards match the current filter.")
    else:
        click.echo(f"\n{shown} shard(s)")


@click.command("verify")
@click.argument("shard", required=False)
def cmd_verify(shard: Optional[str]) -> None:
    """Verify a shard's Merkle tree and signature.

    SHARD can be a shard name prefix or an absolute path.
    If omitted, all shards in the local store are verified.
    """
    try:
        from axm_verify.cli import verify_shard  # from axm-genesis
    except ImportError:
        click.echo("axm-genesis is not installed.  Run: pip install -e ./axm-genesis")
        sys.exit(1)

    shard_root = Path.home() / ".axm" / "shards"
    if shard:
        # Resolve by name prefix or literal path
        candidate = Path(shard)
        if not candidate.exists():
            matches = [d for d in shard_root.iterdir() if d.name.startswith(shard)]
            if not matches:
                click.echo(f"No shard found matching '{shard}'")
                sys.exit(1)
            candidate = matches[0]
        verify_shard(str(candidate))
    else:
        if not shard_root.exists():
            click.echo("No shard store found.")
            return
        for d in sorted(shard_root.iterdir()):
            if d.is_dir() and (d / "manifest.json").exists():
                verify_shard(str(d))


@click.command("spokes")
def cmd_spokes() -> None:
    """List installed spokes discovered via axm.spokes entry points."""
    discovered = _load_spokes()
    if not discovered:
        click.echo("No spokes installed.\n")
        click.echo("To install the chat spoke:")
        click.echo("  pip install -e ./axm-chat")
        return

    click.echo(f"{'SPOKE':<16}  {'COMMANDS'}")
    click.echo("─" * 60)
    for name, group in sorted(discovered.items()):
        cmds = ", ".join(sorted(group.commands)) if hasattr(group, "commands") else "?"
        pkg_name = f"axm-{name}"
        ver = _pkg_version(pkg_name)
        click.echo(f"  {name:<14}  {cmds}   ({ver})")


# ─── Root group ───────────────────────────────────────────────────────────────

@click.group()
@click.version_option(package_name="axm-core", prog_name="axm")
@click.pass_context
def axm(ctx: click.Context) -> None:
    """AXM — sovereign knowledge for AI conversations and beyond.

    \b
    Core commands:
      axm status          health check
      axm list            list all shards
      axm verify          verify shard integrity
      axm spokes          list installed spokes

    \b
    Spoke commands (when spoke is installed):
      axm chat import     ingest conversation exports
      axm chat distill    extract decisions via local LLM
      axm chat query      natural language query

    Install spokes:
      pip install -e ./axm-chat
      pip install -e ./axm-show
    """
    ctx.ensure_object(dict)


def _build_cli() -> click.Group:
    """Build the final CLI by attaching core commands and discovered spokes."""
    axm.add_command(cmd_status)
    axm.add_command(cmd_list)
    axm.add_command(cmd_verify)
    axm.add_command(cmd_spokes)

    for name, group in _load_spokes().items():
        axm.add_command(group, name=name)

    return axm


def main() -> None:
    cli = _build_cli()
    cli()


if __name__ == "__main__":
    main()
