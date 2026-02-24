# AXM CLI Contract

Verbs, arguments, exit codes, and machine-readable output.
Every consumer (NodalFlow, scripts, CI) implements against this contract, not against the source.

---

## Invariants

- CLI imports nothing from Genesis, Forge, or Spectra directly.
- CLI imports Registry only.
- Genesis and Forge are subprocesses. Spectra is an HTTP service.
- Every command that touches a shard resolves through Registry first.
- Human names never reach Genesis or Spectra. Only `shard_id` does.

---

## Configuration

Resolution order (first wins):

1. CLI flag
2. Environment variable
3. `~/.axm/config.json`
4. Default

| Setting       | CLI flag          | Env var           | Default                                   |
|---------------|-------------------|-------------------|-------------------------------------------|
| Spectra URL   | `--spectra-url`   | `AXM_SPECTRA_URL` | `http://localhost:8080`                   |
| Shards dir    | `--shards-dir`    | `AXM_SHARDS_DIR`  | `./shards` if exists, else `~/.axm/shards`|
| Registry path | `--registry`      | `AXM_REGISTRY`    | `./registry/artifacts.json`               |

`~/.axm/config.json` shape:
```json
{
  "spectra_url": "http://localhost:8080",
  "shards_dir": "/data/axm/shards",
  "registry": "/data/axm/registry/artifacts.json"
}
```

---

## Exit codes

| Code | Meaning                                      |
|------|----------------------------------------------|
| 0    | Success                                      |
| 1    | General error (see stderr)                   |
| 2    | Registry error (ref not found, invalid name) |
| 3    | Verification failed (shard tampered/invalid) |
| 4    | Shard not found on disk                      |
| 5    | Spectra unreachable or mount failed          |
| 6    | Build failed (Forge error)                   |

---

## Machine output

All commands accept `--json` flag. When passed, stdout is a single JSON object.
Stderr remains human-readable in all modes.

Success shape:
```json
{ "ok": true, "data": { ... } }
```

Error shape:
```json
{ "ok": false, "error": { "code": 2, "message": "Unknown artifact ref: 'foo/bar'" } }
```

---

## Verbs

---

### `axm resolve <ref>`

Resolve a human ref to a shard_id without side effects.

```
axm resolve medical/fm21-11
axm resolve fm21-11:latest
axm resolve --lock axm.lock.json medical/fm21-11
axm resolve shard_blake3_abc123   # pass-through
```

Flags:
- `--lock <path>` — resolve from lockfile, ignore registry state
- `--json` — machine output

Output (human):
```
shard_blake3_a1b2c3...
```

Output (--json):
```json
{ "ok": true, "data": { "ref": "medical/fm21-11", "shard_id": "shard_blake3_a1b2c3..." } }
```

Exit codes: 0, 2

---

### `axm verify <ref>`

Resolve ref and run Genesis hard verifier against the shard.

```
axm verify medical/fm21-11
axm verify shard_blake3_abc123
```

Flags:
- `--trusted-key <path>` — override policy trust key
- `--json`

Output (human):
```
Resolved 'medical/fm21-11' -> shard_blake3_a1b2c3...
✓ Cryptographic verification passed.
```

Output (--json):
```json
{ "ok": true, "data": { "shard_id": "shard_blake3_a1b2c3...", "verified": true } }
```

Exit codes: 0, 2, 3, 4

---

### `axm mount <ref>`

Resolve, verify, then mount into Spectra. Verify is mandatory by default.

```
axm mount medical/fm21-11
axm mount --no-verify medical/fm21-11     # dev only
axm mount --lock axm.lock.json medical/fm21-11
```

Flags:
- `--no-verify` — skip Genesis verification before mount. Dev/offline only.
- `--lock <path>` — resolve from lockfile
- `--spectra-url <url>`
- `--json`

Output (--json):
```json
{ "ok": true, "data": { "shard_id": "shard_blake3_a1b2c3...", "mount_id": "...", "verified": true } }
```

Exit codes: 0, 2, 3, 4, 5

---

### `axm build <source_doc> --name <canonical_name>`

Ingest, compile, verify, and register a new shard. Calls Forge with `--json` output.

```
axm build ./docs/fm21-11.pdf --name medical/fm21-11
axm build ./corpus/ --name legal/lafc-divorce-2024 --reason "initial compile"
```

Flags:
- `--name <name>` — required. Canonical name to register under.
- `--reason <str>` — audit log reason. Default: `cli build`
- `--shards-dir <path>`
- `--json`

Forge is called as:
```
axm-forge build <source_doc> --out <shards_dir> --json
```

Forge `--json` stdout contract (Forge must implement this):
```json
{ "shard_id": "shard_blake3_a1b2c3...", "path": "/path/to/shard" }
```

CLI reads this JSON. No stdout scraping.

Output (--json):
```json
{
  "ok": true,
  "data": {
    "name": "medical/fm21-11",
    "shard_id": "shard_blake3_a1b2c3...",
    "verified": true
  }
}
```

Exit codes: 0, 2, 6

---

### `axm pin <ref...>`

Snapshot current registry state for listed refs into `axm.lock.json`.

```
axm pin medical/fm21-11 legal/lafc-divorce-2024
axm pin --out custom.lock.json medical/fm21-11
```

Flags:
- `--out <path>` — output path. Default: `axm.lock.json`
- `--json`

Output (--json):
```json
{
  "ok": true,
  "data": {
    "lockfile": "axm.lock.json",
    "pins": {
      "medical/fm21-11": "shard_blake3_a1b2c3...",
      "legal/lafc-divorce-2024": "shard_blake3_d4e5f6..."
    }
  }
}
```

Exit codes: 0, 2

---

### `axm alias <ref> <alias>`

Add a human alias to an existing artifact.

```
axm alias medical/fm21-11 fm21-11:latest
axm alias medical/fm21-11 army/first-aid
```

Exit codes: 0, 2

---

### `axm history <ref>`

Print the lineage of an artifact.

```
axm history medical/fm21-11
```

Output (human):
```
medical/fm21-11
  [0] shard_blake3_a1b2c3...  2026-02-24T00:00:00Z  initial compile
  [1] shard_blake3_d4e5f6...  2026-03-01T00:00:00Z  authority updated: FM 21-11 rev 3
      ^ current
```

Output (--json):
```json
{
  "ok": true,
  "data": {
    "name": "medical/fm21-11",
    "current": "shard_blake3_d4e5f6...",
    "history": [ ... HistoryEntry[] ... ]
  }
}
```

Exit codes: 0, 2

---

### `axm list`

List all registered artifact names.

```
axm list
axm list --tag medical
```

Flags:
- `--tag <tag>` — filter by tag

Exit codes: 0

---

## NodalFlow integration note

NodalFlow must not invent naming rules. It calls `axm resolve` or imports `registry.resolve.Registry` directly. It never constructs shard_ids. It never reads `artifacts.json` directly.
