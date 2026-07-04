"""Seal the Foundry exit bundle through genesis; verify with an out-of-band key.

Uses the real, already-proven ``axm-build`` / genesis compiler surface. It
reproduces the landed ``SealedShard`` / ``VerifyStatus`` custody seam SEMANTICS
(out-of-band key required; PASS / FAIL / MALFORMED / NO_TRUSTED_KEY) WITHOUT
importing ``ghostbox`` -- the axm-core importer must not depend on the attention
layer. Custody and verification remain genesis's; this module only drives it.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import List, Optional, Tuple

from .bundle import BUNDLE_FILES, build_bundle
from .planes import FoundryExitManifest

AXM_BUILD = "axm-build"
AXM_VERIFY = "axm-verify"


class VerifyStatus(str, Enum):
    """Mirror of the landed custody-seam taxonomy (genesis frozen exit codes)."""

    PASS = "pass"
    FAIL = "fail"
    MALFORMED = "malformed"
    NO_TRUSTED_KEY = "no_trusted_key"


@dataclass(frozen=True)
class SealedExitShard:
    shard_id: str            # genesis-derived sh1_, the ONLY custody identity
    shard_dir: str
    trusted_key_path: str    # out-of-band publisher pub (sibling to the shard)
    suite: str
    merkle_root: Optional[str]
    sealed_at: Optional[str]


def kernel_available() -> bool:
    return shutil.which(AXM_BUILD) is not None and shutil.which(AXM_VERIFY) is not None


def seal_exit_bundle(
    manifest: FoundryExitManifest,
    bundle_dir: str | Path,
    out_shard_dir: str | Path,
    *,
    namespace: str = "foundry/exit",
    title: str = "Foundry exit bundle",
    created_at: str = "2026-07-04T00:00:00Z",
) -> SealedExitShard:
    """Build the sealed shard from an already-assembled bundle directory."""
    bundle_dir = Path(bundle_dir)
    out_shard_dir = Path(out_shard_dir)
    work = out_shard_dir.parent
    content_dir = work / "_content"
    key_dir = work / "keys"
    if content_dir.exists():
        shutil.rmtree(content_dir)
    content_dir.mkdir(parents=True, exist_ok=True)
    key_dir.mkdir(parents=True, exist_ok=True)

    # content/ = the bundle artifacts + a canonical source.txt the claims cite.
    for name in BUNDLE_FILES:
        src = bundle_dir / name
        if src.exists():
            shutil.copy2(src, content_dir / name)
    candidates, source_text = _candidates_and_source(manifest, namespace)
    (content_dir / "source.txt").write_text(source_text, encoding="utf-8")
    candidates_path = work / "candidates.jsonl"
    candidates_path.write_text("\n".join(json.dumps(c) for c in candidates) + "\n", encoding="utf-8")

    # Out-of-band keypair (kept in a key pool sibling to the shard, never inside it).
    key_path = key_dir / "publisher.key"
    pub_path = key_dir / "publisher.pub"
    if not (key_path.exists() and pub_path.exists()):
        subprocess.run([AXM_BUILD, "keygen", str(key_dir), "--name", "publisher"], check=True, capture_output=True, text=True)

    subprocess.run(
        [
            AXM_BUILD, "compile", str(candidates_path), str(content_dir), str(out_shard_dir),
            "--private-key", str(key_path),
            "--namespace", namespace, "--title", title, "--created-at", created_at,
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    manifest_bytes = (out_shard_dir / "manifest.json").read_bytes()
    m = json.loads(manifest_bytes)
    shard_id = _derive_shard_id(manifest_bytes)
    return SealedExitShard(
        shard_id=shard_id,
        shard_dir=str(out_shard_dir),
        trusted_key_path=str(pub_path),
        suite=m.get("suite", "axm-hybrid1"),
        merkle_root=(m.get("integrity") or {}).get("merkle_root"),
        sealed_at=(m.get("metadata") or {}).get("created_at"),
    )


def verify_exit_bundle(shard_dir: str | Path, trusted_key: Optional[str | Path]) -> VerifyStatus:
    """Verify through genesis with an out-of-band key.

    No key -> NO_TRUSTED_KEY, decided before invoking the CLI (never falls back
    to the shard's own embedded key). Otherwise map genesis's frozen exit code.
    """
    if not trusted_key:
        return VerifyStatus.NO_TRUSTED_KEY
    code = subprocess.run(
        [AXM_VERIFY, "shard", str(shard_dir), "--trusted-key", str(trusted_key)],
        capture_output=True,
        text=True,
    ).returncode
    if code == 0:
        return VerifyStatus.PASS
    if code == 2:
        return VerifyStatus.MALFORMED
    return VerifyStatus.FAIL


def _derive_shard_id(manifest_bytes: bytes) -> str:
    """Genesis's own sh1_ derivation. Custody identity is genesis's, not ours."""
    from axm_verify.crypto import derive_shard_id  # genesis

    return derive_shard_id(manifest_bytes)


def _candidates_and_source(manifest: FoundryExitManifest, namespace: str) -> Tuple[List[dict], str]:
    """Turn the Foundry structure into genesis candidates + a source.txt the
    claims cite. Datasets and object types become entities; lineage and ontology
    backing become claims. Byte offsets are computed so evidence matches exactly.
    """
    entities: dict = {}

    def ent(label: str, etype: str) -> None:
        if label not in entities:
            entities[label] = {"type": "entity", "namespace": namespace, "label": label, "entity_type": etype}

    statements: List[Tuple[str, dict]] = []
    for d in manifest.datasets:
        ent(d.dataset_rid, "dataset")
    for o in manifest.object_types:
        ent(o.object_type_id, "object_type")
        for ds in o.backing_dataset_rids:
            ent(ds, "dataset")
            statements.append(
                (f"{o.object_type_id} backed_by {ds}",
                 {"subject_label": o.object_type_id, "predicate": "backed_by", "object_label": ds})
            )
    for e in manifest.lineage:
        ent(e.upstream_dataset_rid, "dataset")
        ent(e.downstream_dataset_rid, "dataset")
        statements.append(
            (f"{e.downstream_dataset_rid} derives_from {e.upstream_dataset_rid}",
             {"subject_label": e.downstream_dataset_rid, "predicate": "derives_from", "object_label": e.upstream_dataset_rid})
        )

    if not statements:
        # A shard needs at least one claim; describe the export itself.
        label = manifest.source_system or "foundry-export"
        ent(label, "export")
        statements.append((f"{label} is an axm-sealed foundry exit bundle",
                           {"subject_label": label, "predicate": "is", "object_label": "foundry_exit_bundle"}))
        ent("foundry_exit_bundle", "concept")

    claims: List[dict] = []
    source = ""
    for stmt, base in statements:
        start = len(source.encode("utf-8"))
        source += stmt
        end = len(source.encode("utf-8"))
        source += "\n"
        claims.append(
            {
                "type": "claim",
                "subject_label": base["subject_label"],
                "predicate": base["predicate"],
                "object_label": base["object_label"],
                "object_type": "entity",
                "tier": 1,
                "evidence": {"source_file": "source.txt", "byte_start": start, "byte_end": end, "text": stmt},
            }
        )
    return list(entities.values()) + claims, source


def seal_from_manifest(
    manifest: FoundryExitManifest, work_dir: str | Path, **kw
) -> Tuple[Path, SealedExitShard]:
    """Convenience: assemble the bundle then seal it. Returns (bundle_dir, sealed)."""
    work = Path(work_dir)
    bundle_dir = build_bundle(manifest, work / "bundle")
    sealed = seal_exit_bundle(manifest, bundle_dir, work / "shard", **kw)
    return bundle_dir, sealed
