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
# pulls axm-core@v1.0.0 automatically
# which pulls axm-genesis@v1.2.0 automatically
# axm_build.*, axm_verify.* resolve from genesis
```

---

## axm-genesis (kernel)

The frozen cryptographic protocol. Nothing in the stack changes this without an RFC.

| Package | Purpose |
|---------|---------|
| `axm_build` | Compiler, Merkle tree, signing, manifest |
| `axm_verify` | Verifier, error codes, Parquet schemas |
| `axm_extract` | Document ingestion and chunking |
| `axm_judge` | Stream judge interface |

**Key constraint:** `axm_verify.const.ErrorCode` is additive-only. Existing codes are never renamed or removed. Shards from any version must verify under any newer verifier.

---

## axm-core (hub)

Orchestration tooling. Declares `axm-genesis` as a pinned dependency and re-exposes its surface to spokes.

| Component | Location | Purpose |
|-----------|----------|---------|
| **Forge** | `forge/` | Document extraction pipeline (tier 0/1 regex + tier 3 LLM) |
| **Spectra** | `spectra/` | Runtime query engine (DuckDB + SQL gate) |
| **Clarion** | `clarion/` | Topology-bound encryption (GraphKDF) |
| **Nodal Flow** | `nodalflow/` | Desktop UI (Tauri + Svelte + DuckDB) |

**Key constraint:** Forge, Spectra, and Clarion each have their own `pyproject.toml`. Install them separately if needed. The root `pip install axm-core` exposes only the registry package and the transitive genesis dependency.

```bash
pip install -e .          # axm-core root + axm-genesis
pip install -e ./forge    # axm-forge CLI
pip install -e ./clarion  # topology-bound encryption
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
    Handles: manifest, Parquet schemas, Merkle tree, signing, self-verification.

Step 3 — Inject binary files and reseal  (only if spoke has binary content files)
    shutil.copy2(binary, shard / "content" / "binary.bin")
    new_root = compute_merkle_root(shard, suite=suite)
    # Rewrite manifest root, re-sign, re-verify.
    # See axm-embodied::_inject_latents_and_reseal() for the pattern.

Step 4 — Write domain extension data to ext/  (optional)
    pq.write_table(table, shard / "ext" / "yourdata@1.parquet")
```

---

## Data flow

```
Source document / sensor stream / API response
        ↓
  [Spoke: Step 1 — domain extraction]
        ↓ candidates.jsonl
  [axm_build.compiler_generic — Step 2]
        ↓ shard/ (PASS without binary files)
  [Spoke: Step 3 — binary inject + reseal]  (if needed)
        ↓ shard/ (PASS with binary files)
  [Spoke: Step 4 — ext/ domain data]
        ↓ ext/streams@1.parquet etc.
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
| Shard layout | `axm_verify/const.py` `REQUIRED_*` | v1.0.0 |
| Merkle construction | `axm_build/merkle.py` | v1.0.0 |
| Parquet schemas | `axm_verify/const.py` `*_SCHEMA` | v1.0.0 |
| Identity computation | `axm_verify/identity.py` | v1.0.0 |
| Error code names | `axm_verify/const.py` `ErrorCode` | v1.0.0 (additive only) |
| Gold shard bytes | `axm-genesis/shards/gold/` | v1.0.0 |
| Binary stream format | `axm_embodied_core/protocol.py` | v1.2.0 |
