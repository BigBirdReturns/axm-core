# Reality-backed structured adapters

Forge exposes deterministic tier-0 adapters in
`axm_forge.ingestion.structured`. They preserve the original file as a
`DocumentBlock` in Genesis v1's canonical source-text form, emit unique
exact-substring evidence against that canonical source, and attach Genesis-sealed
`block_id` locators. Richer line, dependency-group, and JSON Pointer fields are
available before compilation but are not claimed as sealed Genesis output.

The current adapter set is intentionally bounded by real files observed in the
durable `D:\Projects` estate on 2026-07-17:

- package manifests: `package.json`, `pyproject.toml`, `requirements*.txt`,
  and `go.mod`;
- Schema.org JSON-LD files and inline `application/ld+json` scripts.

Run a single structured file:

```powershell
$env:PYTHONPATH='D:\Projects\AXM\axm-core\main\forge'
python -m axm_forge.ingestion.structured extract FILE --out OUT
```

Build a multi-root package graph:

```powershell
$env:PYTHONPATH='D:\Projects\AXM\axm-core\main\forge'
python -m axm_forge.ingestion.structured package-graph ROOT_A ROOT_B --out OUT
```

Pass explicit canonical checkout roots rather than scanning `D:\Projects` as a
whole. The package-graph command writes an empty `source.txt`, an empty
`candidates.jsonl`, and a derived `package-graph.json` containing path-qualified
project nodes plus dependency, build-requirement, and workspace edges. It is an
estate inventory, not a single Genesis compilation unit. Compile individual
manifest extractions so each candidate's evidence is unique within its real
source file.

The graph includes every supported manifest below the supplied roots except
the explicitly excluded cache/generated/custody directories. It does not infer
whether nested templates, vendor trees, archives, or probes are "active"; that
policy belongs to the caller's root selection. Project identifiers use the
shortest unique input-root suffix plus the manifest-relative path, so moving an
unchanged estate to another drive does not rewrite graph identity. Workspace
globs remain graph relations and are not emitted as entity candidates.

OpenAPI, FHIR, EPUB, and Akoma Ntoso are not implemented here. No matching real
source file was present in the durable estate, so those proposed adapters have
not crossed the reality-first entry gate.
