from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .contracts import APERTURE_EXTENSION_SPECS, ApertureExtensionError
from .row_validation import canonical_array, validate_extension_rows


def validate_aperture_extension_bundle(
    raw_bundle: Mapping[str, Sequence[Mapping[str, Any]]],
) -> Dict[str, List[Dict[str, str]]]:
    """Validate all registered tables together before any cache mutation."""
    unknown = set(raw_bundle) - set(APERTURE_EXTENSION_SPECS)
    if unknown:
        raise ApertureExtensionError(f"unknown Aperture extension ids: {sorted(unknown)}")
    if not raw_bundle:
        raise ApertureExtensionError("no registered Aperture extensions were supplied")
    bundle = {
        extension_id: validate_extension_rows(APERTURE_EXTENSION_SPECS[extension_id], rows)
        for extension_id, rows in raw_bundle.items()
    }

    revisions = {
        (row["package_id"], row["revision"]): row
        for row in bundle.get("aperture-package-revisions@1", [])
    }
    maps = {row["map_id"]: row for row in bundle.get("aperture-edition-maps@1", [])}
    positions: Dict[Tuple[str, str], set[str]] = {}
    for row in bundle.get("aperture-positions@1", []):
        key = (row["package_id"], row["revision"])
        if key not in revisions:
            raise ApertureExtensionError(f"position references unknown package revision {key!r}")
        positions.setdefault(key, set()).add(row["position_id"])
    for row in bundle.get("aperture-positions@1", []):
        key = (row["package_id"], row["revision"])
        if row["parent_id"] and row["parent_id"] not in positions[key]:
            raise ApertureExtensionError(f"position {row['position_id']!r} references unknown parent")

    facts: Dict[Tuple[str, str], set[str]] = {}
    for row in bundle.get("aperture-facts@1", []):
        key = (row["package_id"], row["revision"])
        if key not in revisions:
            raise ApertureExtensionError(f"fact references unknown package revision {key!r}")
        if row["first_reveal_position_id"] not in positions.get(key, set()):
            raise ApertureExtensionError(f"fact {row['fact_id']!r} references unknown reveal position")
        facts.setdefault(key, set()).add(row["fact_id"])

    for row in bundle.get("aperture-causal-edges@1", []):
        key = (row["package_id"], row["revision"])
        if key not in revisions:
            raise ApertureExtensionError(f"causal edge references unknown package revision {key!r}")
        known = facts.get(key, set())
        causes = canonical_array(row["cause_fact_ids_json"], "cause_fact_ids_json", minimum=1)
        if any(fact_id not in known for fact_id in causes) or row["effect_fact_id"] not in known:
            raise ApertureExtensionError(f"causal edge {row['edge_id']!r} references unknown facts")
        if row["effect_fact_id"] in causes:
            raise ApertureExtensionError(f"causal edge {row['edge_id']!r} is self-causal")

    reveals: set[Tuple[str, str, str, str]] = set()
    for row in bundle.get("aperture-reveals@1", []):
        key = (row["package_id"], row["revision"])
        if row["fact_id"] not in facts.get(key, set()):
            raise ApertureExtensionError(f"reveal {row['reveal_id']!r} references unknown fact")
        if row["position_id"] not in positions.get(key, set()):
            raise ApertureExtensionError(f"reveal {row['reveal_id']!r} references unknown position")
        reveals.add((*key, row["fact_id"], row["position_id"]))
    for row in bundle.get("aperture-facts@1", []):
        identity = (
            row["package_id"], row["revision"], row["fact_id"],
            row["first_reveal_position_id"],
        )
        if identity not in reveals:
            raise ApertureExtensionError(f"fact {row['fact_id']!r} lacks its declared reveal record")

    for row in bundle.get("aperture-sources@1", []):
        key = (row["package_id"], row["revision"])
        if key not in revisions:
            raise ApertureExtensionError(f"source references unknown package revision {key!r}")

    for key, revision in revisions.items():
        map_ids = canonical_array(
            revision["edition_time_map_refs_json"], "edition_time_map_refs_json", minimum=1
        )
        for map_id in map_ids:
            mapped = maps.get(map_id)
            if mapped is None:
                raise ApertureExtensionError(
                    f"package revision {key!r} references unknown TimeMap {map_id!r}"
                )
            if (
                mapped["work_id"] != revision["work_id"]
                or mapped["canonical_edition_id"] != revision["canonical_edition_id"]
            ):
                raise ApertureExtensionError(
                    f"TimeMap {map_id!r} is incompatible with package revision {key!r}"
                )
    return bundle
