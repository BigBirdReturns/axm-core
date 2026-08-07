# AXM intake schemas

- `observation-v1.schema.json` defines the pre-shard evidence envelope.
- `adapter-manifest-v1.schema.json` defines third-party adapter declarations.
- `receipt-v1.schema.json` defines durable intake admission receipts.
- `stdio-v1.schema.json` defines the language-neutral JSONL adapter protocol.

The schemas are portable structural contracts. `axm_core.intake.model` is the executable reference for deterministic identities, payload hashing, coverage arithmetic, and authority restrictions that require computation beyond ordinary JSON Schema validation.
