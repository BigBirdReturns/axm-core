# AXM: Verifier/Forge Extension Status, Roadmap to Demo, Deprecated Code Audit

---

## Part 1: Extension System Status (What's Solid, What's Not)

### DONE — Verified and Tested

**Verifier patch** (`genesis/src/axm_verify/logic.py` line 89):
```python
extra = (items - REQUIRED_ROOT_ITEMS) - {"ext"}
```
- Empty `ext/` passes verification ✓
- `ext/` with files passes when re-signed (Merkle covers them) ✓
- `ext/` with files fails WITHOUT re-signing (Merkle mismatch — correct) ✓
- Junk root dirs (`tmp/`, `cache/`, `foo/`) still rejected with E_LAYOUT_DIRTY ✓
- Gold shard (no ext/) backward compatible ✓
- 10 new tests in `tests/test_extensions.py`, all passing ✓

**Compiler patch** (`genesis/src/axm_build/compiler_generic.py`):
- Creates `ext/` directory alongside content, graph, evidence, sig ✓
- Detects files in ext/ and adds `"extensions": ["name@1"]` to manifest ✓
- When ext/ is empty, manifest has NO extensions key (hash stability) ✓

### NOT DONE — Described in Prior Session But Not Implemented

**Forge Locator bbox field**: The prior session discussed adding `bbox_norm: Optional[Tuple[float,float,float,float]]` to the Forge `Locator` dataclass for spatial data. NOT applied to the code. The Locator in `forge/axm_forge/models/types.py` has: `kind`, `page`, `paragraph_index`, `file_path`, `block_id`. No bbox.

**ext/ schema definitions**: No schema exists for what goes inside ext/. The envelope works (verifier allows it, Merkle covers it) but there's no `ext/spatial.parquet` schema, no `ext/temporal.parquet` schema, no `ext/constraints.parquet` schema. The mechanism exists. The schemas don't.

**Extension naming convention**: The manifest uses `name@version` strings (e.g., `"spatial@1"`). But there's no spec defining what "spatial@1" means, what columns it has, or what "version 2" would change. No registry pattern.

**Compiler extension hooks**: The compiler creates ext/ and detects files in it, but has no codepath to actually WRITE extension files. Extensions would currently need to be added manually after compilation and before signing.

### What This Means

The foundation is real. The verifier allows extensions without breaking backward compatibility. The Merkle tree covers extension data. The compiler creates the directory. But there's nothing that actually PRODUCES extension data yet. The pipe exists; nothing flows through it.

---

## Part 2: Roadmap to "Ingest Anything" Demo

Goal: A single command that takes any structured input (Wikipedia article, PDF, DOCX, web page, notes, CSV, XBRL), produces a signed verified shard, and lets you query it with an LLM where every answer traces to source bytes.

### What Works End-to-End Right Now

`nodal_run.py` does: Wikipedia article → fetch → segment → extract (Ollama tier3) → bind to byte spans → doctor validation → Genesis compile → verify → signed shard + review HTML.

This is a working pipeline for ONE input type (Wikipedia plaintext via API). The gaps are below.

### Phase 1: Universal Ingestion (Forge)

**Current state**: `forge/axm_forge/ingestion/universal.py` reads `.txt` files. That's it. No PDF, no DOCX, no HTML, no structured data.

**What needs to exist**:

A. **Text extraction layer** — takes any file, returns normalized UTF-8 text + a Locator per chunk. Uses:
   - `pdfplumber` or `pymupdf` for PDF (text + page numbers + optional bbox)
   - `python-docx` for DOCX (paragraphs + paragraph index)
   - `beautifulsoup4` for HTML (stripped text + element path)
   - `openpyxl` for XLSX (cell references)
   - Passthrough for .txt/.md
   - Optional: `tesseract` OCR fallback for scanned PDFs

   This is a single module: `forge/axm_forge/ingestion/extractors.py`. Each extractor returns `List[Chunk]` with proper Locators. The `universal.py` dispatcher routes by file extension.

B. **Structured data adapters** — for formats where schema IS the extraction:
   - XBRL → entities (companies) + claims (financial facts) + provenance (filing reference) at tier 0 (rule-based, confidence 1.0)
   - CSV with headers → entities (column names) + claims (cell values) at tier 0
   - JSON with known schema → direct mapping

   These skip the LLM entirely. The schema defines the claims. This is the insight from the deprecated `intake.py`: "When schema exists, we don't need to extract."

C. **`nodal_run.py` generalized** — currently hardcoded for Wikipedia. Needs a `--source` flag that accepts any file path, routes through the right extractor, and runs the same downstream pipeline. The pipeline stages (segment, extract, bind, compile, verify) don't change. Only ingestion changes.

**Effort**: Medium. The extractors are thin wrappers around existing libraries. The routing is file-extension dispatch. The downstream pipeline is already working.

### Phase 2: Multi-Shard Mounting (Spectra)

**Current state**: Spectra's `engine.py` mounts one shard. Nodal Flow's `vault.rs` mounts one shard.

**What needs to exist**:

A. **Spectra multi-mount** — `engine.mount(shard_path, shard_id)` registers parquet views with `{shard_id}_entities`, `{shard_id}_claims`, etc. Query results include shard_id so you know which source each claim came from. DuckDB supports this natively — it's just view registration.

B. **Nodal Flow multi-mount** — `vault.rs` `mount_shard` called N times, each creating namespaced DuckDB views. `query_vault` searches across all mounted shards. Results tagged with shard origin.

C. **Shard catalog** — lightweight manifest index. Read manifests from a directory of shards, build a searchable list (shard_id, title, namespace, created_at, entity count, claim count). Used by Nodal Flow to show available shards and let the user mount/unmount them. No new format — just reads existing manifest.json files.

**Effort**: Small for Spectra (it's SQL view registration). Medium for Nodal Flow (Rust changes to vault.rs). The catalog is trivial.

### Phase 3: Hallucination Firewall (Spectra/Nodal Flow)

**Current state**: `query_ollama` in Nodal Flow sends claims as context and asks the LLM to cite sources. Nothing verifies the LLM actually did.

**What needs to exist**:

Post-processing on LLM output that:
1. Parses citation markers (e.g., `[1]`, `[2]`) from the response
2. Maps each citation to the claims that were injected into the prompt
3. Checks that every factual sentence in the output has a valid citation
4. Flags or strips sentences that contain claims not traceable to mounted shards
5. If the LLM hallucinates (says something not in any shard), the firewall catches it

This is the `enforce_provenance_contract()` function from the deprecated kid_local_tutor, rebuilt for the Genesis stack. The logic is the same: scan sentences for citations, validate citations map to injected context, reject uncited claims.

**Effort**: Small. The logic already exists in deprecated code. Needs adaptation to Spectra's data model.

### Phase 4: Decision Shard Loop

**Current state**: Queries go in, answers come out, nothing persists.

**What needs to exist**:

A. **Interaction logger** — after each query, serialize `{query, response, cited_claim_ids, cited_shard_ids, timestamp, user_context}` into a structured JSONL record.

B. **Decision Forge adapter** — takes interaction JSONL, converts each record into Genesis candidates where:
   - Subject: the query (or a derived entity)
   - Predicate: "decided", "recommended", "cited"
   - Object: the response claim or cited source
   - Evidence: the full response text
   - Tier: 2 (LLM-derived)
   This feeds into the existing compiler. The decision shard gets signed and verified like any other shard.

C. **Cross-shard references** — decision claims that cite reference shard claims need a way to express "this decision was based on claim X from shard Y." This is metadata on the claim, stored in ext/. Not a spec change.

**Effort**: Medium. The logger is trivial. The Forge adapter is a new emission path. Cross-shard references need ext/ schema design.

### Phase 5: Demo Script

When phases 1-3 are done, the demo is:

```bash
# Compile a PDF into a verified shard
python nodal_run.py --source docs/fm21-11-chapter4.pdf --out shards/fm21-11

# Mount it and query with an LLM
python demo_query.py --shards shards/fm21-11 --question "When should I apply a tourniquet?"

# Output:
# > A tourniquet should be used when direct pressure over the wound fails
# > to control bleeding. [1]
# >
# > [1] FM 21-11, Chapter 4, Section 4-9(b), bytes 12847-12923
# >     "Use a tourniquet only when direct pressure has failed..."
# >
# > ✓ All claims verified against mounted shards
# > ✗ 0 hallucinations detected
```

That's the "it runs bash and gcc" moment.

---

## Part 3: Deprecated Code Audit — What's Usable, Where It Goes

### axiom-knowledge-core-main/packages/axm/

**Coordinate system** (`coords.py`, 282 lines)
- 8 major categories: Entity, Action, Property, Relation, Location, Time, Quantity, Abstract
- 4-dimensional addressing: `[major, type, subtype, instance]`
- Frozen at v0.4
- **WHERE IT GOES**: This is an ext/ extension. `ext/coords.parquet` with columns `(entity_id, major, type, subtype, instance)`. Every entity in the graph gets a coordinate. Enables geometric queries ("give me all quantities") without scanning claims. This is the addressing scheme that makes Space queries work.

**IR primitives** (`ir.py`, 467 lines)
- Immutable, content-addressable Node, Relation, Provenance, Fork, Derivation, TemporalAlignment
- Strict validation on all `from_dict()` methods
- Source offsets (`source_start`, `source_end`) on Provenance — already maps to Genesis spans
- **WHERE IT GOES**: Genesis already has entities, claims, provenance, spans. The IR maps 1:1. The extra types (Fork, Derivation, TemporalAlignment) become ext/ extensions. The strict validation patterns should inform Genesis schema validation.

**Space query engine** (`space.py`, 405 lines)
- "Pure geometry, no LLM" query over compiled programs
- Indexes by major category, outgoing/incoming relations
- Graph traversal (BFS/DFS), path finding, subgraph extraction
- Deterministic keyword matching
- **WHERE IT GOES**: This is Spectra functionality. Space queries over DuckDB-mounted shards. The coordinate index (from ext/coords.parquet) enables the major-category filtering. The graph traversal works over claims-as-edges. Rebuild on top of Spectra's DuckDB engine instead of in-memory Python dicts.

**Derivation engine** (`derive/engine.py`, `strategies.py`, `temporal.py`, `confidence.py`)
- Plugin system for derivation passes that transform Programs
- Temporal alignment: detects time-reference nodes in chunks, creates "at" edges connecting facts to their temporal context
- Confidence summary: computes per-chunk confidence from provenance confidence values
- **WHERE IT GOES**: These become Forge post-processing passes. After extraction, before compilation. The temporal pass detects date/time references and creates temporal claims. The confidence pass aggregates extraction confidence. Results go into ext/ extension tables. The strategy plugin pattern is good architecture — keep it for adding new derivation types.

**Intake router** (`intake.py`, 943 lines)
- Source type detection (XBRL, FHIR, JSON-LD, iCal, RSS, PDF, DOCX, XLSX, PPTX, CSV, HTML, markdown, text)
- PDF extraction (pdfplumber/PyPDF2 fallback)
- DOCX extraction (python-docx)
- XLSX extraction (openpyxl)
- PPTX extraction (python-pptx)
- XBRL adapter (parses SEC filings into entities + financial facts)
- iCal adapter (calendar events → entities)
- RSS adapter (feed items → entities)
- Structured vs unstructured routing: "when schema exists, we don't need to extract"
- **WHERE IT GOES**: This IS Phase 1 of the roadmap. The extractors port directly into `forge/axm_forge/ingestion/extractors.py`. The source type detection becomes the router. The key insight ("schema IS extraction, confidence = 1.0") drives the structured adapter path where XBRL/CSV/JSON skip the LLM tier entirely and produce tier-0 candidates.

**Compiler** (`compiler.py`, 529 lines)
- Lexer → Parser → LLM extraction → Emitter pipeline
- Incremental compilation with chunk-level change detection
- Configurable executors (mock, retry, Ollama)
- **WHERE IT GOES**: The incremental compilation concept is valuable for decision shards that update frequently — detect which chunks changed, only re-extract those. The executor pattern (mock for tests, retry for reliability, Ollama for real) maps to Forge's tier system. The chunk-level change detection should be extracted and adapted for Forge's extraction pipeline.

**Chat module** (`chat.py`, 446 lines)
- Keyword extraction from natural language questions
- Question type classification (quantity, time, entity, location)
- Context selection (query Space, format top-K results)
- Prompt construction with system/user roles
- Citation generation and validation
- Multi-turn conversation with follow-up detection
- **WHERE IT GOES**: The keyword extraction and question classification improve Spectra's query routing — instead of raw text search, classify the question type and route to the appropriate coordinate-space query. The citation generation and validation logic feeds the hallucination firewall. The prompt construction patterns inform Nodal Flow's `build_context`.

### axiom-knowledge-core-main/apps/kid_local_tutor/

**CompiledStore** (`compiled_store.py`, 143 lines)
- Deterministic keyword-overlap retrieval over compiled JSONL artifacts
- No embeddings, no vectors — pure token intersection scoring
- Wraps results in SearchResult with distance and metadata
- **WHERE IT GOES**: The retrieval logic maps to a Spectra query mode. DuckDB full-text search replaces the in-memory token matching. The concept of "compiled substrate retrieval" (deterministic, no embeddings) is the right default for offline/edge — embeddings are optional enhancement, not requirement.

**Provenance enforcement** (`provenance.py`, 154 lines)
- `validate_provenance_contract()`: scans LLM output for uncited factual claims
- Sentence splitting, citation parsing, claim detection heuristics
- `enforce_provenance_contract()`: replaces uncited responses with "cannot answer from this local library"
- Source block generation: appends formatted source references to valid responses
- **WHERE IT GOES**: This IS the hallucination firewall (Phase 3). Port the validation logic into Spectra's response pipeline. Adapt citation format from `[1]` style to whatever Nodal Flow uses. The core algorithm (split sentences → detect claims → check citations → reject uncited) is correct and tested.

**Safety filter** (`safety.py`)
- Content filtering for child-appropriate responses
- **WHERE IT GOES**: Spectra/Nodal Flow safety layer. Domain-specific — the kid tutor filter is narrow, but the pattern (post-process LLM output through a safety gate before returning) is the right architecture.

**Orchestrator** (`orchestrator.py`, 65 lines)
- Multi-agent dispatch: route queries to specialized agents, combine results
- Currently single-agent (TutorQnA), designed for expansion
- **WHERE IT GOES**: Future Spectra feature. When you have multiple shard types mounted (reference + decisions + constraints), different query types route to different evaluation paths. The orchestrator pattern handles this.

**Rehydrate** (`rehydrate.py`, 126 lines)
- `TutorQnA`: retrieval-augmented generation with local LLM
- Vector store query → context construction → LLM call → safety filter → provenance enforcement
- llama-cpp-python integration with fallback
- **WHERE IT GOES**: This is Nodal Flow's `query_ollama` implemented in Python. The pipeline is identical: retrieve → format context → call LLM → filter → enforce provenance. The Python version is useful as a standalone Spectra CLI tool that doesn't require the Tauri desktop app.

### SOCOM spectra-polished/

**Constraint adapter** (`constraint/adapter.py`, 218 lines)
- `ConstraintAdapter.from_pack()`: loads compiled packs (base + deltas)
- `evaluate(EvaluateRequest)` → `Decision` with status (permit/deny/conditional)
- Doctrine precedence ordering, FSCM safety rules
- Override detection (FRAGO overrides base constraints)
- **WHERE IT GOES**: Rebuild as a Spectra query mode. Instead of `load_concepts()` reading JSONL, query DuckDB over mounted constraint shards. The constraint types (ROE, FSCM, ACM, WCS, FPCON, EMCON, JRFL) become entities with a `constraint_type` claim. Override relations become claims with `overrides` predicate. The evaluation logic is pure — it doesn't care where the data comes from. Swap the data source from JSONL to Spectra-mounted shards.

**Constraint resolver** (`constraint/resolver.py`, 48 lines)
- `resolve_controlling_constraint()`: determines which constraint controls the decision
- Doctrine precedence sorting, override chain resolution
- Deterministic tie-breaking (sorted by constraint_id)
- **WHERE IT GOES**: Same as adapter — Spectra query mode. The resolver logic is correct. It just needs to read from mounted shards instead of JSONL.

**Authority chain** (`constraint/authority.py`)
- `resolve_delegation_chain_details()`: walks delegation relations to determine authority
- Revocation detection: if any authority in the chain is revoked, the whole chain is denied
- **WHERE IT GOES**: Graph traversal over Spectra-mounted claims. Delegation and revocation are just claim predicates (`DELEGATES_TO`, `REVOKES`). Walk the graph via DuckDB queries.

**Pack diff tool** (`tools/pack_diff/pack_diff.py`)
- Compares two compiled packs, shows added/removed/changed concepts and relations
- **WHERE IT GOES**: Spectra utility. Compare two mounted shards. Useful for auditing what changed between shard versions (reference shard v1 vs v2, or base vs base+delta).

**Provenance inspector** (`tools/provenance_viz/provenance_viz.py`)
- Traces a provenance ID back to its source, shows the chain
- **WHERE IT GOES**: Nodal Flow feature. Click a claim → see its full provenance chain → source document byte range.

**Web UI** (`tools/web_ui/server.py`, 74 lines)
- Minimal HTTP server with form-based constraint evaluation
- **WHERE IT GOES**: Spectra REST API. Generalize beyond constraints to any shard query. Mount shards, POST queries, get cited responses. This is the API layer that lets non-Tauri clients use the system.

### Summary: What Gets Rebuilt vs What Gets Ported

| Component | Source | Action | Target |
|-----------|--------|--------|--------|
| Text extractors (PDF/DOCX/XLSX/PPTX/HTML) | intake.py | Port with minor adaptation | Forge ingestion |
| Structured adapters (XBRL/iCal/RSS/CSV) | intake.py | Port, output Genesis candidates | Forge ingestion |
| Source type detection + routing | intake.py | Port | Forge ingestion |
| Coordinate system | coords.py | Rebuild as ext/ schema | ext/coords.parquet |
| Space query engine | space.py | Rebuild on DuckDB | Spectra query layer |
| Temporal derivation | temporal.py | Rebuild as Forge post-pass | Forge + ext/temporal.parquet |
| Confidence derivation | confidence.py | Rebuild as Forge post-pass | Forge + ext/confidence.parquet |
| Incremental compilation | compiler.py | Extract chunk-change-detection | Forge pipeline |
| Keyword extraction + question classification | chat.py | Port | Spectra query routing |
| Prompt construction + citation format | chat.py | Port | Spectra/Nodal Flow |
| CompiledStore retrieval | compiled_store.py | Rebuild on DuckDB | Spectra query mode |
| Provenance enforcement (hallucination firewall) | provenance.py | Port with citation format adaptation | Spectra response pipeline |
| Safety filter | safety.py | Port | Spectra response pipeline |
| Orchestrator pattern | orchestrator.py | Inform design | Spectra multi-mode query |
| TutorQnA pipeline | rehydrate.py | Already implemented in Nodal Flow | Spectra CLI fallback |
| Constraint evaluation | adapter.py + resolver.py | Rebuild on Spectra-mounted shards | Spectra constraint query mode |
| Authority chain resolution | authority.py | Rebuild as graph traversal over claims | Spectra constraint query mode |
| Pack diff | pack_diff.py | Port for Genesis shard comparison | Spectra utility |
| Provenance inspector | provenance_viz.py | Port | Nodal Flow UI feature |
| Web UI / REST API | server.py | Generalize | Spectra REST API |

### Key Concepts That Survive Across All Deprecated Code

1. **"Schema IS extraction"** — When input has a known schema (XBRL, CSV with headers, structured JSON), bypass LLM extraction entirely. Produce tier-0 candidates at confidence 1.0. This is the fastest, most reliable ingestion path.

2. **Deterministic retrieval as default** — CompiledStore uses keyword overlap, not embeddings. For offline/edge, this is correct. Embeddings are optional enhancement, not architectural requirement.

3. **Provenance contract enforcement** — Every factual claim in LLM output must have a citation that maps to a mounted shard. Uncited claims get rejected. This is the mechanical guarantee that makes "verified knowledge" true.

4. **Two processing paths** — Structured (adapter → direct mapping) and unstructured (compiler → LLM extraction). The router detects which path and the downstream pipeline handles both.

5. **Base + delta composition** — SOCOM's `from_pack([BASE, DELTA_FRAGO, DELTA_REVOKE])` pattern. Load multiple shards, evaluate across all of them, surface conflicts rather than resolving them. This is the multi-shard model.

6. **Coordinate addressing** — 8-category semantic space enables geometric queries without scanning all claims. This is the performance optimization that makes Space queries fast at scale.

---

## Execution Priority

1. **Forge universal ingestion** — extractors for PDF/DOCX/HTML/CSV, structured adapters for XBRL/CSV, source router. This unblocks "throw anything at it and get a shard."

2. **Multi-shard Spectra** — mount N shards, query across all, tag results with shard origin. This unblocks base+delta, reference+decisions, and the SOCOM-style constraint evaluation.

3. **Hallucination firewall** — port provenance.py's enforcement logic into Spectra response pipeline. This makes the verification guarantee mechanical.

4. **Decision shard loop** — interaction logger + Forge decision adapter. This closes the knowledge compounding loop.

5. **ext/ schemas** — define spatial@1, temporal@1, constraints@1, coords@1. This makes extensions interoperable.

6. **Demo script** — end-to-end: any file → shard → mount → query → cited answer → hallucination check. One command. Five minutes. Undeniable.
