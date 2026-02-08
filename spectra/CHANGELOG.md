# Changelog

## [0.3.1] - 2026-01-06

### The "Constitution + Transport" Fix
This release re-anchors Spectra on the AXM Genesis constitution. Spectra mounts only Genesis-conforming shards. When given Clarion, Spectra decrypts to a byte-perfect Genesis shard, runs `axm-verify`, then loads.

### New Features
* **Clarion Transport Adapter**: Supports Clarion Envelope v1.0 as an input transport format.
* **Transport Integrity Enforcement**: Validates ciphertext blob hashes, optional files table digest, and binds decryption to per-file AAD.
* **Strict Genesis Identity**: Derives identity and `mount_id` from `manifest.json` fields mandated by Genesis (`spec_version`, `shard_id`, `integrity.merkle_root`).

### Operational Hardening
* **Strict Constitution Gate**: Fails hard when `axm-verify` is missing unless `SPECTRA_DEV_MODE=1` or `SPECTRA_ALLOW_LAYOUT_FALLBACK=1` is set.
* **Boot Transport Awareness**: Allows public Genesis mounts with no secret, while requiring secrets for Clarion mounts.

## [0.3.0] - 2026-01-06

### The "Operating System" Update
This release transitions Spectra from a stateless runtime kernel to a persistent Operating System. It introduces the **System Catalog**, enabling state recovery after restarts, encrypted secret storage, and foundational identity structures.

### New Features
* **Persistence & Recovery**: Mounts now survive process restarts. The new `boot()` sequence rehydrates the runtime from `spectra.db`.
* **System Catalog**: A persistent SQLite store for Mounts, Users, API Keys, and Policy (ACLs).
* **Secure Vault**: Shard secrets are encrypted at rest using `Fernet` (AES-128-CBC) derived from a boot-time `SPECTRA_SYSTEM_KEY`.
* **Health API**: New `GET /health` endpoint reports catalog connectivity, error states, and uptime.
* **System Audit**: Critical lifecycle events (mount, unmount, boot failures) are now logged durably to the `system_events` table.

### Operational Hardening
* **Concurrency**: Applied `PRAGMA busy_timeout = 5000` and `WAL` mode to prevent "database is locked" errors under multi-worker load.
* **Worker Isolation**: `SPECTRA_CACHE_PATH` and `SPECTRA_AUDIT_PATH` now support `{pid}` templating for safe multi-process deployments.
* **Path Safety**: All filesystem paths are now normalized (`expanduser`, `resolve`) to prevent CWD ambiguity.
* **Boot Guards**: The boot process pre-validates shard paths and secrets before attempting mount to prevent crash loops.

### Breaking Changes
* **New Requirement**: `SPECTRA_SYSTEM_KEY` environment variable is now **mandatory** for production (unless `SPECTRA_DEV_MODE=1` is set).
* **Dependency**: Added `cryptography` package requirement.
* **Behavior**: `mount_shard` operations are now persistent by default. To unmount securely, use the `/unmount` API.
