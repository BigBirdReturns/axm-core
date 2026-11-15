# AXM Vault Workbench 0.4.0

A local application for existing Obsidian vaults: inspect source changes, review evidence-bound extraction proposals, approve selected assertions, seal incremental document revisions, query accepted evidence across vaults, inspect signed history, and refresh native Obsidian views. Obsidian remains the editor. Forge owns extraction, Spectra owns query execution, and the unchanged Genesis compiler and verifier own signed evidence.

The browser application runs on the computer hosting the vaults. The public Knowledge Pack website distributes its reproducible source kit; it does not receive or process your notes. Capture and retrieval need no model. Enabling a remote extraction provider authorizes transmission of selected source prose to that provider, not automatic acceptance or signing.

## Start the application

Extract the complete 0.4.0 source kit. With Python 3.12 or newer, run:

```sh
python3 scripts/launch_workbench.py --demo
```

On Windows, use `py -3 scripts\launch_workbench.py --demo` or `launch-workbench.cmd --demo`. On Linux/macOS, `./launch-workbench.sh --demo` is equivalent. The launcher creates an isolated, versioned runtime, installs the pinned dependencies and included Genesis package, and starts the loopback-only service at `http://127.0.0.1:8357/`. Initial dependency installation requires network access. The installed application makes no model call until extraction is explicitly enabled for a scan.

`--demo` creates two explicitly synthetic vaults and a fresh local demonstration identity only when the state has no registered vaults. Omit it for an empty workspace. Choose `--state /absolute/private/state` to place the durable state elsewhere and `--port 8357` to choose a free local port. The source vault and application state must be separate directories. The launcher keeps the process in the foreground; stop it with Ctrl-C. It creates no operating-system startup registration.

The browser UI provides **Review**, **Search**, **History**, **Connections**, and **Settings**. Jobs show queued, running, completed, failed, cancelled, or interrupted state. Source notes remain in their original folders throughout.

## Register real vaults and keys

In Settings, register an absolute source directory, a stable vault identifier, an explicit publisher name/identifier, and the detached trusted public-key path. A signing-key path enables approval. Keys must remain outside every registered source tree. Vault directories must be distinct and non-overlapping.

Use an existing Genesis hybrid identity or explicitly create a new local one in Settings. New secret keys are written under the application's private state, with mode 0600 where supported. A generated identity establishes local custody, not external proof of who the publisher is. Share its public key through a separately trusted channel when distributing signed artifacts. No private key is included in a collection export.

The state directory contains configuration, a durable SQLite job/review catalog, review captures, model responses, signing identities you explicitly created there, and immutable accepted objects. Protect this directory as private data. `config.json` stores file paths and API-key environment-variable names, never an API secret pasted into a form. The provider environment must exist in the process that launches the service.

## Scan, inspect, select, approve

Select a vault and click **Scan changes**. The application compares source hashes against the current signed catalog. Unchanged notes keep their existing document-shard identities. A uniquely identifiable byte-preserving rename records its predecessor; ambiguous duplicate-content cases remain explicit additions and deletions.

Open the draft review. Each changed note exposes its original hash, source diff and complete staged original. Source capture includes every selected changed note, including frontmatter and exact original UTF-8 bytes. Proposed semantic assertions are separate checkboxes and start unselected. Frontmatter metadata is lifted deterministically through the maintained upstream parser. A model proposal includes subject, predicate, object, extraction tier, exact quotation, source byte range and extraction origin.

**Approve selected and seal edition** authorizes the exact review digest and candidate selection. It captures changed source notes and adds only the selected proposals as additional canonical claims. Rejecting a proposed claim does not erase the underlying source text. Rejecting the review leaves the accepted head unchanged.

The application refuses approval when the plan digest, source inventory, staged note, model response, publisher, trust anchor or accepted predecessor has changed. It checks the live sources again before publishing the new accepted head. Unchanged approval returns the same head without invoking the compiler. An interrupted operation can leave complete immutable candidate objects, but the current head changes only in the final compare-and-swap transaction. Restart marks unfinished jobs interrupted instead of replaying their effects.

## Incremental signed collections

The 0.3.0 reference captured a bounded entire vault in each Knowledge Pack. The workbench uses a different application-level collection profile while keeping Genesis v1 unchanged:

- Each changed document becomes its own signed shard. The previous note revision remains intact and appears in its signed lineage.
- A new signed collection catalog maps paths and original hashes to accepted note shards. It binds the exact review, approved candidate identifiers, publisher, creation time and previous collection head.
- Unchanged documents are referenced, not recompiled. Deletions remove paths from the new current inventory while historical catalogs retain access to their prior revisions.

The default admitted vault size is 10,000 selected Markdown notes, 1 MiB per note and 128 MiB of selected originals. Bounds are explicit configuration, not a promise about every possible workload. Source scanning still reads and hashes selected originals to detect edits. Incremental compilation avoids re-extraction and re-signing of unchanged documents; it does not pretend that an arbitrary filesystem is a transactional snapshot.

Original note bytes, including BOMs, CRLFs, Unicode spelling and frontmatter, are retained separately. Canonical evidence offsets address the labeled normalized UTF-8 `content/source.txt`; they are not represented as offsets into an unnormalized original. Empty notes receive an explicit empty-source record. Symlinks, special files, unstable reads, unsafe paths and case/Unicode collisions are refused.

## Choose a model without coupling authority to it

Settings supports an explicitly selected **OpenAI-compatible API**, **Ollama**, or **source capture only**. Specify the exact served model and base URL. Loopback HTTP is permitted. A non-loopback provider requires HTTPS and the explicit remote-source authorization checkbox. Redirects are refused. API secrets come from the named environment variable. The local CLI additionally accepts a fixed absolute command-provider argv, never a command derived from source text or browser input.

A scan has a finite model-call budget, defaults to 32 calls, and each call has bounded input, response and duration. Provider failures and exhausted budgets appear in the review. They do not silently become accepted claims. A note exceeding the configured model-input bound remains capturable as source; split it or deliberately adjust the provider bound to extract it. Automatic chunked semantic extraction is not part of this release.

Forge sends prose to the model and excludes frontmatter, fenced code and tables from that stochastic route. Proposed quotes must occur exactly once in the canonical source and belong to eligible prose. This rejects invented and ambiguous evidence, but does not prove that the proposed subject/predicate/object preserves every qualification. Review still owns that judgment. The recorded extraction receipt distinguishes requested and provider-reported model identity, retains available usage counters, and binds source and response hashes. A provider-reported model name is a report, not hardware attestation.

The cache key binds source, path, extraction protocol and provider configuration. Reusing a response revalidates its evidence. Explicit **Re-extract unchanged notes** bypasses that cache; ordinary no-change scans make zero model calls.

## Search, context and competing assertions

Spectra builds a disposable SQL projection from independently verified catalogs and note shards. Queries cross registered vaults while retaining each vault's detached trust anchor. Current scope selects each accepted head; history selects replaced or deleted document revisions while excluding document shards still current. Matching returned evidence is reverified against the original sealed bytes.

Search is lexical, parameterized SQL. It is not an unqualified semantic-vector retrieval claim. **Build agent context** passes the verified shortlist to the maintained `obsidian-wiki` context assembler and returns canonical references. Its token budget remains an approximate character estimate; reference metadata is additional.

**Competing assertions** surfaces different approved objects under matching subject/predicate strings. It creates a review candidate, not an automatic contradiction verdict. Distinct times, units, scope or spelling may explain differences or prevent a match. **Connections** displays resolved wikilinks in retained source notes using the pinned upstream syntax, labels unresolved/ambiguous targets, and bounds the visible graph to 200 nodes. Source links are navigation, not independent semantic proof.

## Watch and native Obsidian views

The explicit Watch toggle reuses the maintained `watchfiles` library. It creates source-only drafts after Markdown changes, coalesces notifications, and retains a pending rescan when another job is active. Watching never invokes a model and never signs a new edition. Configuration changes require an idle queue and disabled watchers.

**Refresh Obsidian views** creates versioned Markdown, Canvas and Bases documents under `_AXM-Workbench`. Open `_AXM-Workbench/Editions.base` in the existing vault. Refresh writes zero original source files and preserves edited generated pages and customized layouts. Managed views remain outside source intake. Unsigned annotations and edited views cannot silently enter accepted retrieval. The local browser application has automated desktop/mobile execution tests; native Obsidian desktop rendering is a separate, unclaimed qualification.

## Portable collections and detached verification

Use **Export signed collection** and download the resulting archive from the completed job. This produces a closed `.axmcollection.zip` containing the signed catalog history and all referenced note revisions, with original source bytes and approval evidence. It contains no private key, mutable work queue, local provider secret or executable source-derived instruction.

The collection archive is an application transport profile, `axm-vault-collection-archive/1`, separate from the older single-head `.axmpack.zip` wrapper. It does not change the Genesis shard format. Verify it locally with a separately obtained public key:

```sh
python vault_workbench.py --state /path/to/verification-state verify-collection \
  --archive /path/to/accepted.axmcollection.zip \
  --trusted-key /path/to/separately-obtained-publisher.pub
```

The reader checks Genesis signatures, derived object identities, exact object closure, signed review/catalog consistency, original-note hashes, canonical-source correspondence and historical continuity. It refuses missing or extra objects, duplicate members, traversal, symlinks, encrypted members and expansion over the declared bounds. Export limits are 256 MiB compressed, 512 MiB expanded, 32 MiB per member and 250,000 members. An archive above the admitted bound fails rather than silently discarding history. Importing a foreign collection into the active authoring head is deliberately separate from verification and is not implemented here.

## Implementation ownership and reproducibility

Forge source lives at `forge/axm_forge/vault_extraction.py`; Spectra collection projection lives at `spectra/axiom_runtime/vault_index.py`. The source kit packages exact copies under `runtime/` and records owner paths and SHA-256 in `runtime/SOURCE-MAP.json`. Building from the full repository or the extracted source kit uses those owners, not a separately maintained fork. Spectra retains its SQL gate and audit path. This application configures two query threads and two asynchronous workers so its local runtime does not expand to every host core.

The browser serves only from loopback, rejects unexpected Host/Origin headers, requires a fresh local-session token for writes, and applies a self-only content policy. All model/source strings render as text, including HTML-looking material. The app never executes note instructions or a model-supplied command. These controls do not defend against another process already authorized to alter the same user's keys or private state.

After installation, run the new and retained tests:

```sh
python scripts/build.py --test --web --native
PYTHONPATH=. python -m pytest -q tests/test_workbench.py \
  tests/test_obsidian_export.py tests/test_obsidian_bridge.py tests/test_obsidian_vault.py
python scripts/test_creator.py
python scripts/workbench_browser_test.py
python scripts/browser_test.py
```

The new browser gate uses a labeled deterministic HTTP provider fixture to exercise UI transitions and negative controls. A real model qualification is a separately retained execution receipt. Linux/Python 3.12 and Chrome are the exercised workbench platform; the included portable launchers do not imply that the complete signing workflow has been independently executed on every operating system.
