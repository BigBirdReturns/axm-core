"""AXM's language-neutral, pre-shard evidence intake floor.

The floor preserves exact source bytes and explicit continuity, coverage, and
security declarations. It never assigns Genesis identities or confers authority.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import datetime, timezone
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, Mapping

OBSERVATION_SPEC = "axm-intake/1.0"
ADAPTER_SPEC = "axm-intake-adapter/1.0"
RECEIPT_SPEC = "axm-intake-receipt/1.0"
STDIO_SPEC = "axm-intake-stdio/1"
AUTHORITY = "observation_only"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
OBS_ID = re.compile(r"^obs1_[0-9a-f]{64}$")
CNT_ID = re.compile(r"^cnt1_[0-9a-f]{64}$")
ADAPTER_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{1,127}$")
KINDS = {"conversation", "agent_trace", "agent_task", "protocol_message", "job", "artifact", "attestation", "web_capture", "document", "research_object", "event", "other"}
RELATIONS = {"revision_of", "branch_of", "fragment_of", "correction_of", "derived_from", "generated_by", "used", "generated", "supports", "contradicts", "implements", "responds_to", "associated_with"}


class IntakeError(ValueError):
    def __init__(self, *errors: str):
        self.errors = tuple(errors)
        super().__init__("; ".join(errors))


def canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise IntakeError(f"not canonical JSON data: {exc}") from exc


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def timestamp(value: Any, field: str = "timestamp") -> str:
    if not isinstance(value, str) or not value:
        raise IntakeError(f"{field} must be RFC 3339")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise IntakeError(f"{field} must be RFC 3339") from exc
    if parsed.tzinfo is None:
        raise IntakeError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def inline_payload(data: bytes | str, media_type: str = "application/octet-stream") -> dict[str, Any]:
    if isinstance(data, str):
        raw = data.encode()
        return {"media_type": media_type, "sha256": sha256(raw), "bytes": len(raw), "encoding": "utf-8", "content": data}
    raw = bytes(data)
    return {"media_type": media_type, "sha256": sha256(raw), "bytes": len(raw), "encoding": "base64", "content_base64": base64.b64encode(raw).decode()}


def external_payload(locator: str, digest: str, byte_count: int, media_type: str = "application/octet-stream") -> dict[str, Any]:
    return {"media_type": media_type, "sha256": digest, "bytes": byte_count, "encoding": "external", "locator": locator}


def payload_bytes(payload: Mapping[str, Any], base_dir: Path | None = None, verify_locator: bool = False) -> bytes | None:
    selectors = [key for key in ("content", "content_base64", "locator") if key in payload]
    if len(selectors) != 1:
        raise IntakeError("payload must contain exactly one of content, content_base64, locator")
    key = selectors[0]
    if key == "content":
        if payload.get("encoding") != "utf-8" or not isinstance(payload[key], str):
            raise IntakeError("payload.content requires encoding=utf-8 and a string value")
        return payload[key].encode()
    if key == "content_base64":
        if payload.get("encoding") != "base64" or not isinstance(payload[key], str):
            raise IntakeError("payload.content_base64 requires encoding=base64 and a string value")
        try:
            return base64.b64decode(payload[key], validate=True)
        except ValueError as exc:
            raise IntakeError("payload.content_base64 is invalid") from exc
    if not isinstance(payload[key], str) or not payload[key]:
        raise IntakeError("payload.locator must be a non-empty string")
    if not verify_locator:
        return None
    locator = payload[key]
    if "://" in locator and not locator.startswith("file://"):
        raise IntakeError("remote locators are never fetched implicitly")
    path = Path(locator[7:] if locator.startswith("file://") else locator)
    if not path.is_absolute():
        path = (base_dir or Path.cwd()) / path
    if not path.is_file():
        raise IntakeError(f"payload.locator not found: {path}")
    return path.read_bytes()


def identity_material(record: Mapping[str, Any]) -> dict[str, Any]:
    payload = record.get("payload") or {}
    descriptor = {key: payload[key] for key in ("media_type", "sha256", "bytes", "encoding", "locator") if key in payload}
    return {key: record.get(key) for key in ("specversion", "type", "content_id", "source", "subject", "observed_at", "authority", "relations", "coverage", "security", "extensions")} | {"payload": descriptor}


def observation_id(record: Mapping[str, Any]) -> str:
    return "obs1_" + sha256(canonical(identity_material(record)))


def finalize(record: Mapping[str, Any], recorded_at: str | None = None) -> dict[str, Any]:
    out = json.loads(json.dumps(record, ensure_ascii=False))
    digest = (out.get("payload") or {}).get("sha256")
    if not isinstance(digest, str) or not HEX64.fullmatch(digest):
        raise IntakeError("payload.sha256 must be lowercase SHA-256")
    out["content_id"] = "cnt1_" + digest
    out["recorded_at"] = timestamp(recorded_at, "recorded_at") if recorded_at else out.get("recorded_at") or now()
    out["id"] = observation_id(out)
    validate(out)
    return out


def _string(value: Any, field: str, errors: list[str]) -> str:
    if not isinstance(value, str) or not value:
        errors.append(f"{field} must be a non-empty string")
        return ""
    return value


def validate(record: Any, base_dir: Path | None = None, verify_locator: bool = False) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(record, Mapping):
        raise IntakeError("observation must be an object")
    required = {"specversion", "type", "id", "content_id", "source", "subject", "observed_at", "recorded_at", "payload", "authority", "relations", "coverage", "security", "extensions"}
    if set(record) != required:
        errors.append(f"observation fields must be exactly {sorted(required)}")
    if record.get("specversion") != OBSERVATION_SPEC or record.get("type") != "observation":
        errors.append(f"record must be {OBSERVATION_SPEC} type=observation")
    if not isinstance(record.get("id"), str) or not OBS_ID.fullmatch(record["id"]): errors.append("id must be obs1_<64 hex>")
    if not isinstance(record.get("content_id"), str) or not CNT_ID.fullmatch(record["content_id"]): errors.append("content_id must be cnt1_<64 hex>")
    if record.get("authority") != AUTHORITY: errors.append("authority must be observation_only")
    for field in ("observed_at", "recorded_at"):
        try: timestamp(record.get(field), field)
        except IntakeError as exc: errors += list(exc.errors)

    source = record.get("source")
    if not isinstance(source, Mapping): errors.append("source must be an object")
    else:
        allowed = {"adapter_id", "adapter_version", "producer", "source_uri", "source_revision", "source_license", "profile_id"}
        if not set(source) <= allowed: errors.append("source has unknown fields")
        for key in ("adapter_id", "adapter_version", "producer", "source_uri"): _string(source.get(key), f"source.{key}", errors)
        if source.get("adapter_id") and not ADAPTER_ID.fullmatch(str(source["adapter_id"])): errors.append("source.adapter_id is invalid")

    subject = record.get("subject")
    if not isinstance(subject, Mapping): errors.append("subject must be an object")
    else:
        if set(subject) != {"kind", "logical_id", "version_id", "parent_version_ids"}: errors.append("subject fields are closed")
        if subject.get("kind") not in KINDS: errors.append("subject.kind is invalid")
        for key in ("logical_id", "version_id"): _string(subject.get(key), f"subject.{key}", errors)
        parents = subject.get("parent_version_ids")
        if not isinstance(parents, list) or any(not isinstance(v, str) or not v for v in parents) or len(parents) != len(set(parents or [])): errors.append("subject.parent_version_ids must be a unique string array")

    payload = record.get("payload")
    verified = False
    if not isinstance(payload, Mapping): errors.append("payload must be an object")
    else:
        allowed = {"media_type", "sha256", "bytes", "encoding", "content", "content_base64", "locator"}
        if not set(payload) <= allowed: errors.append("payload has unknown fields")
        _string(payload.get("media_type"), "payload.media_type", errors)
        digest = payload.get("sha256")
        if not isinstance(digest, str) or not HEX64.fullmatch(digest): errors.append("payload.sha256 must be lowercase SHA-256")
        count = payload.get("bytes")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0: errors.append("payload.bytes must be non-negative integer")
        try: raw = payload_bytes(payload, base_dir, verify_locator)
        except IntakeError as exc: errors += list(exc.errors); raw = None
        if raw is not None:
            if isinstance(count, int) and len(raw) != count: errors.append("payload.bytes mismatch")
            if isinstance(digest, str) and sha256(raw) != digest: errors.append("payload.sha256 mismatch")
            verified = not errors
        if isinstance(digest, str) and record.get("content_id") != "cnt1_" + digest: errors.append("content_id does not match payload.sha256")

    relations = record.get("relations")
    if not isinstance(relations, list): errors.append("relations must be an array")
    else:
        seen: set[tuple[str, str]] = set()
        for item in relations:
            if not isinstance(item, Mapping) or not {"type", "target"} <= set(item) or not set(item) <= {"type", "target", "target_kind", "note"}: errors.append("relation is invalid"); continue
            if item.get("type") not in RELATIONS or not isinstance(item.get("target"), str) or not item["target"]: errors.append("relation type or target is invalid")
            pair = (str(item.get("type")), str(item.get("target")))
            if pair in seen: errors.append("duplicate relation")
            seen.add(pair)

    coverage = record.get("coverage")
    if not isinstance(coverage, Mapping) or not {"scope", "status", "method", "exceptions"} <= set(coverage): errors.append("coverage is invalid")
    else:
        if coverage.get("status") not in {"complete", "partial", "unknown", "not_applicable"}: errors.append("coverage.status is invalid")
        exceptions = coverage.get("exceptions")
        if not isinstance(exceptions, list) or any(not isinstance(v, str) or not v for v in exceptions): errors.append("coverage.exceptions must be a string array")
        if coverage.get("status") == "complete":
            den = coverage.get("denominator")
            if not isinstance(den, Mapping) or set(den) != {"kind", "expected", "observed", "excluded"}: errors.append("complete coverage requires a closed denominator")
            else:
                vals = [den.get(k) for k in ("expected", "observed", "excluded")]
                if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in vals): errors.append("coverage denominator counts are invalid")
                elif vals[1] + vals[2] != vals[0] or vals[2] != len(exceptions or []): errors.append("complete coverage denominator does not reconcile")

    security = record.get("security")
    if not isinstance(security, Mapping) or set(security) != {"sensitivity", "personal_data", "credentials", "redactions"}: errors.append("security is invalid")
    else:
        if security.get("sensitivity") not in {"public", "private", "restricted", "unknown"}: errors.append("security.sensitivity is invalid")
        for key in ("personal_data", "credentials"):
            if security.get(key) not in {"yes", "no", "unknown"}: errors.append(f"security.{key} is invalid")
        if not isinstance(security.get("redactions"), list): errors.append("security.redactions must be an array")
    if not isinstance(record.get("extensions"), Mapping): errors.append("extensions must be an object")
    if not errors and record.get("id") != observation_id(record): errors.append("id does not match canonical observation fingerprint")
    if errors: raise IntakeError(*errors)
    return {"observation": dict(record), "payload_verified": verified}


def conform(record: Any, base_dir: Path | None = None, verify_locator: bool = False) -> dict[str, Any]:
    try: checked = validate(record, base_dir, verify_locator)
    except IntakeError as exc: return {"valid": False, "highest_level": None, "achieved": [], "blockers": {"C0-envelope": list(exc.errors)}}
    achieved = ["C0-envelope"]; blockers: dict[str, list[str]] = {}
    if checked["payload_verified"]: achieved.append("C1-custody")
    else: blockers["C1-custody"] = ["payload bytes were not locally verified"]
    if "C1-custody" in achieved: achieved.append("C2-continuity")
    else: blockers["C2-continuity"] = ["C1-custody required"]
    cov = record["coverage"]
    if "C2-continuity" in achieved and cov["status"] != "unknown": achieved.append("C3-coverage")
    else: blockers["C3-coverage"] = ["explicit non-unknown coverage and C2 required"]
    src = record["source"]; bridge = record["extensions"].get("bridge")
    if "C3-coverage" in achieved and src.get("source_revision") and src.get("source_license") and isinstance(bridge, Mapping) and bridge.get("format"): achieved.append("C4-provenance")
    else: blockers["C4-provenance"] = ["source revision, license, bridge format, and C3 required"]
    sec = record["security"]
    if "C4-provenance" in achieved and all(sec[k] != "unknown" for k in ("sensitivity", "personal_data", "credentials")): achieved.append("C5-estate-ready")
    else: blockers["C5-estate-ready"] = ["explicit security classification and C4 required"]
    return {"valid": True, "highest_level": achieved[-1], "achieved": achieved, "blockers": blockers, "observation_id": record["id"], "content_id": record["content_id"]}


def validate_adapter(manifest: Any) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(manifest, Mapping): raise IntakeError("adapter manifest must be an object")
    required = {"specversion", "id", "name", "version", "license", "source", "transport", "inputs", "outputs", "capabilities", "authority", "telemetry", "security", "limits"}
    if not required <= set(manifest) or not set(manifest) <= required | {"extensions"}: errors.append("adapter manifest fields are invalid")
    if manifest.get("specversion") != ADAPTER_SPEC: errors.append(f"specversion must be {ADAPTER_SPEC}")
    if not isinstance(manifest.get("id"), str) or not ADAPTER_ID.fullmatch(manifest["id"]): errors.append("adapter id is invalid")
    if manifest.get("authority") != AUTHORITY: errors.append("adapter authority must be observation_only")
    if manifest.get("telemetry") != {"default": "off"}: errors.append("telemetry must default off")
    source = manifest.get("source")
    if not isinstance(source, Mapping) or set(source) != {"repository", "revision"} or not all(isinstance(source.get(k), str) and source[k] for k in source): errors.append("source must pin repository and revision")
    transport = manifest.get("transport")
    if not isinstance(transport, Mapping) or transport.get("kind") not in {"stdio-jsonl", "file-drop", "native-messaging", "mcp", "otlp", "http", "library"}: errors.append("transport is invalid")
    elif transport["kind"] == "stdio-jsonl" and not transport.get("command"): errors.append("stdio-jsonl requires a command")
    if not isinstance(manifest.get("outputs"), list) or OBSERVATION_SPEC not in manifest["outputs"]: errors.append(f"outputs must include {OBSERVATION_SPEC}")
    security = manifest.get("security")
    if not isinstance(security, Mapping) or set(security) != {"network_required", "credentials", "sandbox_recommended"} or any(not isinstance(v, bool) for v in security.values()): errors.append("adapter security declaration is invalid")
    limits = manifest.get("limits")
    if not isinstance(limits, Mapping) or set(limits) != {"max_record_bytes"} or not isinstance(limits.get("max_record_bytes"), int) or limits["max_record_bytes"] <= 0: errors.append("adapter size limit is invalid")
    if errors: raise IntakeError(*errors)
    return dict(manifest)


def _base(raw: bytes, fmt: str, producer: str, uri: str, revision: str, license_id: str, kind: str, logical: str, version: str, observed: str, bridge: Mapping[str, Any], parents: list[str] | None = None, relations: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return finalize({
        "specversion": OBSERVATION_SPEC, "type": "observation", "id": "obs1_" + "0" * 64, "content_id": "cnt1_" + "0" * 64,
        "source": {"adapter_id": "org.axm.bridge." + fmt.replace("-", "."), "adapter_version": "1.0.0", "producer": producer or "unknown", "source_uri": uri or "urn:unknown", "source_revision": revision, "source_license": license_id},
        "subject": {"kind": kind, "logical_id": logical, "version_id": version, "parent_version_ids": parents or []},
        "observed_at": observed, "recorded_at": now(), "payload": inline_payload(raw, "application/json"), "authority": AUTHORITY,
        "relations": relations or [],
        "coverage": {"scope": "one translated input record", "status": "not_applicable", "method": "one input record mapped to one observation", "denominator": {"kind": "input_record", "expected": 1, "observed": 1, "excluded": 0}, "exceptions": []},
        "security": {"sensitivity": "unknown", "personal_data": "unknown", "credentials": "unknown", "redactions": []},
        "extensions": {"bridge": dict(bridge)},
    })


def _event_time(value: Any, fallback: str | None) -> str:
    return timestamp(value, "source event time") if isinstance(value, str) and value else timestamp(fallback, "observed_at") if fallback else now()


def translate(record: Mapping[str, Any], fmt: str, raw: bytes | None = None, observed_at: str | None = None) -> dict[str, Any]:
    if not isinstance(record, Mapping): raise IntakeError("source record must be an object")
    fmt = fmt.lower(); raw = bytes(raw) if raw is not None else canonical(record)
    if fmt == "cloudevents":
        for key in ("specversion", "id", "source", "type"):
            if not isinstance(record.get(key), str) or not record[key]: raise IntakeError(f"CloudEvents {key} is required")
        logical = f"{record['source']}#{record.get('subject') or record['id']}"
        return _base(raw, fmt, record["source"], record["source"], record["specversion"], "Apache-2.0", "event", logical, record["id"], _event_time(record.get("time"), observed_at), {"format": fmt, "specversion": record["specversion"], "event_type": record["type"]})
    if fmt == "openlineage":
        run, job = record.get("run"), record.get("job")
        if not isinstance(run, Mapping) or not isinstance(job, Mapping): raise IntakeError("OpenLineage run and job are required")
        run_id = str(run.get("runId") or ""); ns = str(job.get("namespace") or ""); name = str(job.get("name") or "")
        if not all((run_id, ns, name, record.get("eventType"))): raise IntakeError("OpenLineage eventType, runId, namespace, and name are required")
        rel = []
        for key, relation in (("inputs", "used"), ("outputs", "generated")):
            for item in record.get(key) or []:
                if isinstance(item, Mapping): rel.append({"type": relation, "target": f"dataset:{item.get('namespace','')}/{item.get('name','')}", "target_kind": "dataset"})
        return _base(raw, fmt, str(record.get("producer") or ns), str(record.get("producer") or ns), str(record.get("schemaURL") or "1.x"), "Apache-2.0", "job", f"{ns}/{name}", run_id, _event_time(record.get("eventTime"), observed_at), {"format": fmt, "specversion": str(record.get("schemaURL") or "1.x"), "event_type": record["eventType"]}, relations=rel)
    if fmt in {"in-toto", "intoto"}:
        subjects = record.get("subject"); predicate = record.get("predicate") if isinstance(record.get("predicate"), Mapping) else {}
        if not isinstance(subjects, list) or not subjects or not isinstance(subjects[0], Mapping) or not record.get("predicateType"): raise IntakeError("in-toto subject and predicateType are required")
        first = subjects[0]; name = str(first.get("name") or ""); digest = first.get("digest") if isinstance(first.get("digest"), Mapping) else {}
        version = next((f"{k}:{digest[k]}" for k in ("sha256", "sha512", "blake3") if digest.get(k)), "subject:" + sha256(canonical(first)))
        builder = predicate.get("builder") if isinstance(predicate.get("builder"), Mapping) else {}; metadata = predicate.get("metadata") if isinstance(predicate.get("metadata"), Mapping) else {}
        return _base(raw, "in-toto", str(builder.get("id") or "unknown"), str(builder.get("id") or "urn:in-toto"), str(record.get("_type") or record.get("type") or "Statement/v1"), "Apache-2.0", "attestation", name, version, _event_time(metadata.get("buildFinishedOn"), observed_at), {"format": "in-toto", "specversion": str(record.get("_type") or "Statement/v1"), "predicate_type": record["predicateType"]})
    if fmt in {"otel", "openinference", "otel-openinference"}:
        trace = str(record.get("traceId") or record.get("trace_id") or ""); span = str(record.get("spanId") or record.get("span_id") or "")
        if not trace or not span or not record.get("name"): raise IntakeError("OpenTelemetry trace id, span id, and name are required")
        attrs = record.get("attributes") if isinstance(record.get("attributes"), Mapping) else {}; parent = str(record.get("parentSpanId") or record.get("parent_span_id") or "")
        producer = str(attrs.get("service.name") or attrs.get("gen_ai.provider.name") or "unknown")
        relations = [{"type": "generated_by", "target": f"span:{trace}/{parent}", "target_kind": "agent_trace"}] if parent else []
        return _base(raw, "otel-openinference", producer, f"urn:otel:{trace}", str(record.get("schemaUrl") or "1.x"), "Apache-2.0", "agent_trace", trace, span, _event_time(record.get("startTime") or record.get("start_time"), observed_at), {"format": "otel-openinference", "specversion": str(record.get("schemaUrl") or "1.x"), "span_name": record["name"], "openinference_kind": str(attrs.get("openinference.span.kind") or ""), "model": str(attrs.get("gen_ai.request.model") or attrs.get("gen_ai.response.model") or "")}, parents=[parent] if parent else [], relations=relations)
    if fmt == "mcp":
        if record.get("jsonrpc") != "2.0": raise IntakeError("MCP input must be JSON-RPC 2.0")
        params = record.get("params") if isinstance(record.get("params"), Mapping) else {}; meta = params.get("_meta") if isinstance(params.get("_meta"), Mapping) else {}
        session = str(meta.get("sessionId") or params.get("sessionId") or record.get("id") or record.get("method") or sha256(raw))
        return _base(raw, fmt, str(meta.get("client") or meta.get("server") or "mcp"), f"urn:mcp:{session}", "json-rpc-2.0", "MIT", "protocol_message", session, str(record.get("id") or sha256(raw)), _event_time(meta.get("observed_at"), observed_at), {"format": fmt, "specversion": "json-rpc-2.0", "method": str(record.get("method") or "response")})
    if fmt == "a2a":
        task = record.get("task") if isinstance(record.get("task"), Mapping) else record; task_id = str(task.get("id") or task.get("taskId") or ""); context = str(task.get("contextId") or task.get("context_id") or task_id)
        if not task_id: raise IntakeError("A2A task id is required")
        status = task.get("status") if isinstance(task.get("status"), Mapping) else {}
        return _base(raw, fmt, str(task.get("agentId") or "a2a-agent"), f"urn:a2a:{context}", str(record.get("protocolVersion") or "1.x"), "Apache-2.0", "agent_task", context, task_id, _event_time(status.get("timestamp"), observed_at), {"format": fmt, "specversion": str(record.get("protocolVersion") or "1.x"), "state": str(status.get("state") or task.get("state") or "unknown")})
    if fmt in {"ag-ui", "agui"}:
        event = str(record.get("type") or record.get("eventType") or ""); thread = str(record.get("threadId") or record.get("thread_id") or record.get("runId") or "")
        if not event or not thread: raise IntakeError("AG-UI event type and thread/run id are required")
        version = str(record.get("messageId") or record.get("toolCallId") or record.get("runId") or sha256(raw))
        return _base(raw, "ag-ui", str(record.get("agentId") or "ag-ui-agent"), f"urn:ag-ui:{thread}", str(record.get("specversion") or "1.x"), "MIT", "agent_task", thread, version, _event_time(record.get("timestamp"), observed_at), {"format": "ag-ui", "specversion": str(record.get("specversion") or "1.x"), "event_type": event})
    if fmt in {"ro-crate", "rocrate"}:
        graph = record.get("@graph")
        if not isinstance(graph, list) or not graph: raise IntakeError("RO-Crate @graph is required")
        root = next((item for item in graph if isinstance(item, Mapping) and item.get("@id") == "./"), graph[0])
        if not isinstance(root, Mapping): raise IntakeError("RO-Crate root entity is invalid")
        crate_id = str(root.get("@id") or "./")
        return _base(raw, "ro-crate", str(root.get("publisher") or root.get("author") or "ro-crate"), crate_id, str(record.get("@context") or "1.x"), "Apache-2.0", "research_object", crate_id, sha256(raw), _event_time(root.get("datePublished") or root.get("dateModified"), observed_at), {"format": "ro-crate", "specversion": str(record.get("@context") or "1.x"), "entity_count": len(graph)})
    raise IntakeError(f"unsupported format: {fmt}")


FORMATS = ("a2a", "ag-ui", "cloudevents", "in-toto", "mcp", "openlineage", "otel-openinference", "ro-crate")


def builtin_manifests() -> list[dict[str, Any]]:
    licenses = {"mcp": "MIT", "ag-ui": "MIT"}
    return [{"specversion": ADAPTER_SPEC, "id": "org.axm.bridge." + fmt.replace("-", "."), "name": f"AXM {fmt} bridge", "version": "1.0.0", "license": licenses.get(fmt, "Apache-2.0"), "source": {"repository": "https://github.com/BigBirdReturns/axm-core", "revision": "built-in"}, "transport": {"kind": "library"}, "inputs": [fmt], "outputs": [OBSERVATION_SPEC], "capabilities": ["translate", "preserve-exact-input-bytes"], "authority": AUTHORITY, "telemetry": {"default": "off"}, "security": {"network_required": False, "credentials": False, "sandbox_recommended": False}, "limits": {"max_record_bytes": 67108864}, "extensions": {"built_in": True}} for fmt in FORMATS]


def discover_adapters() -> list[dict[str, Any]]:
    found = builtin_manifests()
    for ep in entry_points(group="axm.intake_adapters"):
        try:
            obj = ep.load(); manifest = obj.manifest() if hasattr(obj, "manifest") else obj() if callable(obj) else obj
            found.append(validate_adapter(manifest))
        except Exception as exc:
            found.append({"error": f"{ep.name}: {exc}"})
    return found


def handle_stdio(request: Any) -> dict[str, Any]:
    request_id = str(request.get("request_id", "")) if isinstance(request, Mapping) else ""
    try:
        if not isinstance(request, Mapping) or request.get("protocol") != STDIO_SPEC: raise IntakeError(f"protocol must be {STDIO_SPEC}")
        action = request.get("action")
        if action == "health": result: Any = {"status": "ok"}
        elif action == "capabilities": result = {"formats": list(FORMATS), "output": OBSERVATION_SPEC}
        elif action == "translate": result = translate(request.get("record"), str(request.get("format") or ""))
        else: raise IntakeError("action must be health, capabilities, or translate")
        return {"protocol": STDIO_SPEC, "request_id": request_id, "status": "ok", "result": result, "errors": []}
    except IntakeError as exc:
        return {"protocol": STDIO_SPEC, "request_id": request_id, "status": "error", "result": None, "errors": list(exc.errors)}


def receipt(record: Mapping[str, Any], status: str, store: Mapping[str, Any] | None = None) -> dict[str, Any]:
    validate(record)
    if status not in {"accepted", "unchanged", "extended", "revised", "diverged", "truncated", "rejected"}: raise IntakeError("invalid receipt status")
    return {"specversion": RECEIPT_SPEC, "observation_id": record["id"], "content_id": record["content_id"], "status": status, "recorded_at": now(), "store": dict(store or {}), "warnings": [], "authority": AUTHORITY}

# Stable, descriptive aliases used by the public spoke API.
validate_observation = validate
validate_adapter_manifest = validate_adapter
translate_record = translate
conformance_report = conform
compute_observation_id = observation_id
build_inline_payload = inline_payload
build_external_payload = external_payload
supported_formats = lambda: FORMATS
