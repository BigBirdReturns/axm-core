"""Deterministic package-manifest parsing for Forge tier-0 ingestion.

The parser intentionally covers only manifest families observed in the durable
Projects estate: npm ``package.json``, PEP 621/Poetry ``pyproject.toml``,
``requirements*.txt``, and Go ``go.mod`` files.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from axm_forge.ingestion.canonical import canonical_source_text

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]


_SKIP_PARTS = {
    ".git", ".venv", "venv", "sepenv", "node_modules", "__pycache__",
    ".pnpm-store", "dist", "release", "cloud", "recovery",
}
_REQUIREMENTS_RE = re.compile(r"^requirements(?:[-_.].*)?\.txt$", re.I)
_REQ_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


@dataclass(frozen=True)
class PackageDependency:
    name: str
    constraint: str
    group: str
    evidence: str
    line_number: int
    relation: str = "depends_on"


@dataclass(frozen=True)
class PackageProject:
    path: Path
    name: str
    ecosystem: str
    version: str
    raw: str
    dependencies: tuple[PackageDependency, ...]


def is_package_manifest(path: Path) -> bool:
    name = path.name
    return name in {"package.json", "pyproject.toml", "go.mod"} or bool(
        _REQUIREMENTS_RE.match(name)
    )


def discover_package_manifests(roots: Iterable[Path]) -> list[Path]:
    """Find supported manifests below one or more real roots, without caches."""
    found: dict[str, Path] = {}
    for raw_root in roots:
        root = Path(raw_root)
        if root.is_file():
            candidates = [root]
        elif root.is_dir():
            candidates = root.rglob("*")
        else:
            continue
        for path in candidates:
            if not path.is_file() or not is_package_manifest(path):
                continue
            if any(part.casefold() in _SKIP_PARTS for part in path.parts):
                continue
            key = str(path.resolve()).casefold()
            found[key] = path.resolve()
    return sorted(found.values(), key=lambda path: str(path).casefold())


def _line_evidence(raw: str, *tokens: str) -> tuple[str, int]:
    lowered = [token.casefold() for token in tokens if token]
    for line_number, line in enumerate(raw.splitlines(), start=1):
        folded = line.casefold()
        if all(token in folded for token in lowered):
            evidence = line.strip()
            if evidence:
                return evidence, line_number
    fallback = next((line.strip() for line in raw.splitlines() if line.strip()), "manifest")
    return fallback, 1


def _dependency(
    name: str,
    constraint: Any,
    group: str,
    raw: str,
    relation: str = "depends_on",
) -> PackageDependency:
    constraint_text = "" if constraint is None else str(constraint).strip()
    evidence, line_number = _line_evidence(raw, name, constraint_text)
    return PackageDependency(name, constraint_text, group, evidence, line_number, relation)


def _parse_npm(path: Path, raw: str) -> PackageProject:
    data = json.loads(raw)
    name = str(data.get("name") or path.parent.name)
    version = str(data.get("version") or "")
    dependencies: list[PackageDependency] = []
    for field, group in (
        ("dependencies", "runtime"),
        ("devDependencies", "dev"),
        ("peerDependencies", "peer"),
        ("optionalDependencies", "optional"),
    ):
        for dep_name, constraint in (data.get(field) or {}).items():
            dependencies.append(_dependency(str(dep_name), constraint, group, raw))
    workspaces = data.get("workspaces") or []
    if isinstance(workspaces, dict):
        workspaces = workspaces.get("packages") or []
    for workspace in workspaces:
        dependencies.append(_dependency(
            str(workspace), "", "workspace", raw, relation="has_workspace"
        ))
    return PackageProject(path, name, "npm", version, raw, tuple(dependencies))


def _requirement_parts(spec: str) -> tuple[str, str] | None:
    cleaned = spec.split(";", 1)[0].strip()
    if not cleaned or cleaned.startswith(("#", "-", "http://", "https://", "git+")):
        return None
    match = _REQ_NAME_RE.match(cleaned)
    if not match:
        return None
    name = match.group(1)
    constraint = cleaned[match.end():].strip()
    return name, constraint


def _parse_requirement_list(
    specs: Iterable[Any], group: str, raw: str
) -> list[PackageDependency]:
    dependencies: list[PackageDependency] = []
    for spec in specs:
        parts = _requirement_parts(str(spec))
        if parts:
            dependencies.append(_dependency(parts[0], parts[1], group, raw))
    return dependencies


def _parse_pyproject(path: Path, raw: str) -> PackageProject:
    data = tomllib.loads(raw)
    project = data.get("project") or {}
    poetry = ((data.get("tool") or {}).get("poetry") or {})
    name = str(project.get("name") or poetry.get("name") or path.parent.name)
    version = str(project.get("version") or poetry.get("version") or "")
    dependencies = _parse_requirement_list(project.get("dependencies") or [], "runtime", raw)
    for group, specs in (project.get("optional-dependencies") or {}).items():
        dependencies.extend(_parse_requirement_list(specs, str(group), raw))
    for spec in ((data.get("build-system") or {}).get("requires") or []):
        parts = _requirement_parts(str(spec))
        if parts:
            dependencies.append(_dependency(
                parts[0], parts[1], "build", raw, relation="build_requires"
            ))

    for dep_name, constraint in (poetry.get("dependencies") or {}).items():
        if str(dep_name).casefold() == "python":
            continue
        value = constraint.get("version", "") if isinstance(constraint, dict) else constraint
        dependencies.append(_dependency(str(dep_name), value, "runtime", raw))
    poetry_groups = poetry.get("group") or {}
    for group, group_data in poetry_groups.items():
        for dep_name, constraint in ((group_data or {}).get("dependencies") or {}).items():
            value = constraint.get("version", "") if isinstance(constraint, dict) else constraint
            dependencies.append(_dependency(str(dep_name), value, str(group), raw))
    return PackageProject(path, name, "pypi", version, raw, tuple(dependencies))


def _parse_requirements(path: Path, raw: str) -> PackageProject:
    dependencies: list[PackageDependency] = []
    for line_number, line in enumerate(raw.splitlines(), start=1):
        stripped = line.strip()
        parts = _requirement_parts(stripped)
        if not parts:
            continue
        dependencies.append(PackageDependency(parts[0], parts[1], "runtime", stripped, line_number))
    return PackageProject(path, path.parent.name, "pypi", "", raw, tuple(dependencies))


def _parse_go_mod(path: Path, raw: str) -> PackageProject:
    module_match = re.search(r"(?m)^\s*module\s+(\S+)", raw)
    name = module_match.group(1) if module_match else path.parent.name
    dependencies: list[PackageDependency] = []
    in_require_block = False
    for line_number, line in enumerate(raw.splitlines(), start=1):
        stripped = line.strip()
        if stripped == "require (":
            in_require_block = True
            continue
        if in_require_block and stripped == ")":
            in_require_block = False
            continue
        body = stripped
        if body.startswith("require "):
            body = body[len("require "):].strip()
        elif not in_require_block:
            continue
        body = body.split("//", 1)[0].strip()
        parts = body.split()
        if len(parts) >= 2:
            dependencies.append(PackageDependency(parts[0], parts[1], "runtime", stripped, line_number))
    return PackageProject(path, name, "go", "", raw, tuple(dependencies))


def parse_package_manifest(path: Path) -> PackageProject:
    path = Path(path)
    if not is_package_manifest(path):
        raise ValueError(f"Unsupported package manifest: {path.name}")
    raw = canonical_source_text(path.read_text(encoding="utf-8", errors="replace"))
    if path.name == "package.json":
        return _parse_npm(path, raw)
    if path.name == "pyproject.toml":
        return _parse_pyproject(path, raw)
    if path.name == "go.mod":
        return _parse_go_mod(path, raw)
    return _parse_requirements(path, raw)


def _root_labels(roots: list[Path]) -> dict[Path, str]:
    """Use the shortest path suffix that uniquely identifies each input root."""
    labels: dict[Path, str] = {}
    for root in roots:
        parts = root.parts
        for length in range(1, len(parts) + 1):
            suffix = tuple(part.casefold() for part in parts[-length:])
            matches = sum(
                tuple(part.casefold() for part in other.parts[-length:]) == suffix
                for other in roots
                if len(other.parts) >= length
            )
            if matches == 1:
                labels[root] = "/".join(parts[-length:])
                break
    return labels


def _project_id(path: Path, roots: list[Path], labels: dict[Path, str]) -> str:
    matching = [root for root in roots if path == root or root in path.parents]
    root = max(matching, key=lambda item: len(item.parts))
    relative = path.relative_to(root) if path != root else Path(path.name)
    return f"{labels[root]}::{relative.as_posix()}"


def build_package_graph(roots: Iterable[Path]) -> dict[str, Any]:
    """Build a deterministic multi-root project/dependency graph."""
    root_list = sorted(
        {Path(root).resolve() for root in roots}, key=lambda path: str(path).casefold()
    )
    labels = _root_labels(root_list)
    projects = [
        parse_package_manifest(path) for path in discover_package_manifests(root_list)
    ]
    project_rows = [
        {
            "project_id": _project_id(project.path.resolve(), root_list, labels),
            "name": project.name,
            "ecosystem": project.ecosystem,
            "version": project.version,
            "manifest": str(project.path),
        }
        for project in projects
    ]
    edges = [
        {
            "project_id": _project_id(project.path.resolve(), root_list, labels),
            "project": project.name,
            "relation": dep.relation,
            "dependency": dep.name,
            "constraint": dep.constraint,
            "group": dep.group,
            "ecosystem": project.ecosystem,
            "manifest": str(project.path),
            "line_number": dep.line_number,
        }
        for project in projects
        for dep in project.dependencies
    ]
    return {
        "projects": project_rows,
        "edges": edges,
        "manifest_count": len(project_rows),
        "dependency_edge_count": len(edges),
    }
