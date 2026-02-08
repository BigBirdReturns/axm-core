from __future__ import annotations

import argparse
import base64
import json
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from graphkdf import GraphKDFParams

try:
    import duckdb  # type: ignore
except ImportError:  # pragma: no cover
    duckdb = None

# Clarion domain for key derivation
CLARION_DOMAIN = b"axm-clarion"


def _b64d(s: str) -> bytes:
    # Be tolerant of missing padding in base64 strings copied from terminals.
    s = (s or "").strip()
    pad = (-len(s)) % 4
    if pad:
        s += "=" * pad
    return base64.b64decode(s.encode("utf-8"))


def cmd_mount(args: argparse.Namespace) -> int:
    shard_path = Path(args.shard_dir).resolve()
    manifest_path = shard_path / "manifest.json"

    if not manifest_path.exists():
        print(f"Error: Not a shard directory (missing manifest.json): {shard_path}")
        return 1

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    # Fallback to folder name if doc_id is missing, ensuring deterministic AAD.
    doc_id = manifest.get("doc_id") or shard_path.name
    clarion = manifest.get("clarion")

    print(f">>> Mounting Shard: {doc_id}")

    # Unencrypted shards.
    if not clarion:
        print("    Type: Unencrypted")
        print("    You can query the files in 'graph/' directly (JSONL or Parquet).")
        return 0

    if not args.secret:
        print("Error: This is a Clarion-encrypted shard. You must provide --secret.")
        return 1

    print("    Type: Clarion Encrypted \U0001f6e1\ufe0f")
    print(f"    Epoch: {clarion['epoch']}")
    if not duckdb:
        print("    [Info] DuckDB not found. Will decrypt to JSONL files only.")

    try:
        user_secret = _b64d(args.secret)
        salt = _b64d(clarion.get("salt_b64") or clarion.get("salt_id_b64", ""))
        topo_hash = _b64d(clarion["topology_hash_b64"])

        # Use GraphKDF for key derivation
        kdf_params = GraphKDFParams(
            user_secret=user_secret,
            salt=salt,
            epoch=clarion["epoch"],
            topology_hash=topo_hash,
            domain=CLARION_DOMAIN,
        )

        aad_dict = {
            "doc_id": doc_id,
            "epoch": clarion["epoch"],
            "topology_hash_b64": clarion["topology_hash_b64"],
        }
        aad = json.dumps(aad_dict, sort_keys=True).encode("utf-8")
    except Exception as e:
        print(f"    [Error] Failed to prepare crypto context: {e}")
        return 1

    with tempfile.TemporaryDirectory(prefix="axm_mount_") as tmpdir:
        tmp_path = Path(tmpdir)
        con = duckdb.connect(":memory:") if duckdb else None

        print("\n[Decrypting Partitions...]")
        for part in clarion["partitions"]:
            color = part["color"]
            rel_path = part["file"]
            enc_file = shard_path / rel_path

            if not enc_file.exists():
                print(f"    [WARN] Missing file: {rel_path}")
                continue

            try:
                blob_data = json.loads(enc_file.read_text(encoding="utf-8"))
                nonce = _b64d(blob_data["nonce_b64"])
                ciphertext = _b64d(blob_data["ciphertext_b64"])

                k_part = kdf_params.derive_partition_key(color)
                aesgcm = AESGCM(k_part)
                plaintext = aesgcm.decrypt(nonce, ciphertext, aad)

                out_file = tmp_path / f"{color.lower()}.jsonl"
                out_file.write_bytes(plaintext)

                line_count = len(plaintext.splitlines())
                print(f"    [OK] {color:<6} -> {out_file.name} ({line_count} rows)")

                if con:
                    view = f"claims_{color.lower()}"
                    safe_path = out_file.as_posix().replace("'", "''")
                    sql = (
                        f"CREATE OR REPLACE VIEW {view} "
                        f"AS SELECT * FROM read_json_auto('{safe_path}')"
                    )
                    con.execute(sql)

            except Exception as e:
                print(f"    [FAIL] {color}: {e}")
                print("           (Check your secret key or AAD integrity)")

        if not con:
            print(f"\n>>> Decrypted files available in {tmp_path}")
            print("    (Press Enter to finish and cleanup...)")
            input()
            return 0

        print("\n>>> System Ready. SQL Shell active. (Ctrl+C to exit)")
        print("    Try: SELECT * FROM claims_green LIMIT 5;")

        while True:
            try:
                q = input("\nSQL> ").strip()
                if not q or q.lower() in {"exit", "quit"}:
                    break

                res = con.execute(q).fetchall()
                cols = [d[0] for d in con.description]
                print(f"| {' | '.join(cols)} |")
                print("-" * (len(cols) * 10))
                for r in res[:20]:
                    print(r)
                if len(res) > 20:
                    print(f"... ({len(res) - 20} more)")

            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"Error: {e}")

    return 0
