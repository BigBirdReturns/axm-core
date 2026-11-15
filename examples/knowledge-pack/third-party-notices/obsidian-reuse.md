# Obsidian ecosystem reuse

## obsidian-wiki

- Project: [Ar9av/obsidian-wiki](https://github.com/Ar9av/obsidian-wiki).
- Optional dependency: [obsidian-wiki 2026.11.4 on PyPI](https://pypi.org/project/obsidian-wiki/2026.11.4/).
- License: MIT; copyright (c) 2026 Ar9av. The upstream [license](https://github.com/Ar9av/obsidian-wiki/blob/35a91f803951c192feade6372681caaa5785381e/LICENSE) travels with the installed distribution.
- Publishing commit recorded by PyPI: [35a91f803951c192feade6372681caaa5785381e](https://github.com/Ar9av/obsidian-wiki/tree/35a91f803951c192feade6372681caaa5785381e).
- Wheel: `obsidian_wiki-2026.11.4-py3-none-any.whl`.
- Wheel SHA-256: `daedd59bb2557fd27ed375f54482f861007b593aff48932931e8fd0085964a37`.
- Upstream API used: `from obsidian_wiki import Memory`, with `Memory.search` and `Memory.context` on an explicit temporary derived vault.

AXM installs and calls this maintained library without copying or modifying its implementation. The public source package carries the pin, checksum, adapter and this notice; it carries no upstream wheel, virtual environment or copied skill collection. The optional base package has no additional runtime dependency requirement. This integration does not activate upstream setup, agent hooks, network services or command-line auto-update behavior.

## JSON Canvas

The exporter targets [JSON Canvas specification 1.0](https://jsoncanvas.org/spec/1.0/), dated 2024-03-11. The [official repository](https://github.com/obsidianmd/jsoncanvas) and [MIT license](https://github.com/obsidianmd/jsoncanvas/blob/main/LICENSE) attribute copyright (c) 2024 Obsidian.md. The adapter writes its own nodes and edges conforming to this format; it includes no copied JSON Canvas implementation.

## Obsidian Bases

The evidence table follows the official [Bases syntax](https://help.obsidian.md/bases/syntax): a native `.base` document with filters, properties and views. This is format interoperability with the maintained Obsidian viewer. It bundles no Obsidian application or community plugin.

## AXM-authored boundary

`obsidian_bridge.py` and `obsidian_export.py` are authored within this Apache-2.0 reference. Their layouts and Markdown projection are not copied from `kepano/kepano-obsidian` or any personal vault template. Genesis retains cryptographic verification and canonical identity ownership. The upstream search library operates only on the derived presentation.
