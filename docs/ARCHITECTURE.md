# Updated Architecture Diagram (Clean Dependency Lines)

              ┌──────────────────────────────────────────┐
              │                Nodal Flow                │
              │   UI consumes verified claims + proofs   │
              └───────────────▲──────────────────────────┘
                              │
                              │ (verified claims + provenance)
                              │
              ┌───────────────┴──────────────────────────┐
              │                 Spectra                   │
              │  - Mount shard (or decrypt envelope)      │
              │  - Calls Genesis verify (in-process)      │
              │  - Loads parquet into DuckDB / SQL        │
              └───────────────▲──────────────────────────┘
                              │
                              │ (optional encryption boundary)
                              │
              ┌───────────────┴──────────────────────────┐
              │                 Clarion                   │
              │  - Clarion v2.0 envelope.json + blobs/    │
              │  - GraphKDF topology hash v3              │
              │  - AAD binds: envelope_id, shard_id,      │
              │    color, path, plaintext_hash, topo_hash │
              └───────────────▲──────────────────────────┘
                              │
                              │ (Genesis-compliant shard dir)
                              │
              ┌───────────────┴──────────────────────────┐
              │                  Forge                    │
              │  - Ingest source                          │
              │  - Extract candidates                      │
              │  - Calls Genesis compiler (no custom shard)│
              │  - Optional: calls Clarion encrypt         │
              └───────────────▲──────────────────────────┘
                              │
                              │ (frozen spec + verifier)
                              │
              ┌───────────────┴──────────────────────────┐
              │                  Genesis                  │
              │  - Frozen shard contract                   │
              │  - Merkle root + Ed25519 signature         │
              │  - Required parquet schemas                │
              └───────────────────────────────────────────┘
