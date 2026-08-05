from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Tuple

APERTURE_EXTENSION_RUNTIME_FORMAT = "axm-core-aperture-extension-runtime/1"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
PACKAGE_ID_PATTERN = re.compile(r"^storypkg1_[0-9a-f]{64}$")
MAP_ID_PATTERN = re.compile(r"^timemap1_[0-9a-f]{64}$")
NONNEGATIVE_PATTERN = re.compile(r"^(0|[1-9][0-9]*)$")
POSITIVE_PATTERN = re.compile(r"^[1-9][0-9]*$")
INTEGER_PATTERN = re.compile(r"^-?(0|[1-9][0-9]*)$")

REVIEW_STATES = {"candidate", "reviewed", "published", "superseded"}
POSITION_KINDS = {"sequence", "scene", "beat", "event"}
EDGE_STRENGTHS = {"necessary", "strong", "contextual"}
REVEAL_MODES = {"seen", "heard", "explained", "outcome-spoiled"}
SEGMENT_KINDS = {"mapped", "provider_only", "canonical_only"}
CUSTODY_STATES = {"public", "holder_controlled", "derived"}


class ApertureExtensionError(ValueError):
    """Registered Aperture extension bytes violate the frozen contract."""


@dataclass(frozen=True)
class ExtensionSpec:
    extension_id: str
    table_name: str
    columns: Tuple[str, ...]
    primary_key: Tuple[str, ...]


@dataclass(frozen=True)
class ApertureExtensionMount:
    format: str
    mount_id: str
    manifest_sha256: str
    source_path: str
    extension_ids: Tuple[str, ...]
    tables: Tuple[str, ...]
    authority: str = "rebuildable_query_cache_only"


SPECS: Tuple[ExtensionSpec, ...] = (
    ExtensionSpec(
        "aperture-package-revisions@1",
        "aperture_package_revisions",
        (
            "package_id", "revision", "work_id", "canonical_story_digest",
            "canonical_edition_id", "review_state", "supersedes",
            "edition_time_map_refs_json",
        ),
        ("package_id", "revision"),
    ),
    ExtensionSpec(
        "aperture-positions@1",
        "aperture_positions",
        (
            "package_id", "revision", "position_id", "canonical_start_us",
            "canonical_end_us", "kind", "parent_id", "label",
        ),
        ("package_id", "revision", "position_id"),
    ),
    ExtensionSpec(
        "aperture-facts@1",
        "aperture_facts",
        (
            "package_id", "revision", "fact_id", "proposition",
            "first_reveal_position_id", "subject_ids_json", "provenance_refs_json",
        ),
        ("package_id", "revision", "fact_id"),
    ),
    ExtensionSpec(
        "aperture-causal-edges@1",
        "aperture_causal_edges",
        (
            "package_id", "revision", "edge_id", "cause_fact_ids_json",
            "effect_fact_id", "strength", "provenance_refs_json",
        ),
        ("package_id", "revision", "edge_id"),
    ),
    ExtensionSpec(
        "aperture-reveals@1",
        "aperture_reveals",
        (
            "package_id", "revision", "reveal_id", "fact_id", "position_id",
            "mode", "provenance_refs_json",
        ),
        ("package_id", "revision", "reveal_id"),
    ),
    ExtensionSpec(
        "aperture-edition-maps@1",
        "aperture_edition_maps",
        (
            "map_id", "work_id", "provider_edition_id", "canonical_edition_id",
            "segment_id", "kind", "provider_start_us", "provider_end_us",
            "canonical_start_us", "canonical_end_us", "rate_numerator",
            "rate_denominator", "evidence_refs_json", "source_digests_json",
            "confidence", "review_state",
        ),
        ("map_id", "segment_id"),
    ),
    ExtensionSpec(
        "aperture-sources@1",
        "aperture_sources",
        (
            "package_id", "revision", "source_id", "sha256", "custody",
            "contains_redistributable_text",
        ),
        ("package_id", "revision", "source_id"),
    ),
)

APERTURE_EXTENSION_SPECS: Mapping[str, ExtensionSpec] = {
    spec.extension_id: spec for spec in SPECS
}
