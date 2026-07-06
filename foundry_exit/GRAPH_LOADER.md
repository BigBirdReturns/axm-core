# Foundry Exit — Graph Loader (v0)

Downstream of the sealed bundle. Takes a **genesis-verified** Foundry Exit shard
and recreates a **queryable ontology + lineage graph** — no Palantir, no S3, no
GhostBox, and without turning any Palantir id into custody authority.

```
sealed axm-hybrid1 shard  +  out-of-band trusted key
        │
        ▼  genesis verify  (axm-verify shard <dir> --trusted-key <pub>)
   PASS ─┴─ else → REFUSE (wrong key → FAIL, missing key → NO_TRUSTED_KEY, malformed → MALFORMED)
        │
        ▼  read ONLY content/{ontology,lineage,datasets.manifest,foundry_exit_manifest}
   nodes/edges projection  (external Palantir ids preserved verbatim)
        │
        ▼  foundry_exit_graph.json  +  foundry_exit_graph.cypher  (+ optional FalkorDB)
```

Verification **precedes** any graph read. Nothing is projected from a bundle that
did not verify.

## Nodes and edges

| Node kind | From | External id (verbatim) |
|---|---|---|
| `dataset` | dataset manifest / lineage / backing | dataset RID |
| `object_type` | ontology + link targets | object-type id |
| `property` | ontology `properties` | property id |
| `relationship` | ontology `links` | link id |
| `transform` | lineage `transform_ref` | transform ref |
| `file` | dataset manifest object rows | object path |

| Edge | Meaning |
|---|---|
| `object_type -backed_by-> dataset` | ontology backing dataset |
| `object_type -has_property-> property` | object-property link |
| `object_type -has_relationship-> relationship` | object-relationship link |
| `relationship -targets-> object_type` | link target (when declared) |
| `downstream -derives_from-> upstream` | dataset lineage |
| `upstream -input_to-> transform` / `transform -produces-> downstream` | transform I/O |
| `transform -produces_object_type-> object_type` | transform output type |
| `dataset -has_file-> file` | exported object |

## Run it

```bash
# needs the genesis kernel (axm-verify) on PATH and the out-of-band publisher pub
python -m foundry_exit.run_graph_loader <sealed_shard_dir> \
    --trusted-key <publisher.pub> --out foundry_exit_graph_out
python -m pytest tests/test_foundry_exit_graph_loader.py -q
```

Output is byte-stable: `foundry_exit_graph.json` (nodes/edges/stats + a detached
verification receipt) and deterministic `foundry_exit_graph.cypher` (OpenCypher
`MERGE` statements). A FalkorDB loader (`load_into_falkordb`) is available when the
`falkordb` driver is installed; it is **never a test dependency** and skips cleanly
when absent — the JSON/OpenCypher export is the portable target.

## Boundaries (enforced + tested)

- **Verify before read.** No key → `NO_TRUSTED_KEY`; wrong key → `FAIL`; tampered
  or non-shard → `FAIL`/`MALFORMED`. Every non-PASS verdict refuses **before** any
  ontology/lineage is read (asserted: the content readers are never reached on a
  refused bundle).
- **Custody stays genesis's.** The sealed `sh1_` is the only custody id; no node
  id or external id is ever an `sh1_`. Palantir dataset RIDs, ontology ids, link
  ids, and transform refs are external ids, **verbatim**.
- **Permissions are not portable.** Security markings ride along as node metadata
  only; they never become edges or access control.
- **Downstream only.** The loader imports no `ghostbox`, references no `boto3` /
  S3 adapter / importer, and makes no Palantir or network call. It reads only the
  sealed shard's `content/`.

## Live receipts (this environment)

| Check | Result |
|---|---|
| Verified bundle → graph | **PASS** (9 nodes / 10 edges from the sample) |
| Detached receipt on the export | **PASS** (importer / GhostBox / Palantir / S3 all absent) |
| Wrong key | **refused — FAIL** |
| Missing key | **refused — NO_TRUSTED_KEY** (before any read) |
| Tampered / non-shard bundle | **refused — FAIL / MALFORMED** |
| Ontology + lineage external ids | **verbatim** |
| Graph export + OpenCypher | **byte-deterministic** |
| FalkorDB unavailable | **skips cleanly** (RuntimeError, not ImportError crash) |
| Test suite | **17/17** |

**Evidence tier:** recreate-the-graph-from-a-verified-bundle, proven against a
genesis-sealed sample bundle. This is a downstream loader over the already-proven
seal; it does not import, does not fetch, and does not run live Foundry
extraction.

## Control question

Can AXM take a **genesis-verified** Foundry Exit bundle and recreate a queryable
ontology and lineage graph **without Palantir, without GhostBox, and without
turning external Palantir IDs into custody authority**?

**v0 answer: yes** — verified-first, external ids verbatim, custody left to the
genesis `sh1_`, permissions kept as metadata only.
