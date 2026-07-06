"""Load a graph from an ALREADY-SEALED Foundry Exit shard.

    sealed shard + out-of-band trusted key
      -> genesis verify (refuse on wrong/missing key or malformed)
      -> project ontology + lineage into a nodes/edges graph
      -> foundry_exit_graph.json (+ foundry_exit_graph.cypher)

Downstream only: no importer, no S3, no Palantir, no GhostBox. Refuses to read
the graph until genesis has verified the sealed bundle.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from foundry_exit.graph_loader import LoadRefused, load_verified_graph, write_graph_export


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Load a graph from a sealed Foundry Exit bundle.")
    ap.add_argument("shard_dir", help="path to the sealed axm-hybrid1 shard directory")
    ap.add_argument("--trusted-key", required=True, help="out-of-band publisher public key")
    ap.add_argument("--out", default="foundry_exit_graph_out")
    ap.add_argument("--no-cypher", action="store_true", help="skip the OpenCypher export")
    args = ap.parse_args(argv)

    try:
        loaded = load_verified_graph(args.shard_dir, args.trusted_key)
    except LoadRefused as exc:
        print(f"REFUSED ({exc.status.value}): {exc}", file=sys.stderr)
        return 1

    written = write_graph_export(loaded, Path(args.out), cypher=not args.no_cypher)
    print(json.dumps({
        "verified": loaded.provenance["verified"],
        "verify_status": loaded.provenance["verify_status"],
        "detached_status": loaded.provenance["detached_status"],
        "stats": loaded.graph["stats"],
        "written": {k: str(v) for k, v in written.items()},
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
