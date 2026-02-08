from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
from typing import List

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from graphkdf import Edge, GraphKDFParams, compute_topology_hash
from axm_forge.models.claims import Claim, ClaimArg

# Clarion domain for key derivation
CLARION_DOMAIN = b"axm-clarion"


def _b64d(s: str) -> bytes:
    # Be tolerant of missing padding in base64 strings copied from terminals.
    s = (s or "").strip()
    pad = (-len(s)) % 4
    if pad:
        s += "=" * pad
    return base64.b64decode(s.encode("utf-8"))


def _dict_to_claim(d: dict) -> Claim:
    """
    Helper to reconstruct Claim object from JSON dict for topology hashing.
    STRICT MODE: Fails if required topology fields are missing.
    """
    required = {"claim_id", "predicate", "args"}
    missing = required - d.keys()
    if missing:
        raise ValueError(f"Malformed claim row. Missing fields: {missing}")

    args_in = d["args"]
    if not isinstance(args_in, list):
        raise ValueError("Malformed claim row: 'args' must be a list")

    try:
        args = tuple(ClaimArg(role=a["role"], entity_id=a["entity_id"]) for a in args_in)
    except KeyError as e:
        raise ValueError(f"Malformed ClaimArg in args list: missing {e}")

    return Claim(
        claim_id=d["claim_id"],
        predicate=d["predicate"],
        args=args,
        value=d.get("value"),
        polarity=d.get("polarity", "affirmed"),
        conditions=tuple(d.get("conditions", [])),
        source_spans=tuple(),
        provenance=d.get("provenance", {}),
    )


def _claims_to_edges(claims: List[Claim]) -> List[Edge]:
    """Convert Claim objects to GraphKDF Edge objects."""
    edges = []
    for c in claims:
        subj = None
        obj = None
        for a in c.args:
            if a.role == "subject":
                subj = a.entity_id
            elif a.role == "object":
                obj = a.entity_id
        if subj is not None and obj is not None:
            edges.append(Edge(subject=subj, predicate=c.predicate, object=obj))
    return edges


def cmd_verify(args: argparse.Namespace) -> int:
    shard_path = Path(args.shard_dir).resolve()
    manifest_path = shard_path / "manifest.json"

    if not manifest_path.exists():
        print(f"FAIL: No manifest found at {manifest_path}")
        print("      (Did you point to the .axm directory?)")
        return 1

    try:
        manifest = json.loads(manifest_path.read_text("utf-8"))
        doc_id = manifest.get("doc_id") or shard_path.name

        print(f"Verifying Shard: {doc_id}")

        clarion = manifest.get("clarion")
        if not clarion:
            print("  [OK] Unencrypted Shard (Structure Valid)")
            return 0

        # Support both v1 (salt_id_b64) and v2 (salt_b64) formats
        salt_key = "salt_b64" if "salt_b64" in clarion else "salt_id_b64"
        required_keys = ["epoch", salt_key, "topology_hash_b64", "partitions"]
        missing_keys = [k for k in required_keys if k not in clarion]
        if missing_keys:
            print(f"  [FAIL] Manifest missing required Clarion fields: {missing_keys}")
            return 1

        partitions = clarion.get("partitions", [])
        if not partitions:
            print("  [WARN] Clarion block present but partitions list is empty")

        for p in partitions:
            if "color" not in p or "file" not in p:
                print(f"  [FAIL] Malformed partition entry: {p}")
                return 1

            p_file = shard_path / p["file"]
            if not p_file.exists():
                print(f"  [FAIL] Missing Partition File: {p['file']}")
                return 1

        print("  [OK] Structure & Files Present")

        if not args.secret:
            print("\n  [INFO] Skipping Topology Verification (No --secret provided).")
            print("         Run with --secret <KEY> to verify graph integrity.")
            return 0

        print("\n  [VERIFY] Deep Topology Check...")
        try:
            user_secret = _b64d(args.secret)
            salt = _b64d(clarion[salt_key])
            claimed_hash_b64 = clarion["topology_hash_b64"]
            topo_hash_expected = _b64d(claimed_hash_b64)
            epoch = clarion["epoch"]

            # Use GraphKDF for key derivation
            kdf_params = GraphKDFParams(
                user_secret=user_secret,
                salt=salt,
                epoch=epoch,
                topology_hash=topo_hash_expected,
                domain=CLARION_DOMAIN,
            )

            aad = json.dumps(
                {
                    "doc_id": doc_id,
                    "epoch": epoch,
                    "topology_hash_b64": claimed_hash_b64,
                },
                sort_keys=True,
            ).encode("utf-8")

            all_claims: list[Claim] = []

            for part in partitions:
                color = part["color"]
                enc_path = shard_path / part["file"]

                print(f"    ... Decrypting {color}")
                k_part = kdf_params.derive_partition_key(color)

                blob_data = json.loads(enc_path.read_text("utf-8"))
                nonce = _b64d(blob_data["nonce_b64"])
                ciphertext = _b64d(blob_data["ciphertext_b64"])

                aesgcm = AESGCM(k_part)
                plaintext = aesgcm.decrypt(nonce, ciphertext, aad)

                line_no = 0
                for line in plaintext.decode("utf-8").splitlines():
                    line_no += 1
                    if not line.strip():
                        continue
                    try:
                        all_claims.append(_dict_to_claim(json.loads(line)))
                    except Exception as e:
                        print(f"    [FAIL] Malformed JSON on line {line_no} of {color}: {e}")
                        return 1

            print("    ... Recomputing Graph Topology")
            edges = _claims_to_edges(all_claims)
            topo_hash_computed = compute_topology_hash(edges)

            if topo_hash_computed == topo_hash_expected:
                print("  [PASS] Topology Hash Verified Matches Manifest.")
                print(f"         Proof: {base64.b64encode(topo_hash_computed).decode('ascii')[:12]}...")
                return 0

            print("  [FAIL] TOPOLOGY MISMATCH!")
            print(f"         Expected: {claimed_hash_b64}")
            print(f"         Computed: {base64.b64encode(topo_hash_computed).decode('ascii')}")
            return 1

        except Exception as e:
            print(f"  [FAIL] Crypto Error during verify: {e}")
            return 1

    except Exception as e:
        print(f"FAIL: {e}")
        return 1
