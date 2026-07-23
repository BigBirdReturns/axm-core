"""Reality-backed structured adapters for AXM Forge.

The public ``extract()`` router and Forge CLI delegate supported real files
here. The module also exposes a package-estate graph command::

    python -m axm_forge.ingestion.structured extract FILE --out OUT
    python -m axm_forge.ingestion.structured package-graph ROOT [ROOT ...] --out OUT
"""
from __future__ import annotations

import argparse
import json
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

from axm_forge.ingestion.canonical import canonical_source_text
from axm_forge.ingestion.extractors import DocumentBlock, ExtractedDocument
from axm_forge.ingestion.package_graph import (
    build_package_graph,
    is_package_manifest,
    parse_package_manifest,
)
from axm_forge.ingestion.schemaorg import is_schemaorg_data, schemaorg_candidates


class _JsonLdScriptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._capture = False
        self._parts: list[str] = []
        self.scripts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "script":
            return
        values = {key.casefold(): (value or "") for key, value in attrs}
        if values.get("type", "").casefold() == "application/ld+json":
            self._capture = True
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "script" and self._capture:
            self.scripts.append("".join(self._parts))
            self._capture = False
            self._parts = []


def _line_evidence(raw: str, line_number: int) -> str:
    lines = raw.splitlines(keepends=True)
    if not lines:
        return raw
    index = min(max(line_number - 1, 0), len(lines) - 1)
    starts = []
    offset = 0
    for line in lines:
        starts.append(offset)
        offset += len(line)
    start = starts[index]
    end = start + len(lines[index].rstrip("\r\n"))
    left, right = start, end
    while raw.count(raw[left:right]) != 1:
        if left == 0 and right == len(raw):
            break
        left = max(0, left - 1)
        right = min(len(raw), right + 1)
    return raw[left:right]


def extract_schemaorg(path: Path) -> ExtractedDocument:
    path = Path(path)
    raw = canonical_source_text(path.read_text(encoding="utf-8", errors="replace"))
    data = json.loads(raw)
    if not is_schemaorg_data(data):
        raise ValueError(f"JSON-LD file is not Schema.org data: {path}")
    candidates = schemaorg_candidates(data, raw=raw, source_path=path)
    return ExtractedDocument(
        source_path=str(path),
        format="schemaorg",
        blocks=[DocumentBlock(
            text=raw,
            locator={"kind": "schemaorg", "file_path": str(path), "json_pointer": ""},
        )],
        tier0_candidates=candidates or None,
        metadata={"candidate_count": len(candidates)},
    )


def extract_html_schemaorg(path: Path) -> ExtractedDocument:
    path = Path(path)
    raw = canonical_source_text(path.read_text(encoding="utf-8", errors="replace"))
    parser = _JsonLdScriptParser()
    parser.feed(raw)
    candidates: list[dict[str, Any]] = []
    matched_scripts = 0
    for script_index, script_raw in enumerate(parser.scripts):
        try:
            data = json.loads(script_raw)
        except json.JSONDecodeError:
            continue
        if not is_schemaorg_data(data):
            continue
        matched_scripts += 1
        candidates.extend(schemaorg_candidates(
            data,
            raw=script_raw,
            source_path=path,
            locator_extra={"script_index": script_index},
        ))
    return ExtractedDocument(
        source_path=str(path),
        format="html_schemaorg",
        blocks=[DocumentBlock(
            text=raw,
            locator={"kind": "html", "file_path": str(path)},
        )],
        tier0_candidates=candidates or None,
        metadata={
            "jsonld_script_count": len(parser.scripts),
            "schemaorg_script_count": matched_scripts,
            "candidate_count": len(candidates),
        },
    )


def extract_package_manifest(path: Path) -> ExtractedDocument:
    path = Path(path)
    project = parse_package_manifest(path)
    candidates = [
        {
            "subject": project.name,
            "predicate": dependency.relation,
            "object": dependency.name,
            "object_type": "entity",
            "tier": 0,
            "confidence": 1.0,
            "evidence": _line_evidence(project.raw, dependency.line_number),
            "locator": {
                "kind": "package_manifest",
                "file_path": str(path),
                "block_id": f"package-line:{dependency.line_number}",
                "line_number": dependency.line_number,
                "dependency_group": dependency.group,
                "version_constraint": dependency.constraint,
                "ecosystem": project.ecosystem,
            },
        }
        for dependency in project.dependencies
        if dependency.relation != "has_workspace"
    ]
    return ExtractedDocument(
        source_path=str(path),
        format="package_manifest",
        blocks=[DocumentBlock(
            text=project.raw,
            locator={"kind": "package_manifest", "file_path": str(path)},
        )],
        tier0_candidates=candidates or None,
        metadata={
            "package_name": project.name,
            "ecosystem": project.ecosystem,
            "version": project.version,
            "dependency_count": len(project.dependencies),
        },
    )


def extract_package_graph(roots: Iterable[Path]) -> ExtractedDocument:
    root_list = [Path(root) for root in roots]
    return ExtractedDocument(
        source_path=";".join(str(root) for root in root_list),
        format="package_graph",
        blocks=[],
        tier0_candidates=None,
        metadata=build_package_graph(root_list),
    )


def extract_structured(path: Path) -> ExtractedDocument:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    if is_package_manifest(path):
        return extract_package_manifest(path)
    if path.suffix.casefold() in {".jsonld", ".json"}:
        return extract_schemaorg(path)
    if path.suffix.casefold() in {".html", ".htm"}:
        return extract_html_schemaorg(path)
    raise ValueError(f"No reality-backed structured adapter for: {path}")


def _write_document(document: ExtractedDocument, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "source.txt").write_text(document.full_text, encoding="utf-8")
    with (out_dir / "candidates.jsonl").open("w", encoding="utf-8") as handle:
        for candidate in document.tier0_candidates or []:
            handle.write(json.dumps(candidate, ensure_ascii=False, sort_keys=True) + "\n")
    if document.format == "package_graph":
        (out_dir / "package-graph.json").write_text(
            json.dumps(document.metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reality-backed Forge structured adapters")
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract_parser = subparsers.add_parser("extract")
    extract_parser.add_argument("input", type=Path)
    extract_parser.add_argument("--out", type=Path, required=True)

    graph_parser = subparsers.add_parser("package-graph")
    graph_parser.add_argument("roots", type=Path, nargs="+")
    graph_parser.add_argument("--out", type=Path, required=True)

    args = parser.parse_args(argv)
    document = (
        extract_structured(args.input)
        if args.command == "extract"
        else extract_package_graph(args.roots)
    )
    _write_document(document, args.out)
    print(json.dumps({
        "format": document.format,
        "source_count": len(document.blocks),
        "candidate_count": len(document.tier0_candidates or []),
        **({
            "manifest_count": document.metadata["manifest_count"],
            "dependency_edge_count": document.metadata["dependency_edge_count"],
        } if document.format == "package_graph" else {}),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
