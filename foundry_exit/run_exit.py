"""Run Foundry Exit Intake v0 end to end against a local fixture.

    fixture (S3-compatible data plane + metadata plane)
      -> read-only import (dataset bytes + explicit ontology/lineage)
      -> assemble the AXM exit bundle
      -> seal through genesis (real axm-build)
      -> verify with an out-of-band key
      -> reviewable packet
      -> exit test (verify with only shard bytes + oob pub; no Palantir, no
         GhostBox, no importer).
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from foundry_exit.adapters import FilesystemExportSource
from foundry_exit.bundle import build_bundle
from foundry_exit.exit_test import verify_detached
from foundry_exit.importer import FoundryExitImporter, load_json
from foundry_exit.packet import build_packet, render_markdown, to_json
from foundry_exit.seal import kernel_available, seal_exit_bundle, verify_exit_bundle

DEFAULT_FIXTURE = Path(__file__).resolve().parent.parent / "samples" / "foundry_exit_fixture"


def run(fixture: Path, out_dir: Path) -> dict:
    if not kernel_available():
        raise SystemExit(
            "axm-genesis kernel not on PATH (need `axm-build` and `axm-verify`).\n"
            "Install it, e.g.:  pip install -e '/path/to/axm-genesis[dev]'"
        )
    work = Path(tempfile.mkdtemp(prefix="foundry_exit_v0_"))

    # 1) read-only import: dataset bytes from the (filesystem) data plane;
    #    ontology + lineage from explicit metadata inputs.
    source = FilesystemExportSource(fixture)
    importer = FoundryExitImporter(source, stage_dir=work / "staged")
    manifest = importer.import_export(
        inventory=load_json(fixture / "inventory.json"),
        ontology=load_json(fixture / "ontology.json"),
        lineage=load_json(fixture / "lineage.json"),
    )

    # 2) assemble the bundle, 3) seal through genesis
    bundle_dir = build_bundle(manifest, work / "bundle")
    sealed = seal_exit_bundle(manifest, bundle_dir, work / "shard")

    # 4) verify with the out-of-band key
    status = verify_exit_bundle(sealed.shard_dir, sealed.trusted_key_path)

    # 6) exit test: verify with only shard bytes + oob pub (no importer/ghostbox/palantir)
    exit_result = verify_detached(sealed.shard_dir, sealed.trusted_key_path)

    # 5) packet
    packet = build_packet(manifest, sealed, status, exit_test=exit_result)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "foundry_exit_packet.json").write_text(to_json(packet), encoding="utf-8")
    (out_dir / "foundry_exit_packet.md").write_text(render_markdown(packet), encoding="utf-8")
    return packet


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run AXM Foundry Exit Intake v0.")
    ap.add_argument("--fixture", default=str(DEFAULT_FIXTURE))
    ap.add_argument("--out", default="foundry_exit_out")
    args = ap.parse_args(argv)
    packet = run(Path(args.fixture), Path(args.out))
    print(render_markdown(packet))
    et = packet["exit_test"]
    ok = (
        packet["verification"]["status"] == "pass"
        and et["status"] == "PASS"
        and et["importer_involved"] is False
        and et["ghostbox_involved"] is False
    )
    print(f"[foundry exit v0: {'OK' if ok else 'INCOMPLETE'} — "
          f"verified={packet['verification']['status']}, detached={et['status']}, "
          f"importer-in-exit={et['importer_involved']}, ghostbox-in-exit={et['ghostbox_involved']}]")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
