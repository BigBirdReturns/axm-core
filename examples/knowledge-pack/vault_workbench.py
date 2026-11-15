"""Local browser application and CLI for incremental reviewed AXM collections."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import secrets
import threading
from urllib.parse import parse_qs, urlsplit
import uuid

from workbench_store import (
    Workbench,
    VERSION,
    Cancelled,
    checkpoint,
    now,
    process_lock,
    json_bytes,
)
import obsidian_vault as vault
import pack

WEB = Path(__file__).resolve().parent / "workbench-web"


class Application:
    def __init__(self, workbench: Workbench):
        self.workbench = workbench
        self.token = secrets.token_urlsafe(32)
        self.executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="axm-review"
        )
        self.jobs = {}
        self.lock = threading.Lock()
        self.watchers = {}
        self.pending_scans = set()
        self.closing = False
        with workbench.db() as con:
            con.execute(
                "UPDATE jobs SET state='interrupted',error='Previous service stopped; inspect the retained review before resuming' WHERE state IN ('queued','running')"
            )

    def submit(self, operation: str, function) -> dict:
        with self.lock:
            terminal = [k for k, v in self.jobs.items() if v["future"].done()]
            for identity in terminal[:-64]:
                self.jobs.pop(identity)
            active = sum(not task["future"].done() for task in self.jobs.values())
            if active >= 16:
                raise ValueError("The bounded work queue is full")
            identity = "j_" + uuid.uuid4().hex
            event = threading.Event()
            with self.workbench.db() as con:
                con.execute(
                    "INSERT INTO jobs VALUES(?,?,?,?,?,?)",
                    (identity, "queued", operation, "", "", now()),
                )

            def run():
                try:
                    checkpoint(event)
                    with self.workbench.db() as con:
                        con.execute(
                            "UPDATE jobs SET state='running' WHERE id=?", (identity,)
                        )
                    result = function(event)
                    # Full plans stay at their durable address, not in every status poll.
                    result = {
                        k: v
                        for k, v in result.items()
                        if k
                        in {
                            "status",
                            "id",
                            "head",
                            "generation",
                            "compiled_notes",
                            "compiled_catalogs",
                            "reused_notes",
                            "notes",
                            "claims",
                            "approval_digest",
                            "new_edition",
                            "digest",
                            "changed_notes",
                            "unchanged_notes",
                            "model_calls",
                            "cache_hits",
                            "source_files_written",
                            "open_in_obsidian",
                            "preserved_view_edits",
                            "download",
                            "bytes",
                            "archive_sha256",
                            "objects",
                            "editions",
                        }
                    }
                    with self.workbench.db() as con:
                        con.execute(
                            "UPDATE jobs SET state='completed',detail=? WHERE id=?",
                            (json.dumps(result), identity),
                        )
                except Exception as exc:
                    with self.workbench.db() as con:
                        state = "cancelled" if isinstance(exc, Cancelled) else "failed"
                        con.execute(
                            "UPDATE jobs SET state=?,error=? WHERE id=?",
                            (state, str(exc)[:2000], identity),
                        )

                finally:
                    with self.lock:
                        eligible = [
                            name
                            for name in sorted(self.pending_scans)
                            if name in self.watchers
                            and not self.watchers[name]["stop"].is_set()
                        ]
                        next_scan = (
                            eligible[0] if eligible and not self.closing else None
                        )
                        if next_scan:
                            self.pending_scans.discard(next_scan)
                    if next_scan:
                        try:
                            self.submit(
                                "watch-scan:" + next_scan,
                                lambda event, name=next_scan: self.workbench.plan(
                                    name, extract=False, cancel=event
                                ),
                            )
                        except (ValueError, RuntimeError) as exc:
                            self.workbench.event(
                                "watch_scan_not_submitted",
                                {"vault_id": next_scan, "error": str(exc)[:300]},
                            )

            future = self.executor.submit(run)
            self.jobs[identity] = {"event": event, "future": future}
            return {"job_id": identity}

    def cancel(self, identity):
        with self.lock:
            job = self.jobs.get(identity)
            if not job:
                raise ValueError("Unknown live job")
            if job["future"].done():
                return {"status": "already_terminal"}
            job["event"].set()
            if job["future"].cancel():
                with self.workbench.db() as con:
                    con.execute(
                        "UPDATE jobs SET state='cancelled' WHERE id=?", (identity,)
                    )
            return {"status": "cancellation_requested"}

    def watch(self, identifier: str, enabled: bool):
        if type(enabled) is not bool:
            raise ValueError("Watch must be enabled explicitly")
        spec = self.workbench.spec(identifier)
        previous = self.watchers.pop(identifier, None)
        self.pending_scans.discard(identifier)
        if previous:
            previous["stop"].set()
            previous["thread"].join(timeout=3)
        if not enabled:
            return {"status": "watch_disabled"}
        from watchfiles import watch

        stop = threading.Event()

        def changed():
            def include(_, raw):
                relative = Path(raw).relative_to(Path(spec["path"]))
                return (
                    not any(
                        p.startswith(".") or p in {"_AXM", "_AXM-Workbench"}
                        for p in relative.parts
                    )
                    and relative.suffix.lower() == ".md"
                )

            try:
                for changes in watch(
                    spec["path"],
                    watch_filter=include,
                    stop_event=stop,
                    debounce=1000,
                    rust_timeout=1000,
                    raise_interrupt=False,
                ):
                    if not changes:
                        continue
                    with self.lock:
                        busy = any(not j["future"].done() for j in self.jobs.values())
                        if busy:
                            self.pending_scans.add(identifier)
                    if busy:
                        # Preserve a visible invalidation rather than overlap writes.
                        self.workbench.event(
                            "working_copy_changed",
                            {
                                "vault_id": identifier,
                                "paths": len(changes),
                                "scan_required": True,
                            },
                        )
                        continue
                    self.submit(
                        "watch-scan:" + identifier,
                        lambda event: self.workbench.plan(
                            identifier, extract=False, cancel=event
                        ),
                    )
            except Exception as exc:
                self.workbench.event(
                    "watch_failed", {"vault_id": identifier, "error": str(exc)[:500]}
                )

        thread = threading.Thread(
            target=changed, name="axm-watch-" + identifier, daemon=True
        )
        self.watchers[identifier] = {"stop": stop, "thread": thread}
        thread.start()
        return {
            "status": "watch_enabled",
            "mode": "source-only draft proposals; no models and no automatic signing",
        }

    def configure(self, operation, body):
        with self.lock:
            if self.watchers or any(not v["future"].done() for v in self.jobs.values()):
                raise ValueError(
                    "Finish active jobs and disable watchers before changing configuration"
                )
            if operation == "register":
                return self.workbench.register(
                    body["id"],
                    body["path"],
                    body["public_key"],
                    body.get("private_key") or None,
                    label=body.get("label") or body["id"],
                    publisher_id=body["publisher_id"],
                    publisher_name=body["publisher_name"],
                )
            if operation == "provider":
                config = body["provider"]
                if config.get("type") not in {"none", "openai-compatible", "ollama"}:
                    raise ValueError(
                        "Command providers are configured through the local CLI only"
                    )
                allowed = {
                    "type",
                    "base_url",
                    "model",
                    "api_key_env",
                    "allow_remote",
                    "max_input_chars",
                    "timeout_seconds",
                }
                if set(config) - allowed:
                    raise ValueError("Unsupported provider setting")
                return self.workbench.configure_provider(config)
            if operation == "identity":
                if body.get("confirm") != "create-local-signing-identity":
                    raise ValueError(
                        "Explicit confirmation is required to create signing keys"
                    )
                from axm_build.sign import hybrid1_keygen

                name = body["name"]
                if not vault.ID.fullmatch(name):
                    raise ValueError("Identity name must be a portable identifier")
                public, private = hybrid1_keygen()
                parent = self.workbench.state / "keys"
                parent.mkdir(exist_ok=True)
                destination = parent / name
                vault._publish(
                    {"publisher.pub": public, "publisher.key": private}, destination
                )
                (destination / "publisher.key").chmod(0o600)
                return {
                    "status": "IDENTITY_CREATED",
                    "public_key": str(destination / "publisher.pub"),
                    "private_key": str(destination / "publisher.key"),
                    "scope": "Local signing identity under your custody; distribution of the public key is a separate trust decision.",
                }
            raise ValueError("Unknown configuration operation")

    def state(self):
        return {
            **self.workbench.state_json(),
            "csrf_token": self.token,
            "watching": [k for k, v in self.watchers.items() if v["thread"].is_alive()],
        }

    def close(self):
        self.closing = True
        self.pending_scans.clear()
        for value in self.watchers.values():
            value["stop"].set()
        for value in self.jobs.values():
            value["event"].set()
        self.executor.shutdown(wait=True, cancel_futures=False)
        self.workbench.close()


def make_server(app: Application, port=8357):
    class Handler(BaseHTTPRequestHandler):
        server_version = "AXMVaultWorkbench/" + VERSION

        def log_message(self, format, *args):
            # Do not place source queries or credentials in process logs.
            pass

        def boundary(self, write=False):
            port = self.server.server_port
            hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
            if self.headers.get("Host") not in hosts:
                raise PermissionError("Untrusted Host header")
            origin = self.headers.get("Origin")
            if origin and origin not in {"http://" + host for host in hosts}:
                raise PermissionError("Cross-origin requests are refused")
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                raise PermissionError("Cross-site requests are refused")
            if write and not hmac.compare_digest(
                self.headers.get("X-AXM-Token", ""), app.token
            ):
                raise PermissionError("Missing or stale local-session token")

        def respond(self, status, body, content_type="application/json; charset=utf-8"):
            if not isinstance(body, bytes):
                body = json_bytes(body)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            )
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            try:
                self.boundary()
                path = urlsplit(self.path)
                query = {k: values[-1] for k, values in parse_qs(path.query).items()}
                if path.path == "/api/download":
                    import re

                    identity = query.get("id", "")
                    if not re.fullmatch(r"[0-9a-f]{32}\.axmcollection\.zip", identity):
                        raise ValueError("Invalid export identity")
                    from workbench_collection import MAX_ARCHIVE

                    data = vault._read(
                        app.workbench.state / "exports" / identity, MAX_ARCHIVE
                    )
                    return self.respond(200, data, "application/zip")
                if path.path in {"/", "/app.js", "/style.css"}:
                    name = "index.html" if path.path == "/" else path.path[1:]
                    content_type = mimetypes.guess_type(name)[0] or "text/plain"
                    return self.respond(
                        200, (WEB / name).read_bytes(), content_type + "; charset=utf-8"
                    )
                if path.path == "/api/state":
                    result = app.state()
                elif path.path == "/api/plan":
                    result = app.workbench.read_plan(query.get("id", ""))
                elif path.path == "/api/source":
                    result = app.workbench.source_review(
                        query.get("plan", ""), query.get("path", "")
                    )
                elif path.path == "/api/search":
                    result = app.workbench.search(
                        query.get("q", ""),
                        query.get("scope", "current"),
                        query.get("vault", ""),
                    )
                elif path.path == "/api/history":
                    result = app.workbench.history(query.get("vault", ""))
                elif path.path == "/api/graph":
                    result = app.workbench.graph(query.get("vault", ""))
                elif path.path == "/api/conflicts":
                    result = app.workbench.conflicts(query.get("vault", ""))
                else:
                    return self.respond(404, {"error": "Unknown route"})
                self.respond(200, result)
            except PermissionError as exc:
                self.respond(403, {"error": str(exc)})
            except Exception as exc:
                self.respond(400, {"error": str(exc)[:2000]})

        def do_POST(self):
            try:
                self.boundary(write=True)
                if (
                    self.headers.get("Content-Type", "").split(";")[0]
                    != "application/json"
                ):
                    raise ValueError("Use application/json")
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 4 * 1024 * 1024:
                    raise ValueError("Invalid request size")
                body = pack.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("Expected an object")
                path = urlsplit(self.path).path
                if path == "/api/scan":
                    identifier = body["vault_id"]
                    app.workbench.spec(identifier)
                    extract, force = (
                        body.get("extract", False),
                        body.get("force_extract", False),
                    )
                    result = app.submit(
                        "scan:" + identifier,
                        lambda event: app.workbench.plan(
                            identifier,
                            extract=extract,
                            force_extract=force,
                            cancel=event,
                        ),
                    )
                elif path == "/api/approve":
                    identifier, digest, accepted = (
                        body["plan_id"],
                        body["digest"],
                        body["accepted"],
                    )
                    app.workbench.read_plan(identifier)
                    result = app.submit(
                        "seal:" + identifier,
                        lambda event: app.workbench.approve(
                            identifier, digest, accepted, cancel=event
                        ),
                    )
                elif path == "/api/reject":
                    result = app.workbench.reject(body["plan_id"], body["digest"])
                elif path == "/api/refresh":
                    identifier = body["vault_id"]
                    app.workbench.spec(identifier)
                    result = app.submit(
                        "refresh:" + identifier,
                        lambda event: app.workbench.refresh(identifier, cancel=event),
                    )
                elif path == "/api/export":
                    identifier = body["vault_id"]
                    app.workbench.spec(identifier)

                    def export(event):
                        from workbench_collection import export_collection

                        directory = app.workbench.state / "exports"
                        directory.mkdir(exist_ok=True)
                        name = uuid.uuid4().hex + ".axmcollection.zip"
                        value = export_collection(
                            app.workbench, identifier, directory / name, event
                        )
                        return {**value, "download": "/api/download?id=" + name}

                    result = app.submit("export:" + identifier, export)
                elif path == "/api/context":
                    result = app.workbench.context(
                        body.get("q", ""),
                        body.get("scope", "current"),
                        body.get("vault", ""),
                        body.get("budget", 2000),
                    )
                elif path == "/api/cancel":
                    result = app.cancel(body["job_id"])
                elif path in {"/api/register", "/api/provider", "/api/identity"}:
                    result = app.configure(path.rsplit("/", 1)[-1], body)
                elif path == "/api/watch":
                    result = app.watch(body["vault_id"], body["enabled"])
                else:
                    return self.respond(404, {"error": "Unknown route"})
                self.respond(200, result)
            except PermissionError as exc:
                self.respond(403, {"error": str(exc)})
            except Exception as exc:
                self.respond(400, {"error": str(exc)[:2000]})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


def demo(workbench: Workbench):
    if workbench.config["vaults"]:
        raise ValueError("Demo initialization requires fresh state")
    from axm_build.sign import hybrid1_keygen

    root = workbench.state.parent / (workbench.state.name + "-demo-vaults")
    root.mkdir()
    public, private = hybrid1_keygen()
    keys = workbench.state / "keys"
    keys.mkdir()
    (keys / "demo.pub").write_bytes(public)
    (keys / "demo.key").write_bytes(private)
    (keys / "demo.key").chmod(0o600)
    fixtures = {
        "research": {
            "Protocol.md": "---\ntitle: Research protocol\nowner: Research\n---\n# Research protocol\n\nOrion retains source evidence for 90 days.\nA release requires two independent approvals.\n\nSee [[Evidence]].\n",
            "Evidence.md": "# Evidence\n\nOriginal source bytes remain immutable after acceptance.\nReview proposals before creating a signed successor.\n",
        },
        "operations": {
            "Runbook.md": "---\ntitle: Operations runbook\nowner: Operations\n---\n# Operations runbook\n\nOrion retains source evidence for 30 days.\nThe release window opens on Friday.\n"
        },
    }
    for identifier, notes in fixtures.items():
        source = root / identifier
        source.mkdir()
        for path, text in notes.items():
            (source / path).write_text(text, encoding="utf-8")
        workbench.register(
            identifier,
            str(source),
            str(keys / "demo.pub"),
            str(keys / "demo.key"),
            label=identifier.title(),
            publisher_id="@workbench-demo",
            publisher_name="Explicit local demonstration identity",
        )
    return {
        "status": "DEMO_INITIALIZED",
        "vaults": list(fixtures),
        "state": str(workbench.state),
        "meaning": "Fresh demonstration identity and synthetic sources; no production authenticity asserted.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--port", type=int, default=8357)
    commands.add_parser("demo")
    commands.add_parser("status")
    register = commands.add_parser("register")
    register.add_argument("--id", required=True)
    register.add_argument("--vault", required=True)
    register.add_argument("--trusted-key", required=True)
    register.add_argument("--private-key")
    register.add_argument("--publisher-id", required=True)
    register.add_argument("--publisher-name", required=True)
    provider = commands.add_parser("provider")
    provider.add_argument("--config", type=Path, required=True)
    scan = commands.add_parser("scan")
    scan.add_argument("--vault", required=True)
    scan.add_argument("--extract", action="store_true")
    scan.add_argument("--force-extract", action="store_true")
    approve = commands.add_parser("approve")
    approve.add_argument("--plan", required=True)
    approve.add_argument("--digest", required=True)
    approve.add_argument(
        "--accepted",
        type=Path,
        required=True,
        help="JSON array of explicitly selected proposal ids",
    )
    export = commands.add_parser(
        "export", help="Export a closed signed collection without private keys"
    )
    export.add_argument("--vault", required=True)
    export.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser(
        "verify-collection",
        help="Verify portable collection with a separately supplied public key",
    )
    verify.add_argument("--archive", type=Path, required=True)
    verify.add_argument("--trusted-key", type=Path, required=True)
    args = parser.parse_args(argv)
    workbench = Workbench(args.state)
    try:
        if args.command == "serve":
            with process_lock(workbench.state / "locks/server.lock"):
                app = Application(workbench)
                server = make_server(app, args.port)
                print(
                    json.dumps(
                        {
                            "status": "LISTENING",
                            "url": f"http://127.0.0.1:{server.server_port}/",
                            "version": VERSION,
                        }
                    ),
                    flush=True,
                )
                try:
                    server.serve_forever()
                finally:
                    server.server_close()
                    app.close()
            return 0
        if args.command == "demo":
            result = demo(workbench)
        elif args.command == "register":
            result = workbench.register(
                args.id,
                args.vault,
                args.trusted_key,
                args.private_key,
                publisher_id=args.publisher_id,
                publisher_name=args.publisher_name,
            )
        elif args.command == "provider":
            result = workbench.configure_provider(
                pack.loads(vault._read(args.config, 65536))
            )
        elif args.command == "export":
            from workbench_collection import export_collection

            result = export_collection(workbench, args.vault, args.output)
        elif args.command == "verify-collection":
            from workbench_collection import verify_collection

            result = verify_collection(args.archive, args.trusted_key)
        elif args.command == "scan":
            result = workbench.plan(
                args.vault, extract=args.extract, force_extract=args.force_extract
            )
        elif args.command == "approve":
            result = workbench.approve(
                args.plan,
                args.digest,
                pack.loads(vault._read(args.accepted, 4 * 1024 * 1024)),
            )
        else:
            result = workbench.state_json()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False))
        return 2
    finally:
        workbench.close()


if __name__ == "__main__":
    raise SystemExit(main())
