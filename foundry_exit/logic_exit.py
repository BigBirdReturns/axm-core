"""Logic Exit v0 — Actions + Functions/Queries: contract carried, engine not.

Two planks of WORKFLOW_EXIT_MAP.md Layer 3, sealed to the honest tier the
published wire shapes allow:

  * ACTION definitions — apiName + typed parameters, from the published Actions
    API v2 (List/Get Action Types) — as genesis claims.
  * QUERY definitions — apiName + typed parameters + output type, from the
    published Query API (Get Query Type) — as genesis claims.
  * FUNCTION source — the authored TypeScript/Python (git-cloneable from Code
    Repositories) — sealed VERBATIM as content.

What it carries: the CONTRACT (what each action/query takes and returns) and the
SOURCE. What it does NOT carry, and attests so on the shard: the Actions ENGINE
(submission-criteria/rules→edits/writeback) and the Functions RUNTIME — those
have no portable export and are rebuilt on the customer's own write path.

No Palantir code, no credentials, no network. Custody is the genesis ``sh1_``.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from . import _seal_common as SC
from .exit_test import verify_detached

NAMESPACE = "foundry/logic-exit"
ACTION_TYPES_FILE = "actionTypes.json"
QUERY_TYPES_FILE = "queryTypes.json"
FUNCTIONS_DIR = "functions"

DEFAULT_CAPTURE = Path(__file__).resolve().parent.parent / "samples" / "logic_exit_synthetic"


class LogicCaptureError(ValueError):
    """A capture file is malformed; the message names the file and the bad key."""


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_bytes().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise LogicCaptureError(f"{path.name}: not valid UTF-8 JSON: {e}") from e


def _data_list(doc: Any, where: str) -> List[Mapping[str, Any]]:
    if isinstance(doc, Mapping) and "data" in doc:
        doc = doc["data"]
    if not isinstance(doc, list):
        raise LogicCaptureError(f"{where}: expected a JSON array (or {{'data': [...]}})")
    return doc


def _params(d: Mapping[str, Any]) -> List[Tuple[str, str]]:
    """[(paramName, typeString)] from a {parameters:{name:{dataType:{type}}}} map."""
    out: List[Tuple[str, str]] = []
    pmap = d.get("parameters") or {}
    if isinstance(pmap, Mapping):
        for name, spec in pmap.items():
            t = "unknown"
            if isinstance(spec, Mapping):
                dt = spec.get("dataType")
                if isinstance(dt, Mapping) and dt.get("type") is not None:
                    t = str(dt["type"])
            out.append((str(name), t))
    return out


def load_capture(capture_dir: str | Path):
    capture_dir = Path(capture_dir)
    actions: List[Mapping[str, Any]] = []
    queries: List[Mapping[str, Any]] = []
    content: Dict[str, bytes] = {}

    ap = capture_dir / ACTION_TYPES_FILE
    if ap.exists():
        content[ACTION_TYPES_FILE] = ap.read_bytes()
        for a in _data_list(_load(ap), ACTION_TYPES_FILE):
            if "apiName" not in a:
                raise LogicCaptureError(f"{ACTION_TYPES_FILE}: an action type is missing 'apiName'")
            actions.append(a)

    qp = capture_dir / QUERY_TYPES_FILE
    if qp.exists():
        content[QUERY_TYPES_FILE] = qp.read_bytes()
        for q in _data_list(_load(qp), QUERY_TYPES_FILE):
            if "apiName" not in q:
                raise LogicCaptureError(f"{QUERY_TYPES_FILE}: a query type is missing 'apiName'")
            queries.append(q)

    fdir = capture_dir / FUNCTIONS_DIR
    functions: List[str] = []
    if fdir.is_dir():
        for f in sorted(fdir.iterdir()):
            if f.is_file():
                content[f"{FUNCTIONS_DIR}__{f.name}"] = f.read_bytes()
                functions.append(f.name)

    if not (actions or queries or functions):
        raise LogicCaptureError("capture is empty: need actionTypes.json, queryTypes.json, and/or functions/")
    return {"actions": actions, "queries": queries, "functions": functions, "content": content}


def _build_claims(cap) -> Tuple[List[dict], str, Dict[str, int], Dict[str, int]]:
    c = SC.Claims(NAMESPACE)
    logic = c.entity("logic-exit", "logic_exit")
    # Honest attestation, sealed INTO the shard: contract + source, not the engine.
    c.claim(logic, "carries", "action + query definitions, function source", "literal:string", 1)
    c.claim(logic, "not_carried", "Actions engine (submission-criteria/rules/writeback); Functions runtime", "literal:string", 1)

    for a in cap["actions"]:
        name = str(a["apiName"])
        al = c.entity(f"action/{name}", "action")
        c.claim(al, "kind", "action", "literal:string", 1)
        if a.get("status") is not None:
            c.claim(al, "status", str(a["status"]), "literal:string", 1)
        for pname, ptype in _params(a):
            pl = c.entity(f"param/{name}.{pname}", "param")
            c.claim(al, "has_param", pl, "entity", 1)
            c.claim(pl, "has_type", ptype, "literal:string", 1)

    for q in cap["queries"]:
        name = str(q["apiName"])
        ql = c.entity(f"query/{name}", "query")
        c.claim(ql, "kind", "query", "literal:string", 1)
        out = q.get("output")
        if isinstance(out, Mapping) and isinstance(out.get("dataType"), Mapping) and out["dataType"].get("type") is not None:
            c.claim(ql, "returns", str(out["dataType"]["type"]), "literal:string", 1)
        for pname, ptype in _params(q):
            pl = c.entity(f"param/{name}.{pname}", "param")
            c.claim(ql, "has_param", pl, "entity", 1)
            c.claim(pl, "has_type", ptype, "literal:string", 1)

    for fname in cap["functions"]:
        fl = c.entity(f"function/{fname}", "function")
        # source is sealed verbatim in content/; the claim binds the artifact name
        c.claim(fl, "source_sealed", fname, "literal:string", 1)

    candidates, source, counts = c.build()
    tallies = {"actions": len(cap["actions"]), "queries": len(cap["queries"]), "functions": len(cap["functions"])}
    return candidates, source, counts, tallies


def run(capture_dir: Path, out_dir: Path) -> dict:
    if not SC.kernel_available():
        raise SystemExit("axm-genesis kernel not on PATH (need axm-build / axm-verify).")
    cap = load_capture(capture_dir)
    candidates, source, counts, tallies = _build_claims(cap)
    work = Path(tempfile.mkdtemp(prefix="logic_exit_v0_"))
    sealed = SC.seal(candidates, source, cap["content"], work / "shard",
                     namespace=NAMESPACE, title="Foundry logic exit shard")
    verify = verify_detached(sealed.shard_dir, sealed.trusted_key_path)
    packet = {
        "artifact": "AXM Foundry Logic Exit v0 (actions + functions)",
        "claim": ("Action + query DEFINITIONS as genesis claims and function SOURCE sealed "
                  "verbatim. The contract and source travel; the Actions engine and Functions "
                  "runtime do not, and the shard attests so."),
        "shard_id": sealed.shard_id,
        "counts": {**tallies, "entities": sealed.entity_count, "claims": sealed.claim_count},
        "verification": {"status": verify.get("status"), "exit_code": verify.get("exit_code")},
        "evidence_tier": ("Reconciled against Palantir's PUBLISHED Actions API v2 (List/Get Action "
                          "Types) and Query API (Get Query Type). Synthetic sample, not a live tenant. "
                          "Carries CONTRACT + SOURCE (tier 1); the execution engine/runtime is NOT "
                          "carried and is attested as such on the shard."),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "logic_exit_packet.json").write_text(json.dumps(packet, indent=2) + "\n", encoding="utf-8")
    return packet


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run AXM Foundry Logic Exit v0.")
    ap.add_argument("capture_dir", nargs="?", default=str(DEFAULT_CAPTURE))
    ap.add_argument("--out", default="logic_exit_out")
    args = ap.parse_args(argv)
    p = run(Path(args.capture_dir), Path(args.out))
    ok = p["verification"]["status"] == "PASS"
    print(f"[logic exit v0: {'OK' if ok else 'FAIL'} — shard={p['shard_id']}, verify={p['verification']['status']}, "
          f"actions={p['counts']['actions']}, queries={p['counts']['queries']}, functions={p['counts']['functions']}]")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
