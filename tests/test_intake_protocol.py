from __future__ import annotations
import json
from pathlib import Path
import pytest
from click.testing import CliRunner
from axm_core.intake import AUTHORITY, FORMATS, IntakeError, conform, finalize, handle_stdio, inline_payload, observation_id, translate, validate, validate_adapter
from axm_core.intake.cli import intake_group

T = "2026-07-28T12:00:00Z"


def draft():
    payload = inline_payload("hello", "text/plain")
    return {
        "specversion": "axm-intake/1.0", "type": "observation", "id": "obs1_" + "0"*64, "content_id": "cnt1_" + payload["sha256"],
        "source": {"adapter_id": "org.example.adapter", "adapter_version": "1", "producer": "example", "source_uri": "urn:example", "source_revision": "abc", "source_license": "Apache-2.0"},
        "subject": {"kind": "event", "logical_id": "thing", "version_id": "v1", "parent_version_ids": []},
        "observed_at": T, "recorded_at": T, "payload": payload, "authority": AUTHORITY, "relations": [],
        "coverage": {"scope": "one", "status": "complete", "method": "single", "denominator": {"kind": "record", "expected": 1, "observed": 1, "excluded": 0}, "exceptions": []},
        "security": {"sensitivity": "private", "personal_data": "no", "credentials": "no", "redactions": []},
        "extensions": {"bridge": {"format": "example"}},
    }


def observation(): return finalize(draft(), T)


def test_identity_replay_and_exact_payload():
    a = observation(); b = dict(a); b["recorded_at"] = "2026-07-28T12:05:00Z"
    assert observation_id(a) == observation_id(b)
    assert validate(a)["payload_verified"]
    assert conform(a)["highest_level"] == "C5-estate-ready"


def test_digest_authority_and_coverage_fail_closed():
    bad = observation(); bad["payload"]["content"] = "tampered"
    with pytest.raises(IntakeError, match="sha256"): validate(bad)
    bad = observation(); bad["authority"] = "approved"; bad["id"] = observation_id(bad)
    with pytest.raises(IntakeError, match="observation_only"): validate(bad)
    bad = observation(); bad["coverage"]["denominator"]["expected"] = 2; bad["id"] = observation_id(bad)
    with pytest.raises(IntakeError, match="does not reconcile"): validate(bad)


def samples():
    return {
        "cloudevents": {"specversion":"1.0","id":"e1","source":"urn:test","type":"example","time":T},
        "openlineage": {"eventType":"COMPLETE","eventTime":T,"producer":"urn:p","run":{"runId":"r1"},"job":{"namespace":"n","name":"j"},"inputs":[],"outputs":[]},
        "in-toto": {"_type":"https://in-toto.io/Statement/v1","subject":[{"name":"a.bin","digest":{"sha256":"a"*64}}],"predicateType":"https://slsa.dev/provenance/v1","predicate":{"builder":{"id":"urn:b"},"metadata":{"buildFinishedOn":T}}},
        "otel-openinference": {"traceId":"t1","spanId":"s1","name":"LLM","startTime":T,"attributes":{"openinference.span.kind":"LLM"}},
        "mcp": {"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"_meta":{"sessionId":"session"}}},
        "a2a": {"protocolVersion":"1","task":{"id":"task","contextId":"ctx","status":{"state":"completed","timestamp":T}}},
        "ag-ui": {"type":"RUN_FINISHED","threadId":"thread","runId":"run","timestamp":T},
        "ro-crate": {"@context":"https://w3id.org/ro/crate/1.3/context","@graph":[{"@id":"./","name":"crate","datePublished":T}]},
    }


@pytest.mark.parametrize("fmt", FORMATS)
def test_bridges_preserve_bytes_without_authority(fmt):
    record = samples()[fmt]; raw = json.dumps(record, indent=2).encode(); result = translate(record, fmt, raw)
    checked = validate(result)
    assert checked["payload_verified"]
    assert result["payload"]["bytes"] == len(raw)
    assert result["authority"] == AUTHORITY
    assert conform(result)["highest_level"] == "C4-provenance"


def manifest():
    return {"specversion":"axm-intake-adapter/1.0","id":"org.example.adapter","name":"Example","version":"1","license":"Apache-2.0","source":{"repository":"https://example.invalid/r","revision":"abc"},"transport":{"kind":"stdio-jsonl","command":["adapter"]},"inputs":["example"],"outputs":["axm-intake/1.0"],"capabilities":["translate"],"authority":AUTHORITY,"telemetry":{"default":"off"},"security":{"network_required":False,"credentials":False,"sandbox_recommended":True},"limits":{"max_record_bytes":1024},"extensions":{}}


def test_adapter_contract_and_stdio():
    validate_adapter(manifest())
    bad = manifest(); bad["telemetry"] = {"default":"on"}
    with pytest.raises(IntakeError, match="default off"): validate_adapter(bad)
    response = handle_stdio({"protocol":"axm-intake-stdio/1","request_id":"r","action":"translate","format":"cloudevents","record":samples()["cloudevents"]})
    assert response["status"] == "ok" and response["result"]["authority"] == AUTHORITY


def test_cli_roundtrip(tmp_path: Path):
    source = tmp_path/"event.json"; out = tmp_path/"observation.json"; source.write_text(json.dumps(samples()["cloudevents"]))
    runner = CliRunner(); result = runner.invoke(intake_group,["translate","cloudevents",str(source),"--output",str(out)])
    assert result.exit_code == 0, result.output
    assert runner.invoke(intake_group,["validate",str(out)]).exit_code == 0
    report = runner.invoke(intake_group,["conform",str(out),"--json-out"])
    assert report.exit_code == 0 and json.loads(report.output)["highest_level"] == "C4-provenance"


def test_vectors_and_schemas_are_json():
    root = Path(__file__).resolve().parents[1]
    for path in list((root/"schemas/intake").glob("*.json")) + list((root/"conformance/intake-v1").rglob("*.json")):
        json.loads(path.read_text())
    validate(json.loads((root/"conformance/intake-v1/good/observation.json").read_text()))
    validate_adapter(json.loads((root/"conformance/intake-v1/good/adapter.json").read_text()))
    for path in (root/"conformance/intake-v1/bad").glob("*.json"):
        with pytest.raises(IntakeError): validate(json.loads(path.read_text()))
