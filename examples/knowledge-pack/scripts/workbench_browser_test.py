"""Exercise the actual local workbench with a labeled deterministic provider fixture."""

from __future__ import annotations
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from workbench_store import Workbench  # noqa: E402
from vault_workbench import Application, make_server, demo  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "build/workbench-browser")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    chrome = (
        os.environ.get("CHROME_PATH")
        or shutil.which("google-chrome")
        or shutil.which("chromium")
    )
    node = shutil.which("node")
    if not chrome or not node:
        raise SystemExit("Chrome and Node are required for real browser qualification")
    calls = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(body)
            text = body["messages"][0]["content"]
            rows = []
            for days in ("30", "90", "180"):
                quote = f"Orion retains source evidence for {days} days."
                if quote in text:
                    rows.append(
                        {
                            "subject": "Orion",
                            "predicate": "retains_source_evidence_days",
                            "object": days,
                            "quote": quote,
                        }
                    )
            dangerous = '<img src=x onerror="window.axmInjected=1">'
            if dangerous in text:
                rows.append(
                    {
                        "subject": "Literal source string",
                        "predicate": "contains",
                        "object": dangerous,
                        "quote": dangerous,
                    }
                )
            rows.append(
                {
                    "subject": "Fabricated",
                    "predicate": "is",
                    "object": "true",
                    "quote": "Invented text never in the source.",
                }
            )
            value = json.dumps(
                {"choices": [{"message": {"content": json.dumps({"claims": rows})}}]}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(value)))
            self.end_headers()
            self.wfile.write(value)

    provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    threading.Thread(target=provider.serve_forever, daemon=True).start()
    with tempfile.TemporaryDirectory(prefix="axm-workbench-browser-") as temp:
        root = Path(temp)
        workbench = Workbench(root / "state")
        demo(workbench)
        spec = workbench.spec("research")
        source = Path(spec["path"])
        with (source / "Evidence.md").open("a") as handle:
            handle.write(
                '\nLiteral source: <img src=x onerror="window.axmInjected=1">\n'
            )
        workbench.configure_provider(
            {
                "type": "openai-compatible",
                "model": "deterministic-browser-fixture",
                "base_url": f"http://127.0.0.1:{provider.server_port}/v1",
            }
        )
        application = Application(workbench)
        server = make_server(application, 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        origin = f"http://127.0.0.1:{server.server_port}/"
        profile = root / "chrome-profile"
        log = (args.output / "chrome.log").open("w")
        command = [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--disable-background-networking",
            "--disable-component-update",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-sync",
            "--metrics-recording-only",
            "--renderer-process-limit=1",
            "--num-raster-threads=1",
            "--disable-extensions",
            "--disable-features=SpareRendererForSitePerProcess",
            "--remote-debugging-port=0",
            "--user-data-dir=" + str(profile),
            "about:blank",
        ]
        if os.environ.get("CI") == "true":
            command.insert(1, "--no-sandbox")
        if hasattr(os, "sched_getaffinity") and shutil.which("taskset"):
            # Constrain this test browser, not the host or other workloads.
            cpus = sorted(os.sched_getaffinity(0))[:2]
            command = [
                shutil.which("taskset"),
                "-c",
                ",".join(map(str, cpus)),
                *command,
            ]
        child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                portfile = profile / "DevToolsActivePort"
                if portfile.exists():
                    break
                if child.poll() is not None:
                    raise RuntimeError("Chrome exited before readiness")
                time.sleep(0.1)
            else:
                raise RuntimeError("Chrome readiness timeout")
            port = portfile.read_text().splitlines()[0]
            subprocess.run(
                [
                    node,
                    str(ROOT / "tests/workbench-browser.mjs"),
                    port,
                    origin,
                    str(args.output.resolve()),
                    str(source),
                ],
                cwd=ROOT,
                check=True,
                timeout=240,
            )
            summary = {
                "status": "PASS",
                "provider": "deterministic test fixture, not a live model",
                "provider_requests": len(calls),
                "origin": origin,
            }
            (args.output / "provider-receipt.json").write_text(
                json.dumps(summary, indent=2)
            )
            print(json.dumps(summary))
        finally:
            child.terminate()
            try:
                child.wait(10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
            log.close()
            server.shutdown()
            server.server_close()
            application.close()
    provider.shutdown()
    provider.server_close()


if __name__ == "__main__":
    main()
