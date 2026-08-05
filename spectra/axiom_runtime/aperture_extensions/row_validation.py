from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .contracts import (
    APERTURE_EXTENSION_SPECS,
    CUSTODY_STATES,
    EDGE_STRENGTHS,
    INTEGER_PATTERN,
    MAP_ID_PATTERN,
    NONNEGATIVE_PATTERN,
    PACKAGE_ID_PATTERN,
    POSITION_KINDS,
    POSITIVE_PATTERN,
    REVEAL_MODES,
    REVIEW_STATES,
    SEGMENT_KINDS,
    SHA256_PATTERN,
    ApertureExtensionError,
    ExtensionSpec,
)


def canonical_array(value: str, field: str, *, minimum: int = 0) -> List[str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ApertureExtensionError(f"{field} must be compact canonical JSON") from exc
    if not isinstance(parsed, list) or len(parsed) < minimum:
        raise ApertureExtensionError(f"{field} must contain at least {minimum} values")
    if any(not isinstance(item, str) or not item for item in parsed):
        raise ApertureExtensionError(f"{field} must contain non-empty strings only")
    if len(set(parsed)) != len(parsed):
        raise ApertureExtensionError(f"{field} must contain unique values")
    canonical = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
    if value != canonical:
        raise ApertureExtensionError(f"{field} is not canonical compact JSON")
    return parsed


def _decimal_in_unit_interval(value: str, field: str) -> None:
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ApertureExtensionError(f"{field} must be a decimal string") from exc
    if not number.is_finite() or number < 0 or number > 1:
        raise ApertureExtensionError(f"{field} must be in [0,1]")


def _require_pattern(value: str, pattern: re.Pattern[str], field: str) -> None:
    if not pattern.fullmatch(value):
        raise ApertureExtensionError(f"{field} has invalid format")


def _require_nonempty(value: str, field: str) -> None:
    if not value:
        raise ApertureExtensionError(f"{field} must be non-empty")


def _validate_row_shape(spec: ExtensionSpec, row: Mapping[str, Any], index: int) -> Dict[str, str]:
    keys = set(row)
    expected = set(spec.columns)
    missing = expected - keys
    extra = keys - expected
    if missing or extra:
        raise ApertureExtensionError(
            f"{spec.extension_id} row {index} key mismatch: missing={sorted(missing)} extra={sorted(extra)}"
        )
    if any(not isinstance(row[column], str) for column in spec.columns):
        raise ApertureExtensionError(f"{spec.extension_id} row {index} must use JSON strings only")
    return {column: str(row[column]) for column in spec.columns}


def validate_extension_rows(
    spec: ExtensionSpec,
    raw_rows: Sequence[Mapping[str, Any]],
) -> List[Dict[str, str]]:
    rows = [_validate_row_shape(spec, row, index) for index, row in enumerate(raw_rows)]
    identities: set[Tuple[str, ...]] = set()
    for index, row in enumerate(rows):
        identity = tuple(row[column] for column in spec.primary_key)
        if identity in identities:
            raise ApertureExtensionError(f"{spec.extension_id} duplicate primary key {identity!r}")
        identities.add(identity)
        prefix = f"{spec.extension_id} row {index}"

        if "package_id" in row:
            _require_pattern(row["package_id"], PACKAGE_ID_PATTERN, f"{prefix}.package_id")
        if "revision" in row:
            _require_pattern(row["revision"], POSITIVE_PATTERN, f"{prefix}.revision")
        if "map_id" in row:
            _require_pattern(row["map_id"], MAP_ID_PATTERN, f"{prefix}.map_id")
        if "canonical_story_digest" in row:
            _require_pattern(row["canonical_story_digest"], SHA256_PATTERN, f"{prefix}.canonical_story_digest")
        if "sha256" in row:
            _require_pattern(row["sha256"], SHA256_PATTERN, f"{prefix}.sha256")
        if "review_state" in row and row["review_state"] not in REVIEW_STATES:
            raise ApertureExtensionError(f"{prefix}.review_state is unsupported")
        for field in (
            "work_id", "canonical_edition_id", "provider_edition_id", "position_id",
            "fact_id", "edge_id", "reveal_id", "segment_id", "source_id",
        ):
            if field in row:
                _require_nonempty(row[field], f"{prefix}.{field}")

        if spec.extension_id == "aperture-package-revisions@1":
            if row["supersedes"] and not PACKAGE_ID_PATTERN.fullmatch(row["supersedes"]):
                raise ApertureExtensionError(f"{prefix}.supersedes must be empty or a package identity")
            canonical_array(row["edition_time_map_refs_json"], f"{prefix}.edition_time_map_refs_json", minimum=1)
        elif spec.extension_id == "aperture-positions@1":
            _require_pattern(row["canonical_start_us"], NONNEGATIVE_PATTERN, f"{prefix}.canonical_start_us")
            _require_pattern(row["canonical_end_us"], POSITIVE_PATTERN, f"{prefix}.canonical_end_us")
            if int(row["canonical_end_us"]) <= int(row["canonical_start_us"]):
                raise ApertureExtensionError(f"{prefix} must have a positive canonical interval")
            if row["kind"] not in POSITION_KINDS:
                raise ApertureExtensionError(f"{prefix}.kind is unsupported")
            _require_nonempty(row["label"], f"{prefix}.label")
        elif spec.extension_id == "aperture-facts@1":
            _require_nonempty(row["proposition"], f"{prefix}.proposition")
            _require_nonempty(row["first_reveal_position_id"], f"{prefix}.first_reveal_position_id")
            canonical_array(row["subject_ids_json"], f"{prefix}.subject_ids_json")
            canonical_array(row["provenance_refs_json"], f"{prefix}.provenance_refs_json", minimum=1)
        elif spec.extension_id == "aperture-causal-edges@1":
            canonical_array(row["cause_fact_ids_json"], f"{prefix}.cause_fact_ids_json", minimum=1)
            _require_nonempty(row["effect_fact_id"], f"{prefix}.effect_fact_id")
            if row["strength"] not in EDGE_STRENGTHS:
                raise ApertureExtensionError(f"{prefix}.strength is unsupported")
            canonical_array(row["provenance_refs_json"], f"{prefix}.provenance_refs_json", minimum=1)
        elif spec.extension_id == "aperture-reveals@1":
            _require_nonempty(row["position_id"], f"{prefix}.position_id")
            if row["mode"] not in REVEAL_MODES:
                raise ApertureExtensionError(f"{prefix}.mode is unsupported")
            canonical_array(row["provenance_refs_json"], f"{prefix}.provenance_refs_json", minimum=1)
        elif spec.extension_id == "aperture-edition-maps@1":
            _validate_time_map_row(row, prefix)
        elif spec.extension_id == "aperture-sources@1":
            if row["custody"] not in CUSTODY_STATES:
                raise ApertureExtensionError(f"{prefix}.custody is unsupported")
            if row["contains_redistributable_text"] not in {"true", "false"}:
                raise ApertureExtensionError(
                    f"{prefix}.contains_redistributable_text must be true or false"
                )
    return rows


def _validate_time_map_row(row: Mapping[str, str], prefix: str) -> None:
    if row["kind"] not in SEGMENT_KINDS:
        raise ApertureExtensionError(f"{prefix}.kind is unsupported")
    for field in ("provider_start_us", "provider_end_us", "canonical_start_us", "canonical_end_us"):
        if row[field]:
            _require_pattern(row[field], NONNEGATIVE_PATTERN, f"{prefix}.{field}")
    if bool(row["provider_start_us"]) != bool(row["provider_end_us"]):
        raise ApertureExtensionError(f"{prefix} has an incomplete provider interval")
    if bool(row["canonical_start_us"]) != bool(row["canonical_end_us"]):
        raise ApertureExtensionError(f"{prefix} has an incomplete canonical interval")
    provider_pair = bool(row["provider_start_us"])
    canonical_pair = bool(row["canonical_start_us"])
    if provider_pair and int(row["provider_end_us"]) <= int(row["provider_start_us"]):
        raise ApertureExtensionError(f"{prefix} provider interval must be positive")
    if canonical_pair and int(row["canonical_end_us"]) <= int(row["canonical_start_us"]):
        raise ApertureExtensionError(f"{prefix} canonical interval must be positive")
    expected_pairs = {
        "mapped": (True, True),
        "provider_only": (True, False),
        "canonical_only": (False, True),
    }
    if (provider_pair, canonical_pair) != expected_pairs[row["kind"]]:
        raise ApertureExtensionError(f"{prefix} interval presence contradicts segment kind")
    _require_pattern(row["rate_numerator"], INTEGER_PATTERN, f"{prefix}.rate_numerator")
    _require_pattern(row["rate_denominator"], POSITIVE_PATTERN, f"{prefix}.rate_denominator")
    canonical_array(row["evidence_refs_json"], f"{prefix}.evidence_refs_json")
    source_digests = canonical_array(
        row["source_digests_json"], f"{prefix}.source_digests_json", minimum=1
    )
    for digest in source_digests:
        _require_pattern(digest, SHA256_PATTERN, f"{prefix}.source_digests_json")
    _decimal_in_unit_interval(row["confidence"], f"{prefix}.confidence")
