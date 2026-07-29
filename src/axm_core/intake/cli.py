from __future__ import annotations
import json, sys
from pathlib import Path
import click
from . import FORMATS, IntakeError, canonical, conform, discover_adapters, handle_stdio, observation_id, translate, validate, validate_adapter


def load(path: Path):
    try: return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc: raise click.ClickException(f"cannot read {path}: {exc}") from exc


def fail(exc: IntakeError):
    for error in exc.errors: click.echo(f"ERROR: {error}", err=True)
    raise click.exceptions.Exit(2)


@click.group("intake")
def intake_group(): """Bridge community evidence into AXM without granting authority."""


@intake_group.command("translate")
@click.argument("format_name", type=click.Choice(FORMATS, case_sensitive=False))
@click.argument("source", type=click.Path(path_type=Path, exists=True, dir_okay=False))
@click.option("--output", type=click.Path(path_type=Path))
@click.option("--observed-at")
def cmd_translate(format_name, source, output, observed_at):
    raw = source.read_bytes()
    try: result = translate(json.loads(raw.decode()), format_name, raw, observed_at)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc: raise click.ClickException(f"source is not UTF-8 JSON: {exc}")
    except IntakeError as exc: fail(exc)
    text = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if output: output.parent.mkdir(parents=True, exist_ok=True); output.write_text(text, encoding="utf-8"); click.echo(str(output))
    else: click.echo(text, nl=False)


@intake_group.command("validate")
@click.argument("observation", type=click.Path(path_type=Path, exists=True, dir_okay=False))
@click.option("--verify-locator", is_flag=True)
def cmd_validate(observation, verify_locator):
    try: result = validate(load(observation), observation.parent, verify_locator)
    except IntakeError as exc: fail(exc)
    click.echo(f"PASS  {result['observation']['id']}  payload={'verified' if result['payload_verified'] else 'declared'}")


@intake_group.command("conform")
@click.argument("observation", type=click.Path(path_type=Path, exists=True, dir_okay=False))
@click.option("--verify-locator", is_flag=True)
@click.option("--json-out", is_flag=True)
def cmd_conform(observation, verify_locator, json_out):
    result = conform(load(observation), observation.parent, verify_locator)
    if json_out: click.echo(json.dumps(result, sort_keys=True))
    else:
        click.echo(result["highest_level"] or "INVALID")
        for level in result["achieved"]: click.echo(f"  PASS  {level}")
        for level, blockers in result["blockers"].items():
            for blocker in blockers: click.echo(f"  HOLD  {level}: {blocker}")
    if not result["valid"]: raise click.exceptions.Exit(2)


@intake_group.command("fingerprint")
@click.argument("observation", type=click.Path(path_type=Path, exists=True, dir_okay=False))
def cmd_fingerprint(observation): click.echo(observation_id(load(observation)))


@intake_group.command("adapters")
@click.option("--json-out", is_flag=True)
def cmd_adapters(json_out):
    records = discover_adapters()
    if json_out: click.echo(json.dumps(records, sort_keys=True)); return
    for item in records: click.echo(item.get("id") or f"ERROR {item['error']}")


@intake_group.group("adapter")
def adapter_group():
    """Validate third-party adapter declarations."""


@adapter_group.command("validate")
@click.argument("manifest", type=click.Path(path_type=Path, exists=True, dir_okay=False))
def cmd_adapter_validate(manifest):
    try: result = validate_adapter(load(manifest))
    except IntakeError as exc: fail(exc)
    click.echo(f"PASS  {result['id']}  {result['version']}")


@intake_group.command("stdio")
def cmd_stdio():
    for line in sys.stdin:
        if not line.strip(): continue
        try: request = json.loads(line)
        except json.JSONDecodeError as exc: response = {"protocol": "axm-intake-stdio/1", "request_id": "", "status": "error", "result": None, "errors": [str(exc)]}
        else: response = handle_stdio(request)
        click.echo(json.dumps(response, ensure_ascii=False, separators=(",", ":")))


def main(): intake_group()
if __name__ == "__main__": main()
