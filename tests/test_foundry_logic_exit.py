"""Logic Exit v0 — action/query definitions + function source, contract tier."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from foundry_exit import logic_exit as L

requires_kernel = pytest.mark.skipif(
    not L.SC.kernel_available(), reason="axm-genesis kernel not on PATH"
)
FIXTURE = Path(__file__).resolve().parent.parent / "samples" / "logic_exit_synthetic"


def test_loader_parses_actions_queries_functions():
    cap = L.load_capture(FIXTURE)
    assert [a["apiName"] for a in cap["actions"]] == ["createFlightDelay", "markFlightArrived"]
    assert [q["apiName"] for q in cap["queries"]] == ["avgDelayByRoute"]
    assert cap["functions"] == ["avgDelayByRoute.ts"]
    # function source is carried in content, keyed under a flattened name
    assert any(k.startswith("functions__") for k in cap["content"])


def test_missing_apiname_is_a_clear_error(tmp_path):
    d = tmp_path / "cap"
    d.mkdir()
    (d / "actionTypes.json").write_text(json.dumps({"data": [{"parameters": {}}]}))
    with pytest.raises(L.LogicCaptureError, match="apiName"):
        L.load_capture(d)


def test_claims_include_contract_and_honest_attestation():
    cap = L.load_capture(FIXTURE)
    candidates, source, counts, tallies = L._build_claims(cap)
    preds = {c["predicate"] for c in candidates if c.get("type") == "claim"}
    assert {"has_param", "has_type", "returns", "source_sealed", "carries", "not_carried"} <= preds
    # the engine/runtime is attested as NOT carried, on the shard
    notc = [c for c in candidates if c.get("predicate") == "not_carried"]
    assert notc and "engine" in notc[0]["object_label"].lower()
    # every claim binds to its byte span
    sb = source.encode("utf-8")
    for c in candidates:
        if c.get("type") == "claim":
            ev = c["evidence"]
            assert sb[ev["byte_start"]:ev["byte_end"]].decode("utf-8") == ev["text"]


@requires_kernel
def test_seals_and_verifies_detached(tmp_path):
    packet = L.run(FIXTURE, tmp_path / "out")
    assert packet["verification"]["status"] == "PASS"
    assert packet["shard_id"].startswith("sh1_")
    assert packet["counts"]["actions"] == 2
    assert packet["counts"]["functions"] == 1


@requires_kernel
def test_function_source_is_byte_identical(tmp_path):
    import tempfile
    from foundry_exit import _seal_common as SC
    cap = L.load_capture(FIXTURE)
    cands, src, counts, _ = L._build_claims(cap)
    work = Path(tempfile.mkdtemp())
    sealed = SC.seal(cands, src, cap["content"], work / "shard",
                     namespace=L.NAMESPACE, title="t")
    content = Path(sealed.shard_dir) / "content"
    assert (content / "functions__avgDelayByRoute.ts").read_bytes() == \
        (FIXTURE / "functions" / "avgDelayByRoute.ts").read_bytes()
