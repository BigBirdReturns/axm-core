# Existing Obsidian vaults: reviewed, signed, and returned

Use Obsidian for ordinary authoring. Capture its selected Markdown notes into an AXM snapshot, inspect the source changes, explicitly approve that exact capture, and publish a signed successor through the existing Genesis compiler. Refresh adds native evidence views inside the same vault. Original notes, local annotations, customized Bases layouts, and earlier generated editions remain in place.

This is source-capture interoperability. A `records_note` claim records the contents of a particular note, not the truth of every assertion inside it. The snapshot retains original bytes separately from the normalized aggregate used for canonical claim evidence. It performs no automatic semantic extraction or background signing.

## Install and supply publisher identity

Use the environment described in [OBSIDIAN.md](OBSIDIAN.md), including the hash-pinned optional requirements. Supply a Genesis hybrid private key and its independently trusted public key through separate paths outside the source vault. Keep the private key out of source control, captures, release bundles, and exported views. The tools do not create or discover a production publisher identity for you. Tests create fresh, ephemeral identities.

All commands below run from `examples/knowledge-pack`, or the root of the public source kit. Relative paths are interpreted from that working directory. Create the parent directories for output paths first. `--go-verifier ./build/axm-verify-go` adds independent Go verification to every relevant pack check after the existing native build has run.

## First edition

Capture a review plan outside the vault:

```sh
python obsidian_vault.py plan /path/to/vault \
  --vault-id research \
  --publisher-id research-publisher \
  --publisher-name 'Research publisher' \
  --trusted-key /path/to/publisher.pub \
  --out /path/to/reviews/edition-1
```

The JSON result supplies `approval_sha256`, note counts, selected exclusions, and added/changed/deleted/renamed paths. `review.md` contains bounded unified diffs; the complete captured notes remain in the review directory. A truncated diff is explicitly identified. The digest commits to the inventory, publisher, selection, predecessor, creation time, original note hashes, and the review presentation.

After reviewing those exact bytes, seal them:

```sh
python obsidian_vault.py seal /path/to/vault \
  --plan /path/to/reviews/edition-1 \
  --approve APPROVAL_SHA256_FROM_PLAN \
  --private-key /path/to/publisher.key \
  --trusted-key /path/to/publisher.pub \
  --output /path/to/packs/edition-1.axmpack.zip
```

The approval must match the complete plan digest. A changed working note, selection rule, staged note, review presentation, predecessor pack, or trust anchor refuses the operation. The CLI performs these checks before opening the signing key. Genesis creates the canonical tables, identities, Merkle commitments, and signatures; this adapter does not reimplement those surfaces. The signed artifact also retains the approved plan and review. The approval record is the signing operator's explicit workflow assertion, not a second human-authentication scheme.

Refresh the existing vault:

```sh
python obsidian_vault.py refresh /path/to/vault \
  --pack /path/to/packs/edition-1.axmpack.zip \
  --trusted-key /path/to/publisher.pub
```

In Obsidian, open `_AXM/Evidence editions.base`. It lists accepted editions and links to each edition's source evidence, Canvas graph, and claim tables. Those native files use paths rebased to the original vault root. No nested Obsidian vault, plugin, theme, new note editor, or external service is installed.

## Subsequent edits

Edit the ordinary source notes in Obsidian. Repeat `plan` and `seal` with `--previous /path/to/packs/edition-1.axmpack.zip` on both commands, using new review/output paths. Approve the new plan digest, then refresh from the new pack. The new signed head names its predecessor; predecessor shard bytes remain unchanged inside the resulting package.

Changes include additions, deletions, and modifications. A rename is identified only when one removed path and one added path have uniquely matching bytes; ambiguous duplicate-content cases remain separate additions/deletions. A fully deleted selection creates an explicit empty snapshot with its inventory, while retaining history. A no-change run returns the previous archive byte for byte and never invokes the signing compiler.

Compare current authoring notes with a captured edition at any time:

```sh
python obsidian_vault.py status /path/to/vault \
  --pack /path/to/packs/edition-2.axmpack.zip \
  --trusted-key /path/to/publisher.pub
```

`MATCHES_CAPTURED_NOTES` refers to the selected note bytes at the time of this check. `WORKING_COPY_CHANGED` identifies unsigned source revisions. A static view is an edition, not continuous attestation of a file that remains editable.

## What refresh owns

`_AXM/owner.json` identifies the adapter-owned view workspace; an existing unrelated `_AXM` directory is refused and preserved. The workspace contains immutable named edition directories, byte-identical pack copies, append-only edition-navigation notes, a local navigation receipt, and a reusable native Bases layout. The local receipt supplies no cryptographic authority. Every refresh verifies the referenced pack against the separately supplied key and refuses rollback or divergent history relative to the published head.

Refresh writes zero original source files. It never replaces an existing edition directory. Modified generated pages remain edited and appear in the returned drift report. `_AXM/Notes.md` belongs to the human, and an existing `Evidence editions.base` layout also remains untouched. All managed views and annotations stay outside source intake. To make an annotation part of a new signed edition, put it in an ordinary source note and review that new capture.

A failed publication may leave complete immutable outputs that a retry can reuse; it never replaces different existing outputs. Concurrent publishers are refused by a workspace ownership lock. An interrupted process can leave that lock for inspection rather than having another writer steal it on an arbitrary timeout.

## Agent retrieval

The existing bridge reads the signed pack rather than an edited view:

```sh
python obsidian_bridge.py search /path/to/packs/edition-2.axmpack.zip \
  'decision topic' --trusted-key /path/to/publisher.pub
python obsidian_bridge.py context /path/to/packs/edition-2.axmpack.zip \
  'decision topic' --trusted-key /path/to/publisher.pub --budget 2000
python obsidian_bridge.py search /path/to/packs/edition-2.axmpack.zip \
  'old decision' --trusted-key /path/to/publisher.pub --scope history
```

The maintained upstream library supplies lexical ranking and estimated-budget context assembly. Current queries see the supplied pack's signed head; historical queries see its included predecessors. Local source edits and edited exported views cannot silently enter those answers. Results retain canonical shard/claim identifiers, provenance IDs, source hashes, and canonical byte spans. The context budget remains the upstream character estimate, not a strict model-tokenizer ceiling.

## Selection, original bytes, and bounds

The adapter reuses the pinned library's knowledge-page conventions, frontmatter splitting, metadata parser, and `.okignore` rules. Hidden/tool directories, agent-instruction files, and the vault-root `README.md` are excluded by those conventions; exclusions are visible in the plan and retained in the snapshot. Attachments, Canvas documents, and Bases layouts in the source vault are not interpreted or captured as knowledge pages. They remain untouched. Generated Canvas and Bases views are a separate output.

Each note's original UTF-8 bytes, including BOMs, CRLFs, frontmatter, links, and Unicode spelling, are retained under a safe content filename. The signed inventory binds that filename to the original vault-relative path, byte count, and SHA-256. Canonical evidence addresses the explicitly identified normalized aggregate `content/source.txt`; these offsets are not represented as offsets into the unnormalized original note. Snapshot readers verify the approved inventory, retained plan/review, publisher/namespace/lineage, original hashes, and note-capture claims in addition to ordinary Genesis verification.

The initial bound is 128 selected notes, 256 KiB per note, and 2 MiB of selected note bytes per snapshot. The existing pack limits still apply: 8 MiB archive, 32 MiB expansion, 1,024 members, and 32 included editions. Exceeding a bound refuses output rather than dropping notes or rewriting history. Symlinks, special files, unstable reads, unsafe paths, and case/Unicode path collisions are refused. Qualification currently exercises the full round trip on Linux; desktop rendering and other operating systems require their own execution receipts.

## Qualification

```sh
python scripts/build.py --test --web --native
PYTHONPATH=. python -m pytest -q \
  tests/test_obsidian_export.py tests/test_obsidian_bridge.py tests/test_obsidian_vault.py
python scripts/test_creator.py
```

The round-trip suite exercises real signing and independent native verification, original-byte custody, review staleness, no-change reuse, Unicode/CRLF/empty/duplicate notes, source and signature tampering, history scoping, rollback/fork refusal, preservation of human edits, no-network operation, collision/symlink rejection, and the actual CLI path. These tests establish their stated boundaries, not semantic truth or an unbounded production-vault capacity claim.
