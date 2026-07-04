"""Foundry Exit Intake v0.

Make AXM ready to import an authorized Palantir Foundry export: dataset bytes
from the S3-compatible data plane, ontology + lineage from the metadata plane,
then seal the whole exit bundle through genesis so the liberated record survives
Palantir, GhostBox, and any AI layer.

Boundaries this package holds:
  - S3 is the dataset-BYTE interface only. Ontology and lineage come from
    explicit metadata inputs (JSON), never from S3.
  - Palantir identifiers are EXTERNAL ids, preserved verbatim; the AXM custody
    id is the genesis-derived ``sh1_`` on the sealed bundle. Palantir ids are
    never AXM custody ids.
  - Genesis owns custody and verification. This package seals through the real
    genesis compiler and verifies with an out-of-band key.
  - GhostBox is NOT in the import path: nothing here imports ``ghostbox``.
  - Read-only against sources: no adapter can write to Palantir or any endpoint.
"""
