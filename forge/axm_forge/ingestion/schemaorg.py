"""Schema.org JSON-LD lift into Forge tier-0 candidates."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def is_schemaorg_data(data: Any) -> bool:
    if isinstance(data, list):
        return any(is_schemaorg_data(item) for item in data)
    if not isinstance(data, dict):
        return False
    context = data.get("@context")
    if isinstance(context, str) and "schema.org" in context.casefold():
        return True
    if isinstance(context, list) and any("schema.org" in str(item).casefold() for item in context):
        return True
    return "@graph" in data and any(is_schemaorg_data(item) for item in data.get("@graph", []))


def _escape_pointer(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _label(item: dict[str, Any], pointer: str) -> str:
    for key in ("name", "headline", "title", "identifier", "@id", "@type"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return pointer or "schema:Thing"


class _JsonSpans:
    """Map JSON Pointers to exact source spans without normalizing the JSON."""

    def __init__(self, raw: str) -> None:
        self.raw = raw
        self.index = 0
        self.value_spans: dict[str, tuple[int, int]] = {}
        self.member_spans: dict[str, tuple[int, int]] = {}

    def parse(self) -> None:
        self._parse_value("")

    def _skip_space(self) -> None:
        while self.index < len(self.raw) and self.raw[self.index].isspace():
            self.index += 1

    def _parse_string(self) -> tuple[str, int, int]:
        start = self.index
        self.index += 1
        while self.index < len(self.raw):
            char = self.raw[self.index]
            if char == "\\":
                self.index += 2
                continue
            self.index += 1
            if char == '"':
                break
        end = self.index
        return json.loads(self.raw[start:end]), start, end

    def _parse_value(self, pointer: str) -> tuple[int, int]:
        self._skip_space()
        start = self.index
        char = self.raw[self.index]
        if char == "{":
            self.index += 1
            self._skip_space()
            if self.raw[self.index] != "}":
                while True:
                    self._skip_space()
                    key, member_start, _ = self._parse_string()
                    self._skip_space()
                    if self.raw[self.index] != ":":
                        raise ValueError("Invalid JSON object member")
                    self.index += 1
                    child = f"{pointer}/{_escape_pointer(key)}"
                    _, member_end = self._parse_value(child)
                    self.member_spans[child] = (member_start, member_end)
                    self._skip_space()
                    if self.raw[self.index] == "}":
                        break
                    if self.raw[self.index] != ",":
                        raise ValueError("Invalid JSON object separator")
                    self.index += 1
            self.index += 1
        elif char == "[":
            self.index += 1
            self._skip_space()
            item_index = 0
            if self.raw[self.index] != "]":
                while True:
                    child = f"{pointer}/{item_index}"
                    child_start, child_end = self._parse_value(child)
                    self.member_spans[child] = (child_start, child_end)
                    item_index += 1
                    self._skip_space()
                    if self.raw[self.index] == "]":
                        break
                    if self.raw[self.index] != ",":
                        raise ValueError("Invalid JSON array separator")
                    self.index += 1
            self.index += 1
        elif char == '"':
            self._parse_string()
        else:
            while (
                self.index < len(self.raw)
                and self.raw[self.index] not in ",]}"
                and not self.raw[self.index].isspace()
            ):
                self.index += 1
        end = self.index
        self.value_spans[pointer] = (start, end)
        return start, end

    def span(self, pointer: str) -> tuple[int, int]:
        return self.member_spans.get(pointer, self.value_spans[pointer])


def _unique_slice(
    raw: str,
    start: int,
    end: int,
    used: set[str],
) -> str:
    """Expand a structural span only until it is unique in this source."""
    left, right = start, end
    while True:
        evidence = raw[left:right]
        if raw.count(evidence) == 1 and evidence not in used:
            used.add(evidence)
            return evidence
        if left == 0 and right == len(raw):
            return evidence
        left = max(0, left - 1)
        right = min(len(raw), right + 1)


def schemaorg_candidates(
    data: Any,
    *,
    raw: str,
    source_path: Path,
    locator_extra: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Lift JSON-LD scalar properties and typed-object edges with JSON pointers."""
    candidates: list[dict[str, Any]] = []
    extra = dict(locator_extra or {})
    spans = _JsonSpans(raw)
    spans.parse()
    used_evidence: set[str] = set()
    evidence_by_pointer: dict[str, str] = {}

    def add(subject: str, predicate: str, value: Any, object_type: str, pointer: str) -> None:
        script_index = extra.get("script_index")
        block_prefix = f"jsonld:{script_index}" if script_index is not None else "json"
        object_value = str(value).lower() if isinstance(value, bool) else str(value)
        if pointer not in evidence_by_pointer:
            evidence_by_pointer[pointer] = _unique_slice(
                raw, *spans.span(pointer), used_evidence
            )
        candidates.append({
            "subject": subject,
            "predicate": predicate,
            "object": object_value,
            "object_type": object_type,
            "tier": 0,
            "confidence": 1.0,
            "evidence": evidence_by_pointer[pointer],
            "locator": {
                "kind": "schemaorg",
                "file_path": str(source_path),
                "json_pointer": pointer,
                **extra,
                "block_id": f"{block_prefix}:{pointer or '/'}",
            },
        })

    def walk(value: Any, pointer: str, parent: str | None = None, relation: str | None = None) -> None:
        if isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{pointer}/{index}", parent, relation)
            return
        if not isinstance(value, dict):
            if parent and relation and value is not None:
                add(parent, relation, value, "literal:boolean" if isinstance(value, bool) else "literal:string", pointer)
            return

        subject = _label(value, pointer)
        if parent and relation:
            add(parent, relation, subject, "entity", pointer)

        schema_type = value.get("@type")
        if isinstance(schema_type, list):
            for index, item in enumerate(schema_type):
                add(subject, "schema_type", item, "entity", f"{pointer}/@type/{index}")
        elif schema_type:
            add(subject, "schema_type", schema_type, "entity", f"{pointer}/@type")

        for key, item in value.items():
            if key in {"@context", "@type"}:
                continue
            child_pointer = f"{pointer}/{_escape_pointer(key)}"
            predicate = "identifier" if key == "@id" else key
            if isinstance(item, (dict, list)):
                walk(item, child_pointer, subject, predicate)
            elif item is not None:
                object_type = "literal:boolean" if isinstance(item, bool) else "literal:string"
                add(subject, predicate, item, object_type, child_pointer)

    if isinstance(data, dict) and isinstance(data.get("@graph"), list):
        walk(data["@graph"], "/@graph")
    else:
        walk(data, "")
    return candidates
