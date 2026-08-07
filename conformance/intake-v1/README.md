# AXM intake v1 conformance vectors

A conforming implementation accepts every record under `good/` and rejects every observation under `bad/`.

| Vector | Expected result |
|---|---|
| `good/cloudevents-observation.json` | Valid C4 observation with verified inline payload bytes |
| `good/adapter-manifest.json` | Valid third-party adapter declaration |
| `bad/payload-digest-mismatch.json` | Reject because the representation bytes do not match `payload.sha256` |
| `bad/authority-escalation.json` | Reject because an adapter may declare only `observation_only` |

The executable reference is `axm_core.intake.model`. JSON Schema validates the portable structural subset; the reference validator also checks deterministic identities, exact payload bytes, coverage arithmetic, and authority semantics.
