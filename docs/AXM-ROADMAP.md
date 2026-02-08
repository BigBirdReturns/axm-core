# AXM Roadmap: From Working Stack to What Matters

## Where We Actually Are

The stack compiles source text into cryptographically signed knowledge shards. The chain works: source bytes go through sentence segmentation, claim extraction (LLM or regex), byte-range binding, validation, Merkle tree construction, Ed25519 signing, and verification. A gold shard exists and passes. 30 tests pass. The Tier 3 pipeline integrates with the Genesis compiler.

Known defects in the LLM-to-Genesis interface: pysbd splits medical abbreviations wrong, normalization can break byte alignment on complex documents, entity resolution doesn't exist, object_type is hardcoded. These are fixable without redesign.

What doesn't exist yet: nobody outside this conversation has run this code on a real document and gotten a useful shard back.

That's the honest starting point.

---

## The Three Things That Matter

Everything below serves one of three goals. If a task doesn't serve one of these, it gets cut.

**1. Make AXM produce real shards from real documents.**
Engineering. Makes everything else possible.

**2. Give Eugene something he can plug formal verification into.**
Partnership. Multiplies credibility and capability.

**3. Demonstrate that knowledge compilation breaks the cloud cartel's business model.**
Positioning. Makes people care.

---

## Goal 1: Real Shards from Real Documents

### Phase 1A: Fix What's Broken (Solo, 1-2 weeks)

These are the defects from the Tier 3 integration. No new features. Just make the existing pipeline honest.

**Fix normalization alignment.** The Genesis compiler runs `normalize_source_text()` before matching evidence spans. The segmenter operates on raw text. If normalization changes byte positions, the entire provenance chain is fiction. Two options: run normalization before segmentation (correct but changes segmenter contract), or add a normalization-aware span remapper after binding. The first option is cleaner. Do that.

**Fix entity resolution.** "TXA", "Tranexamic acid", and "tranexamic acid" becoming three entities makes the knowledge graph useless. This doesn't need to be perfect. A case-insensitive dedup with alias tracking (acronyms, common abbreviations) gets 80% of the value. The LLM prompt can also be modified to emit a canonical entity name, which reduces the problem at the source.

**Fix object_type classification.** Every claim object is currently tagged "entity" even when the object is a literal value like "500mg" or "1962". Genesis creates bogus entity nodes for these. The LLM prompt needs to distinguish entity references from literal values. Alternatively, a heuristic in the binder (if object matches a number/date/quantity pattern, mark it literal) handles this without LLM changes.

**Fix the schema adapter hack.** The binder emits `extraction_tier`, Genesis reads `tier`. The `nodal_run.py` script rewrites candidates into a second file. This is a two-line fix in `tier3_stage2.py` to emit Genesis-compatible fields directly.

**Test on 5 real Wikipedia articles across domains.** Medical (Tranexamic acid), legal (Fourth Amendment), technical (TCP/IP), historical (Battle of Midway), scientific (CRISPR). Each article exercises different text patterns. Fix what breaks. Document what doesn't work.

**Deliverable:** `python nodal_run.py "Fourth Amendment"` produces a shard you'd trust enough to show someone. Not perfect. Not production. But real claims from real text with real byte-range provenance that verifies.

### Phase 1B: Beyond Wikipedia (2-4 weeks after 1A)

Wikipedia is clean text. Real documents are not.

**PDF extraction.** The router already classifies PDFs and routes to `pdf_chunker.py`. The chunker exists in Genesis's `axm_extract` package. Wire the Tier 3 pipeline to accept chunker output instead of raw text. This means the segmenter needs to handle text that came from PDF extraction (possibly with OCR artifacts, column breaks, header/footer contamination). Don't try to solve all PDF problems. Pick a category (DoD field manuals, SEC filings, medical guidelines) and make that category work reliably.

**Multi-page source provenance.** Current pipeline assumes one source file. Real documents have pages. The provenance table needs `source_page` populated correctly, and byte offsets need to be page-relative or document-relative (pick one, document it, be consistent). The Genesis spec supports this; the pipeline doesn't populate it.

**Batch compilation.** `nodal_run.py` does one document at a time. For a useful knowledge base, you need to compile a corpus. This means shard composition: can you merge shards? Can you query across shards? Spectra already mounts multiple shards into DuckDB. The question is whether the compilation pipeline supports batch input cleanly.

**Deliverable:** Compile a 10-document corpus from a single domain (pick one: military field manuals, a set of SEC filings, clinical guidelines). Mount all shards in Spectra. Run queries that cross documents. This is the first thing you can show someone that isn't a toy.

---

## Goal 2: Give Eugene a Verification Surface

### What Eugene Needs

Eugene (and the formal verification community around Lean4/Nala) cares about one question: can you take a claim extracted by an LLM and subject it to external verification that isn't just "the LLM said so"?

AXM already answers a narrow version of this: the byte-range provenance proves the evidence text exists in the source document, and the Merkle tree proves it hasn't been tampered with. That's structural verification (the claim is grounded). It's not semantic verification (the claim is true).

The gap between structural and semantic verification is where Eugene's work fits.

### Phase 2A: Define the Verification Interface (Design, 1 week)

**The Radiant layer.** This sits between the binder (which produces candidates with byte-range evidence) and the compiler (which builds the shard). Its job: take each candidate claim and assign a verification status.

Verification status tiers:

- **Tier 0: Structural only.** Evidence bytes match source. No semantic check. This is what we have now.
- **Tier 1: Pattern-verified.** Claim matches a known extraction pattern with high confidence (regex tier, date/number patterns). Deterministic.
- **Tier 2: Cross-referenced.** Claim corroborated by a second source or known fact database. Probabilistic but grounded.
- **Tier 3: Formally verified.** Claim has been checked by an external verifier (Lean4 proof, domain-specific logic checker, human attestation). This is Eugene's territory.

The Genesis manifest already has a `tier` field on every claim. What it doesn't have is a `verification_method` field that records how the tier was assigned. Add that. It's a string: "regex_match", "byte_span_only", "lean4_proof", "human_review", "cross_reference". This makes the shard self-documenting about its own epistemic confidence.

**The interface contract for external verifiers:**

```
Input:  claim (subject, predicate, object, evidence, source_bytes)
Output: {verified: bool, method: string, proof_artifact: optional bytes, notes: string}
```

Any system that implements this interface can plug into the pipeline. Lean4 is one. A human review tool is another. A domain-specific rule engine is a third. The point is the interface, not the implementation.

**Deliverable:** A specification document (2-3 pages) that Eugene can read and say either "yes, I can build to this" or "no, change X." This is the collaboration artifact. Not code. A contract.

### Phase 2B: Build the Hooks (Engineering, 2 weeks after 2A agreement)

**Add verification stage to nodal_run.py.** After binding, before compilation, run each candidate through available verifiers. Start with a pass-through (Tier 0 for everything, same as now). Then add a rule-based verifier for Tier 1 (if the claim matches a date pattern and the date is in the source, mark it Tier 1). This proves the pipeline works without requiring Lean4.

**Add verification_method to the Genesis manifest and Parquet schema.** This is a schema change. It touches the compiler, the verifier, and Spectra's mount logic. Do it once, do it right, because this field becomes the foundation for everything Eugene builds on.

**Create a verification harness for external tools.** A Python script that reads candidates.jsonl, calls an external verifier via subprocess or HTTP, and writes back annotated candidates with verification results. Eugene's Lean4 tool, or anyone else's verifier, plugs in here.

**Deliverable:** A shard where some claims are Tier 0, some are Tier 1, and the manifest records the difference. Eugene receives the harness specification and can start building a Lean4 adapter without waiting for anything else.

---

## Goal 3: Break the Cloud Cartel's Business Model

### The Argument

The cloud AI business model is: you pay per query, forever. Your data goes to their servers. Your knowledge is locked in their context windows. You own nothing.

AXM inverts this: you pay once (in compute) to compile knowledge. Then you query it forever, locally, for free. The shard is a file. You own it. It works offline. It works in 50 years. No subscription. No API key. No vendor.

This isn't an abstract argument. It's a demonstrable economic fact. 1,000 queries to GPT-4 about a document cost $10-50 depending on document size and query complexity. 1 AXM compilation of that document plus 1,000 local queries cost about $1 in compute, then $0 forever. The break-even is around 100 queries per document. After that, every query is free.

But nobody will believe the math until they can see it work.

### Phase 3A: The Demo That Makes People Angry (2-3 weeks, overlaps with 1B)

**Pick a document everyone cares about.** Not a Wikipedia article. Something with stakes. Options: a public SEC 10-K filing (enterprise audience cares), a military field manual chapter (defense audience cares), a clinical practice guideline (healthcare audience cares), a terms of service agreement (everyone cares).

**Compile it into a shard. Record the process.** Screen recording of `nodal_run.py` running. Show the LLM extraction phase (expensive, runs once). Show the shard being created. Show the verification passing. Show the file size (small). Show the query (instant, free, offline).

**Then show the same queries against ChatGPT/Claude API.** Show the cost accumulating. Show the latency. Show that the answers sometimes hallucinate. Show that AXM's answers have byte-range citations and the API answers don't.

**The framing is not "AI bad."** The framing is: "The expensive part should happen once. Then you own the answer." This is the line that landed when you posted about Eugene's 128GB rig.

**Deliverable:** A 3-5 minute video or a blog post with screenshots. This is what you share on LinkedIn. This is what your defense contacts see. This is what makes Nishanth say "let me help build this."

### Phase 3B: The Distribution Story (4-8 weeks, depends on traction)

A shard is a file. Files can be shared. This is where the sovereignty argument gets teeth.

**Magnet link distribution.** A shard has a Merkle root. That root is a content address. Anyone who has the root can verify the shard. Anyone who has the shard can share it. This is BitTorrent for knowledge.

**Clarion encryption for selective sharing.** The Clarion layer already exists in the stack. It wraps shards in AES-GCM envelopes with HKDF key derivation. The GraphKDF extension (topology-bound encryption where the key changes if the graph structure is tampered with) is designed but not implemented. For now, simple envelope encryption is sufficient: encrypt a shard, share the key with authorized parties, they decrypt and verify locally.

**The political argument:** When a government compiles its own knowledge base from its own documents using its own compute, the result is sovereign. It doesn't phone home. It doesn't depend on an American cloud provider. It doesn't stop working when a sanctions list changes. This is what the defense contacts and the sovereign AI movement care about. Not the code. The independence.

**Deliverable:** A public shard (something compiled from a public domain document) shared via its Merkle root. Anyone can download it, verify it, and query it without an account, API key, or subscription. That's the proof of concept for the distribution model.

---

## What This Doesn't Include (And Why)

**Nodal Flow (the Tauri desktop app).** It exists in the stack. The Svelte frontend and Rust backend (vault.rs) are written. But a desktop app is a product, not a proof of concept. Building the app before the pipeline produces reliable shards means building a beautiful interface for garbage data. Defer until Phase 1B is done.

**Clarion GraphKDF (topology-bound encryption).** Designed, not built. The current simple encryption works. GraphKDF matters when you need selective disclosure (share some claims but not others from the same shard). That's a real use case for defense and enterprise, but it's not the next thing to build.

**AXM Embodied (robotics).** The architecture for action provenance (tracing robot actions to byte offsets in event logs, with a "Law Gate" that restricts execution to signed, auditable rules) is designed. It's a separate product. Not on this roadmap.

**FakeSoap.** Different project, different audience. Shares the same analytical method but doesn't use the AXM stack.

---

## Priority Order

1. Fix the four defects from Phase 1A. Days, not weeks.
2. Test on 5 real articles. Reveals the next set of problems.
3. Write the Radiant spec for Eugene (Phase 2A). A document, not code. Can happen in parallel with testing.
4. Build the demo (Phase 3A). One document, compiled, shown working. Creates external momentum.
5. Everything else follows from what people say when they see the demo.

---

## The Honest Assessment

AXM's technical architecture is sound. The separation of compilation from generation is the right idea. Byte-level provenance with cryptographic signing is the right primitive. The stack is real code that runs and produces real output.

What's missing is proof that it works on anything other than synthetic test cases and one hand-curated gold shard. The gap between "the tests pass" and "this is useful" is where the actual work lives.

The cloud cartel's business model doesn't break because someone wrote a compiler. It breaks when a thousand people compile their own knowledge and stop paying per query. Getting from here to there requires making the pipeline reliable enough that someone other than you can run it and trust the output.

That's the job. Everything else is positioning.
