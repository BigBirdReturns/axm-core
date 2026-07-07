# AXM Architecture

```
axm-genesis  ←  axm-core  ←  axm-embodied
  kernel          hub           spoke
                           ←  axm-<future>
```

---

## Dependency rules

- Spokes depend on `axm-core`. `axm-core` declares `axm-genesis` as a pinned dependency.
- Spokes never import `axm-genesis` directly and never vendor it.
- `axm-core` never imports any spoke.
- `axm-genesis` has no dependencies on `axm-core` or any spoke.

Install chain:

```bash
pip install axm-embodied
# pulls axm-core@v1.1.0 automatically
# which pulls the pinned axm-genesis commit automatically
# axm_build.*, axm_verify.* resolve from genesis
```

---

## axm-genesis (kernel)

The frozen cryptographic protocol. Nothing in the stack changes this without an RFC.

| Package | Purpose |
|---------|---------|
| `axm_build` | Compiler, Merkle tree, axm-hybrid1 signing, manifest |
| `axm_verify` | Verifier, error codes, canonical JSONL table schemas, identity (`recompute_entity_id`/`recompute_claim_id`) |

The kernel is **only** `axm_build` + `axm_verify`. Document ingestion, extraction,
chunking, and stream judging are **not** part of genesis — they live in core/forge
(see Forge below).

**Key constraint:** `axm_verify.const.ErrorCode` is additive-only. Existing codes are never renamed or removed. Within the frozen v1 major, a shard compiled under any 1.x verifies under every other 1.x. (Pre-v1 shards are v0.x prototypes; git history keeps them, v1 verifiers reject them.)

---

## axm-core (hub)

Orchestration tooling. Declares `axm-genesis` as a pinned dependency and re-exposes its surface to spokes.

| Component | Location | Purpose |
|-----------|----------|---------|
| **Forge** | `forge/` | Document extraction pipeline (tier 0/1 regex + tier 3 LLM) |
| **Spectra** | `spectra/` | Runtime query engine (DuckDB + SQL gate) |
| **Clarion** | `clarion/` | Topology-bound encryption (GraphKDF) |
| **Foundry Exit** | moved to [GhostBox](https://github.com/BigBirdReturns/GhostBox) | Palantir exit (ontology/pipeline/logic/residual, `axm-exit-ship`) — now on the GhostBox spoke; depends on core only for Spectra query |
| **Nodal Flow** | separate repo | Desktop UI (Tauri + Svelte + DuckDB) — not in this repo |

**Key constraint:** Forge, Spectra, and Clarion each have their own `pyproject.toml`. Install them separately if needed. The root `pip install axm-core` exposes only the registry package and the transitive genesis dependency.

**Note:** the Foundry exit was extracted to the GhostBox spoke (2026-07). Core no longer packages `foundry_exit`; `[tool.setuptools.packages.find]` discovers only `axm_core*`, `axm_forge*`, `axiom_runtime*`. GhostBox depends on core for the Spectra query proof — the correct spoke → hub direction.

```bash
pip install -e .          # axm-core root + axm-genesis
pip install -e ./forge    # axm-forge CLI
pip install -e ./clarion  # topology-bound encryption (encrypt/decrypt needs graphkdf, not on PyPI)
```

---

## Spokes

Each spoke is an independent repo that:

1. Declares `axm-core` (not `axm-genesis`) as its hub dependency
2. Contains an `axm_<spoke>_core` package for domain-local constants and identity functions
3. Delegates all shard construction to `compile_generic_shard` from `axm_build`
4. Self-verifies: every compiled shard must pass `axm-verify` or compilation fails

### `axm_<spoke>_core` pattern

Every spoke has a local Python package for things that are genuinely its own:

```
axm-embodied/src/axm_embodied_core/
  protocol.py    # binary format constants (AXLF, AXLR, AXRR, LATENT_DIM, ...)
  ids.py         # span_id, prov_id — no genesis equivalent; frozen
  __init__.py
```

This package never duplicates genesis. `entity_id` and `claim_id` always delegate to `axm_verify.identity`. Only the spoke-specific functions (byte-range identity, domain magic bytes) stay local.

A future `axm-financial` spoke would have `axm_financial_core/` containing its own format constants and any financial-domain-specific identity functions. The pattern is identical.

### Spoke compile pattern (four steps)

```
Step 1 — Extract domain data → candidates.jsonl
    Parse your primary artifact into (subject, predicate, object, object_type, tier, evidence) tuples.
    This is the only domain-specific code.

Step 2 — Compile via genesis
    compile_generic_shard(CompilerConfig(...))
    Handles: manifest, canonical JSONL tables, Merkle tree, axm-hybrid1
    signing, self-verification — AND the kernel-registry ext/ tables
    (lineage@1, references@1, temporal@1, locators@1) from candidate keys.

Step 3 — Domain extension data rides in candidates  (optional)
    # v1: only the compiler writes into a shard. Per-candidate keys
    # (locator, references, valid_from/valid_until/temporal_context) become
    # sealed ext/<name>@<version>.jsonl tables; manifest.extensions is the
    # closed list of what the compiler emitted. Anything else your domain
    # derives is a runtime cache OUTSIDE the shard directory.
```

---

## Data flow

```
Source document / sensor stream / API response
        ↓
  [Spoke: Step 1 — domain extraction]
        ↓ candidates.jsonl
  [axm_build.compiler_generic — Step 2]
        ↓ shard/ — canonical JSONL tables + compiler-emitted ext/*.jsonl
  [axm_verify.logic — self-verification gate]
        ↓ status: PASS
  [Clarion] → encrypted envelope  (optional)
  [Spectra] → mounted, queryable
  [Nodal Flow] → UI
```

---

## What is frozen

| Item | Location | Frozen since |
|------|----------|-------------|
| Shard layout | `spec/v1/SPECIFICATION.md` §4, `axm_verify/logic.py` | v1.0.0 (RFC 0002) |
| Merkle construction | `axm_build/merkle.py` | v1.0.0 (RFC 0002) |
| Canonical JSONL table schemas | `axm_verify/const.py` `*_SCHEMA` | v1.0.0 (RFC 0002) |
| `axm-hybrid1` suite | `axm_build/sign.py`, spec §7 | v1.0.0 (RFC 0002) |
| Identity computation | `axm_verify/identity.py` | v1.0.0 (RFC 0002) |
| Error code names | `axm_verify/const.py` `ErrorCode` | v1.0.0 (RFC 0002) |
| Gold shard bytes | `axm-genesis/shards/gold/fm21-11-hemorrhage-v2` | v1.0.0 (RFC 0002) |
| Binary stream format | `axm_embodied_core/protocol.py` (profile `embodied@1`) | profile-versioned |
