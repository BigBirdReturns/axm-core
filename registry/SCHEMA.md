# AXM Registry Schema

Human contract for `artifacts.json` and `axm.lock.json`.
The machine-readable schema is `registry/schema.json`.

---

## Invariant

Registry can move pointers. Registry never rewrites shards.
Supersession is pointer movement plus lineage edges, not shard mutation.

---

## artifacts.json

Backing store for the Registry. File-backed now, promotable to SQLite or a shard later.

```json
{
  "artifacts": {
    "<canonical_name>": { ... ArtifactEntry ... }
  }
}
```

### Canonical name format

`namespace/slug`

- lowercase, alphanumeric, hyphens, underscores only
- namespace groups related corpora: `medical`, `legal`, `army`, `lafc`
- slug identifies the specific corpus: `fm21-11`, `case-xyz`, `hemorrhage`

Examples: `medical/fm21-11`, `legal/lafc-divorce-2024`, `army/roe-v3`

---

### ArtifactEntry

| Field     | Type             | Required | Notes                                              |
|-----------|------------------|----------|----------------------------------------------------|
| `name`    | string           | yes      | Canonical name. Must match its key in `artifacts`. |
| `current` | string           | yes      | Active `shard_id`. Only mutable field.             |
| `history` | HistoryEntry[]   | yes      | Append-only. Never delete entries.                 |
| `aliases` | string[]         | no       | Additional refs that resolve to this artifact.     |
| `tags`    | string[]         | no       | Freeform labels for filtering.                     |
| `policy`  | Policy           | no       | Authority selection rules.                         |

```json
{
  "name": "medical/fm21-11",
  "current": "shard_blake3_a1b2c3...",
  "history": [ ... ],
  "aliases": ["army/first-aid", "fm21-11:latest"],
  "tags": ["medical", "army", "field-manual"],
  "policy": {
    "trust_key": "keys/canonical_test_publisher.pub",
    "require_verified": true
  }
}
```

---

### HistoryEntry

Append-only record of every time `current` changed.

| Field          | Type   | Required | Notes                                              |
|----------------|--------|----------|----------------------------------------------------|
| `shard_id`     | string | yes      | `shard_blake3_<hex>`                               |
| `timestamp`    | string | yes      | RFC 3339 UTC                                       |
| `reason`       | string | yes      | Human-readable. Why this shard became current.     |
| `compiler`     | string | no       | e.g. `forge@1.0.0`                                 |
| `spec_version` | string | no       | Genesis spec version. e.g. `1.0.0`                 |

Reason vocabulary (convention, not enforced):
- `initial compile`
- `authority updated: <source> <edition>`
- `correction: <claim_id> was wrong`
- `re-extraction: new tier-3 model`
- `key rotation`

```json
{
  "shard_id": "shard_blake3_a1b2c3...",
  "timestamp": "2026-02-24T00:00:00Z",
  "reason": "initial compile",
  "compiler": "forge@1.0.0",
  "spec_version": "1.0.0"
}
```

---

### Policy

Optional. Controls how the CLI and runtime treat this artifact.

| Field              | Type    | Notes                                              |
|--------------------|---------|----------------------------------------------------|
| `trust_key`        | string  | Path to trusted public key for Genesis verification|
| `require_verified` | boolean | If true, mount is blocked until verify passes.     |

---

## axm.lock.json

Pinned snapshot of name → shard_id at a point in time.
Used for reproducible runs. Registry state cannot move these feet.

```json
{
  "pinned_at": "2026-02-24T00:00:00Z",
  "pins": {
    "medical/fm21-11": "shard_blake3_a1b2c3...",
    "legal/lafc-divorce-2024": "shard_blake3_d4e5f6..."
  }
}
```

| Field       | Type   | Notes                                   |
|-------------|--------|-----------------------------------------|
| `pinned_at` | string | RFC 3339 UTC timestamp of pin creation. |
| `pins`      | object | name → shard_id. All values immutable.  |

### Rules

- `axm pin <ref...>` writes this file.
- `axm mount --lock axm.lock.json` mounts the exact pinned set, ignoring registry state.
- `axm resolve --lock axm.lock.json <name>` resolves from pins, not from `artifacts.json`.
- Lockfile is checked into version control. It is the reproducibility guarantee.

---

## shard_id format

`shard_blake3_<lowercase_hex_merkle_root>`

This is the only identifier Genesis cares about. Everything above is a pointer to this.
