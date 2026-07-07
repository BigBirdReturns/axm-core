# Start here — the AXM ecosystem in one page

*You just cloned something in the AXM ecosystem and want to know what it is,
what the other repos are, and which one to clone first. This is that page.*

## The thesis, in one sentence

**Records that outlive their infrastructure.** Everything here exists to take a
record — an ontology, a conversation, a physical event, a decision — and seal it
into a form that stays valid and verifiable when the platform that made it, the
vendor that hosted it, and the company that built AXM are all gone. Sealed with
a post-quantum kernel, verifiable detached with nothing but the bytes and an
out-of-band key.

## If you have exactly one goal, clone exactly these

| Your goal | Clone | Then read |
|---|---|---|
| **Exit an ontology off Palantir Foundry** | `axm-genesis` + `GhostBox` | [FIRST_HOUR.md](https://github.com/BigBirdReturns/GhostBox/blob/main/foundry_exit/FIRST_HOUR.md) — clone to sealed exit in under an hour |
| **See the whole exit as one hull (9/9 planks)** | `axm-genesis` + `GhostBox` | [SHIP_OF_THESEUS.md](https://github.com/BigBirdReturns/GhostBox/blob/main/foundry_exit/SHIP_OF_THESEUS.md) — `axm-exit-ship`: all 9 planks sealed, 4 full-surface |
| **Exit pipeline schemas + the dependency DAG** | `axm-genesis` + `GhostBox` | [PIPELINE_EXIT.md](https://github.com/BigBirdReturns/GhostBox/blob/main/foundry_exit/PIPELINE_EXIT.md) — `axm-pipeline-exit` |
| **Understand what a Foundry exit does and doesn't cover** | (just read) | [WORKFLOW_EXIT_MAP.md](https://github.com/BigBirdReturns/GhostBox/blob/main/foundry_exit/WORKFLOW_EXIT_MAP.md) — the frontier, surface by surface |
| **Verify a sealed shard someone handed you** | `axm-genesis` | its README — `axm-verify shard <dir> --trusted-key <key>` |
| **Turn LLM chat exports into signed, queryable knowledge** | `axm-chat` | [axm-chat docs](https://bigbirdreturns.github.io/axm-chat/) |
| **See one operator view over the whole ecosystem** | `axm-console` | its `docs/CONTINUITY.md` (the 30-year charter) |
| **Build your own spoke** | `axm-core` | [`SPOKE_API.md`](SPOKE_API.md) |

Everything depends on **axm-genesis** (the kernel). If in doubt, clone that
first; it verifies any shard from any spoke with no other repo present.

## The map — eight repos, what each proves

**The spine**
- **axm-genesis** — the cryptographic kernel. Shard spec, compiler, verifier,
  post-quantum crypto (`axm-hybrid1` = Ed25519 + ML-DSA-44). Compiled knowledge
  with provenance. *Everything below seals and verifies through this.*
- **axm-core** — the orchestration hub: the `axm` CLI host, **Forge** (document
  ingestion), **Spectra** (DuckDB query engine), and the `axm` CLI host. *The
  Foundry exit used to live here; it now lives on the **GhostBox** spoke, which
  depends on core only for Spectra query.*
- **axm-console** — one seat over the ecosystem. The operator's console across
  all the spokes; home of the **CONTINUITY charter**, the 10 invariants meant to
  hold for 20–30 years.

**The spokes — each proves one surface, in its own repo**
- **axm-chat** — LLM conversation exports → cryptographically signed, queryable
  shards; every claim traces to a byte range in the source; verifies offline.
- **GhostBox** — the intelligence + observation spoke (semantic tension analysis, Screen
  Ghost photonic intake). **Home of the Palantir exit** (`axm-exit-ship`, 9/9 planks) — it watches the incumbent and stages the switch.
- **ScreenGhost** — autonomous UI control + state observation. Watches any
  screen, understands it, acts. No cloud, no API.
- **axm-embodied** — the physical-liability spoke: real-world events and
  reconciliation, on the same kernel.
- **axm-aide** — the sovereign personal-assistant spoke: your records, your
  custody, on the same seal.

## The rules that don't change

Before you build on this, know the three invariants that everything else bends
around (full set in `axm-console/docs/CONTINUITY.md`):

1. **Never overclaim.** Every page, doc, and commit states what was actually
   exercised vs. not. "Proven, not deployed." The Foundry exit is proven on
   ontology structure + data; the *workflow layer* is mapped, not built, and
   says so on the tin.
2. **The machine never decides to carry real data.** The channel activates on
   real records only when a data controller with lawful authority chooses — the
   consent gate. Synthetic-until-authorized, never covert.
3. **Honesty over polish, always.** If a demo can't do a thing, the page says
   so. No safety mechanism is ever routed around by rewording.

Clone the kernel, run the first hour, verify a shard with nothing but its bytes.
That's the whole promise, and it's reproducible today.
