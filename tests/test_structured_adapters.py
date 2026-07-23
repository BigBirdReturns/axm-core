from __future__ import annotations

import json
import sys
from argparse import Namespace
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "forge"))

from axm_forge.ingestion.structured import (  # noqa: E402
    extract_html_schemaorg,
    extract_package_graph,
    extract_package_manifest,
    extract_schemaorg,
)
from axm_forge.ingestion.extractors import extract  # noqa: E402
from axm_forge.ingestion.canonical import canonical_source_text  # noqa: E402
from axm_forge.cli.main import cmd_extract  # noqa: E402


def test_package_manifest_emits_tier0_edges_with_exact_evidence(tmp_path: Path) -> None:
    manifest = tmp_path / "package.json"
    manifest.write_text(json.dumps({
        "name": "real-project",
        "workspaces": ["apps/*"],
        "dependencies": {"alpha": "^1.2.0"},
        "devDependencies": {"beta": "~3.0.0"},
    }, indent=2), encoding="utf-8")

    document = extract_package_manifest(manifest)

    assert document.format == "package_manifest"
    assert {(row["object"], row["locator"]["dependency_group"]) for row in document.tier0_candidates or []} == {
        ("alpha", "runtime"),
        ("beta", "dev"),
    }
    assert all(row["tier"] == 0 for row in document.tier0_candidates or [])
    assert all(row["evidence"] in document.full_text for row in document.tier0_candidates or [])
    assert all(document.full_text.count(row["evidence"]) == 1 for row in document.tier0_candidates or [])
    assert len({row["locator"]["block_id"] for row in document.tier0_candidates or []}) == 2
    assert all(row["object"] != "apps/*" for row in document.tier0_candidates or [])


def test_package_graph_combines_observed_manifest_families(tmp_path: Path) -> None:
    npm = tmp_path / "web"
    npm.mkdir()
    (npm / "package.json").write_text(
        '{"name":"web","dependencies":{"react":"19"},"workspaces":["apps/*"]}', encoding="utf-8"
    )
    python = tmp_path / "service"
    python.mkdir()
    (python / "pyproject.toml").write_text(
        '[build-system]\nrequires=["hatchling>=1"]\n[project]\nname="service"\ndependencies=["fastapi>=1"]\n', encoding="utf-8"
    )
    go = tmp_path / "worker"
    go.mkdir()
    (go / "go.mod").write_text(
        'module example/worker\n\nrequire example/dependency v1.0.0\n', encoding="utf-8"
    )

    document = extract_package_graph([tmp_path])

    assert document.metadata["manifest_count"] == 3
    assert document.metadata["dependency_edge_count"] == 5
    assert document.tier0_candidates is None
    assert len({row["project_id"] for row in document.metadata["projects"]}) == 3
    assert all(
        not Path(row["project_id"].split("::", 1)[0]).is_absolute()
        for row in document.metadata["projects"]
    )
    assert {row["relation"] for row in document.metadata["edges"]} == {
        "depends_on", "has_workspace", "build_requires"
    }


def test_schemaorg_jsonld_preserves_json_pointer_and_evidence(tmp_path: Path) -> None:
    source = tmp_path / "dataset.jsonld"
    source.write_text(json.dumps({
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": "Observed Dataset",
        "alternateName": "Observed Dataset",
        "isAccessibleForFree": True,
        "creator": {"@type": "Organization", "name": "Observed Org"},
    }, indent=2), encoding="utf-8")

    document = extract_schemaorg(source)

    assert document.format == "schemaorg"
    assert any(row["predicate"] == "schema_type" and row["object"] == "Dataset" for row in document.tier0_candidates or [])
    assert any(row["locator"]["json_pointer"] == "/creator" for row in document.tier0_candidates or [])
    assert all(row["evidence"] in document.full_text for row in document.tier0_candidates or [])
    assert all(document.full_text.count(row["evidence"]) == 1 for row in document.tier0_candidates or [])
    assert all(row["locator"]["block_id"].startswith("json:") for row in document.tier0_candidates or [])
    assert any(
        row["object_type"] == "literal:boolean" and row["object"] == "true"
        for row in document.tier0_candidates or []
    )
    assert extract(source).format == "schemaorg"


def test_inline_schemaorg_is_lifted_without_flattening_html(tmp_path: Path) -> None:
    source = tmp_path / "index.html"
    source.write_text(
        '<html><script type="application/ld+json">'
        '{"@context":"https://schema.org","@type":"WebSite","name":"Observed Site"}'
        '</script><body>Visible</body></html>',
        encoding="utf-8",
    )

    document = extract_html_schemaorg(source)

    assert document.metadata["schemaorg_script_count"] == 1
    assert document.tier0_candidates
    assert document.blocks[0].text == canonical_source_text(source.read_text(encoding="utf-8"))
    assert all(row["locator"]["script_index"] == 0 for row in document.tier0_candidates)
    assert all(row["locator"]["block_id"].startswith("jsonld:0:") for row in document.tier0_candidates)
    assert all(document.full_text.count(row["evidence"]) == 1 for row in document.tier0_candidates)
    assert extract(source).format == "html_schemaorg"


def test_cli_plain_html_falls_back_from_schemaorg_adapter(tmp_path: Path) -> None:
    source = tmp_path / "index.html"
    raw = "<html><body><h1>Plain page</h1><p>No JSON-LD.</p></body></html>"
    source.write_text(raw, encoding="utf-8")
    out = tmp_path / "out"

    result = cmd_extract(Namespace(
        input=str(source),
        out=str(out),
        llm_key=None,
        enable_llm=False,
        llm_provider="ollama",
        llm_model="unused",
    ))

    assert result == 0
    source_outputs = list(out.glob("doc-*/source.txt"))
    assert len(source_outputs) == 1
    assert source_outputs[0].read_text(encoding="utf-8") == raw
    assert not (out / "source.txt").exists()
