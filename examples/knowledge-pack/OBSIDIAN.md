# Obsidian export and maintained search

The bridge turns a verified AXM knowledge pack into linked Markdown, a native Obsidian Canvas, and a Bases evidence table. It uses the maintained `obsidian-wiki` Python API for lexical ranking and context assembly. Genesis continues to own artifact verification; the pack reader continues to own transport and signed history; this adapter owns the disposable projection between those boundaries.

## Install

Use Python 3.12 or newer from this directory or the unpacked public source archive. Install the existing Genesis reader dependency in an isolated environment:

```sh
python -m venv .venv
# Activate the environment using your operating system's standard command.
python -m pip install './vendor/axm-genesis[mldsa-compat]'
```

Export uses this reader without the optional search dependency. For `search` and `context`, install the exact upstream wheel:

```sh
python -m pip install --require-hashes --only-binary=:all: -r requirements-obsidian.txt
```

The pin is `obsidian-wiki==2026.11.4`. Its base package requires no additional runtime dependencies. The adapter calls `obsidian_wiki.Memory` directly; it does not run global setup, register agent hooks, invoke the upstream command-line updater, or install Obsidian plugins. The package and its caches remain outside the public source archive.

## Export a vault

```sh
python obsidian_bridge.py export fixtures/example.axmpack.zip --trusted-key fixtures/trusted-demo.pub --output ./evidence-vault
```

Choose a new output directory, then open that folder as a vault in Obsidian. The output includes current and historical claim notes, retained evidence and original archive bytes, `Knowledge.canvas`, and `Evidence.base`. A retry with identical generated content is accepted; an edited target is refused so regeneration cannot overwrite human changes.

The key is an explicit input obtained separately from the pack. The supplied fixture key is the public demonstration identity described in the main README. Every export passes through the existing Genesis-backed `inspect_pack` function before any claim is presented as a verified artifact record. The bridge preserves canonical shard and claim identifiers, source hashes, byte offsets, and predecessor relationships. Markdown and native views remain derived presentation files. Their edits cannot modify the signed input or authorize a successor edition.

## Search and build context

```sh
python obsidian_bridge.py search fixtures/example.axmpack.zip 'source evidence' --trusted-key fixtures/trusted-demo.pub --limit 8
python obsidian_bridge.py context fixtures/example.axmpack.zip 'source evidence' --trusted-key fixtures/trusted-demo.pub --budget 2000
python obsidian_bridge.py search fixtures/example.axmpack.zip 'source evidence' --trusted-key fixtures/trusted-demo.pub --scope history --limit 8
```

Successful command results and runtime failures emit JSON. Invalid command syntax uses standard argparse usage and error output. Search and context take a pack and its external trust key, create a fresh verified projection in a temporary directory, and call the real upstream `Memory.search` or `Memory.context` API against the selected scope. They read neither a human-edited export vault nor its caches. Upstream query caches are temporary and leave with that projection.

| Scope | Included editions |
|---|---|
| `current` (default) | The unique head derived from signed history in the supplied pack. |
| `history` | Its included predecessor editions, excluding the head. An empty history returns empty results. |

Here `current` identifies the head of this particular pack. It makes no claim about the latest edition elsewhere or the current truth of a source. Physically separate query scopes keep historical matches from entering current results through retrieval ranking.

The context budget uses the upstream estimate of characters divided by four. It is an approximate content budget rather than a strict tokenizer ceiling. The JSON metadata and evidence wrapper sit outside that budget. Canonical extraction tier is retained as `axm_tier`; any upstream retrieval tier is labeled separately and does not change the source claim's classification.

## Qualify the integration

After installing the Genesis dependency and the pinned optional wheel, install the test requirements and exercise both the exporter and the real upstream API:

```sh
python -m pip install -r requirements-test.txt
python -m pytest -q tests/test_obsidian_export.py tests/test_obsidian_bridge.py
```

These focused checks cover verified projection and evidence mapping, fresh-output and retry behavior, scope separation, and the pinned library boundary.

## Ownership and provenance

The dependency owns frontmatter handling, lexical ranking, and context assembly. The adapter owns the correspondence between generated notes and verified AXM records. Signatures and the declared verification scope remain those of Genesis. Unknown domain profiles stay explicitly unchecked, and retrieval scores do not certify semantic truth or source completeness.

The exporter targets [JSON Canvas 1.0](https://jsoncanvas.org/spec/1.0/) and the official [Obsidian Bases syntax](https://help.obsidian.md/bases/syntax), so Obsidian maintains those viewers. The adapter and generated layouts are authored here; no personal vault or Kepano template is copied. Source attribution, the immutable upstream publishing commit, and the wheel checksum are recorded in [the reuse notice](third-party-notices/obsidian-reuse.md) and [the optional requirements](requirements-obsidian.txt).

The public source builder includes both adapter modules, these instructions, the pinned requirements, and the reuse notice. It excludes dependency wheels and virtual environments. Upgrading the dependency requires an explicit pin and checksum change plus the bridge's real-library integration checks.
