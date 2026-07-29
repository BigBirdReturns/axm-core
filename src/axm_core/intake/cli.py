from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from . import (
    FORMATS,
    IntakeError,
    conform,
    discover_adapters,
    handle_stdio,
    observation_id,
    translate,
    validate,
    validate_adapter,
)
from .store import IntakeStore, StoreConfig, StoreError


def load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise click.ClickException(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise click.ClickException(f"expected a JSON object in {path}")
    return value


def fail(exc: BaseException) -> None:
    errors = getattr(exc, "errors", (str(exc),))
    for error in errors:
        click.echo(f"ERROR: {error}", err=True)
    raise click.exceptions.Exit(2)


def store_for(root: Path | None, writer_id: str | None) -> IntakeStore:
    try:
        return IntakeStore(StoreConfig.load(root, writer_id))
    except (StoreError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@click.group("intake")
def intake_group() -> None:
    """Bridge community evidence into AXM without granting authority."""


@intake_group.command("translate")
@click.argument("format_name", type=click.Choice(FORMATS, case_sensitive=False))
@click.argument("source", type=click.Path(path_type=Path, exists=True, dir_okay=False))
@click.option("--output", type=click.Path(path_type=Path))
@click.option("--observed-at")
def cmd_translate(format_name: str, source: Path, output: Path | None, observed_at: str | None) -> None:
    raw = source.read_bytes()
    try:
        result = translate(json.loads(raw.decode()), format_name, raw, observed_at)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise click.ClickException(f"source is not UTF-8 JSON: {exc}") from exc
    except IntakeError as exc:
        fail(exc)
    text = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        click.echo(str(output))
    else:
        click.echo(text, nl=False)


@intake_group.command("validate")
@click.argument("observation", type=click.Path(path_type=Path, exists=True, dir_okay=False))
@click.option("--verify-locator", is_flag=True)
def cmd_validate(observation: Path, verify_locator: bool) -> None:
    try:
        result = validate(load(observation), observation.parent, verify_locator)
    except IntakeError as exc:
        fail(exc)
    click.echo(
        f"PASS  {result['observation']['id']}  "
        f"payload={'verified' if result['payload_verified'] else 'declared'}"
    )


@intake_group.command("conform")
@click.argument("observation", type=click.Path(path_type=Path, exists=True, dir_okay=False))
@click.option("--verify-locator", is_flag=True)
@click.option("--json-out", is_flag=True)
def cmd_conform(observation: Path, verify_locator: bool, json_out: bool) -> None:
    result = conform(load(observation), observation.parent, verify_locator)
    if json_out:
        click.echo(json.dumps(result, sort_keys=True))
    else:
        click.echo(result["highest_level"] or "INVALID")
        for level in result["achieved"]:
            click.echo(f"  PASS  {level}")
        for level, blockers in result["blockers"].items():
            for blocker in blockers:
                click.echo(f"  HOLD  {level}: {blocker}")
    if not result["valid"]:
        raise click.exceptions.Exit(2)


@intake_group.command("fingerprint")
@click.argument("observation", type=click.Path(path_type=Path, exists=True, dir_okay=False))
def cmd_fingerprint(observation: Path) -> None:
    click.echo(observation_id(load(observation)))


@intake_group.command("adapters")
@click.option("--json-out", is_flag=True)
def cmd_adapters(json_out: bool) -> None:
    records = discover_adapters()
    if json_out:
        click.echo(json.dumps(records, sort_keys=True))
        return
    for item in records:
        click.echo(item.get("id") or f"ERROR {item['error']}")


@intake_group.group("adapter")
def adapter_group() -> None:
    """Validate third-party adapter declarations."""


@adapter_group.command("validate")
@click.argument("manifest", type=click.Path(path_type=Path, exists=True, dir_okay=False))
def cmd_adapter_validate(manifest: Path) -> None:
    try:
        result = validate_adapter(load(manifest))
    except IntakeError as exc:
        fail(exc)
    click.echo(f"PASS  {result['id']}  {result['version']}")


@intake_group.command("stdio")
def cmd_stdio() -> None:
    """Serve the language-neutral one-request-per-line bridge protocol."""
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            response = {
                "protocol": "axm-intake-stdio/1",
                "request_id": "",
                "status": "error",
                "result": None,
                "errors": [str(exc)],
            }
        else:
            response = handle_stdio(request)
        click.echo(json.dumps(response, ensure_ascii=False, separators=(",", ":")))


@intake_group.group("store")
def store_group() -> None:
    """Operate the crash-safe local observation custody store."""


def store_options(function):
    function = click.option(
        "--writer-id",
        envvar="AXM_INTAKE_WRITER",
        help="Stable identifier for this writer stream.",
    )(function)
    function = click.option(
        "--root",
        type=click.Path(path_type=Path),
        envvar="AXM_INTAKE_STORE",
        help="Store root; defaults to ~/.axm/intake.",
    )(function)
    return function


@store_group.command("admit")
@click.argument("observation", type=click.Path(path_type=Path, exists=True, dir_okay=False))
@click.option("--json-out", is_flag=True)
@store_options
def cmd_store_admit(
    observation: Path,
    json_out: bool,
    root: Path | None,
    writer_id: str | None,
) -> None:
    store = store_for(root, writer_id)
    try:
        receipt = store.admit_file(observation)
    except (IntakeError, StoreError, OSError, ValueError) as exc:
        fail(exc)
    if json_out:
        click.echo(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    else:
        click.echo(
            f"PASS  {receipt['observation_id']}  "
            f"event={receipt['event_seq']}  custody={receipt['conformance_level']}"
        )


@store_group.command("status")
@click.option("--json-out", is_flag=True)
@store_options
def cmd_store_status(json_out: bool, root: Path | None, writer_id: str | None) -> None:
    result = store_for(root, writer_id).status()
    if json_out:
        click.echo(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        click.echo(
            f"{result['observations']} observation(s), {result['contents']} content object(s), "
            f"{result['events']} event(s), {result['quarantined']} quarantined"
        )
        click.echo(result["root"])


@store_group.command("verify")
@click.option("--json-out", is_flag=True)
@store_options
def cmd_store_verify(json_out: bool, root: Path | None, writer_id: str | None) -> None:
    result = store_for(root, writer_id).verify()
    if json_out:
        click.echo(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        click.echo(
            f"{result['status']}  observations={result['observations_checked']}  "
            f"events={result['events_checked']}"
        )
        for error in result["errors"]:
            click.echo(f"  ERROR  {error}", err=True)
    if result["status"] != "PASS":
        raise click.exceptions.Exit(1)


@store_group.command("rebuild")
@click.option("--json-out", is_flag=True)
@store_options
def cmd_store_rebuild(json_out: bool, root: Path | None, writer_id: str | None) -> None:
    try:
        result = store_for(root, writer_id).rebuild_index()
    except (StoreError, OSError, ValueError) as exc:
        fail(exc)
    if json_out:
        click.echo(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        click.echo(
            f"recovered={result['recovered']} failed={result['failed']} backup={result['backup']}"
        )
    if result["failed"]:
        raise click.exceptions.Exit(1)


@store_group.command("backup")
@click.argument("destination", type=click.Path(path_type=Path, dir_okay=False))
@store_options
def cmd_store_backup(destination: Path, root: Path | None, writer_id: str | None) -> None:
    try:
        result = store_for(root, writer_id).backup(destination)
    except (StoreError, OSError, ValueError) as exc:
        fail(exc)
    click.echo(str(result))


@store_group.command("restore")
@click.argument("archive", type=click.Path(path_type=Path, exists=True, dir_okay=False))
@click.argument("destination", type=click.Path(path_type=Path))
@click.option("--writer-id", envvar="AXM_INTAKE_WRITER")
def cmd_store_restore(archive: Path, destination: Path, writer_id: str | None) -> None:
    try:
        restored = IntakeStore.restore_backup(
            archive,
            destination,
            writer_id=writer_id,
        )
    except (StoreError, OSError, ValueError) as exc:
        fail(exc)
    click.echo(json.dumps(restored.verify(), ensure_ascii=False, sort_keys=True))


@store_group.group("spool")
def spool_group() -> None:
    """Use the atomic file-drop protocol for disconnected producers."""


@spool_group.command("submit")
@click.argument("observation", type=click.Path(path_type=Path, exists=True, dir_okay=False))
@store_options
def cmd_spool_submit(observation: Path, root: Path | None, writer_id: str | None) -> None:
    try:
        path = store_for(root, writer_id).spool_submit(observation)
    except (StoreError, OSError, ValueError) as exc:
        fail(exc)
    click.echo(str(path))


@spool_group.command("pump")
@click.option("--limit", type=click.IntRange(1, 1000), default=100, show_default=True)
@click.option("--json-out", is_flag=True)
@store_options
def cmd_spool_pump(
    limit: int,
    json_out: bool,
    root: Path | None,
    writer_id: str | None,
) -> None:
    try:
        result = store_for(root, writer_id).spool_pump(limit=limit)
    except (StoreError, OSError, ValueError) as exc:
        fail(exc)
    if json_out:
        click.echo(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        click.echo(
            f"attempted={result['attempted']} accepted={result['accepted']} "
            f"rejected={result['rejected']} recovered={result['recovered']}"
        )
    if result["rejected"]:
        raise click.exceptions.Exit(1)


def main() -> None:
    intake_group()


if __name__ == "__main__":
    main()
