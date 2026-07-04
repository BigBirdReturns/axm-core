# AXM Foundry Exit Intake v0 — reviewable packet

**Claim:** A liberated Foundry record: sealed through genesis, survives Palantir, GhostBox, and the importer.

- bundle shard id: `sh1_911dc6b61d5dc4480cfaa2d897216e4605225fef27401a89b2501c73f1435088`
- suite: `axm-hybrid1` · merkle_root: `276bbee18bfa280bd48eecf4d372d4877ee4748791c557e9cda1a40a6a82b45c`
- trusted key: `/tmp/foundry_exit_v0_fglq8so2/keys/publisher.pub` (out-of-band)
- verification: **pass** via `axm-verify shard <dir> --trusted-key <oob_pub> (real genesis kernel)`

## Datasets exported (with checksums)
- `ri.foundry.main.dataset.orders` — 1 object(s), sha256 ['7fdff1bdcc231787991339f939beebed8f56140217e1e2f9afe00b610386fa2c']
- `ri.foundry.main.dataset.raw_orders` — 0 object(s), sha256 []

## Ontology object types preserved
- ['Order']

## Lineage edges preserved
- `ri.foundry.main.dataset.raw_orders` → `ri.foundry.main.dataset.orders` (transform: `ri.foundry.main.transform.clean_orders`)

## External ids (Palantir), carried verbatim
- ['Order', 'ri.foundry.main.dataset.orders', 'ri.foundry.main.dataset.raw_orders', 'ri.foundry.main.transform.clean_orders']

## Boundary notes
- Palantir dataset RIDs / ontology object-type IDs are EXTERNAL ids, carried verbatim; they are NOT AXM custody ids (custody id is the genesis sh1_ on the sealed bundle).
- S3 is the dataset-byte interface only -- it does not import the ontology; ontology and lineage came from explicit metadata inputs.
- GhostBox is not in the import path: no ghostbox code is imported or called by the importer.
- The importer is read-only against sources: no write path to Palantir or any endpoint exists.
- Security markings are recorded for provenance only -- no Palantir permissions were made portable.

## Exit test — record survives Palantir, GhostBox, and the importer
- importer involved: **False** · ghostbox involved: **False**
- detached verify status: **PASS** (exit 0)
- verified with only the sealed shard bytes + the out-of-band public key.
