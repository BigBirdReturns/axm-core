"""Local-only creator. Production signing identities are deliberately absent."""
from __future__ import annotations
import argparse
import base64
import json
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit
from pack import MAX_ARCHIVE, challenge, compile_pack, decode, inspect_pack, loads

ROOT = Path(__file__).resolve().parent

def make_handler(state: Path, go: str):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def send(self, status, content, mime="application/json"):
            if not isinstance(content, bytes):
                content = json.dumps(content, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(content)

        def allowed(self):
            host = self.headers.get("Host", "")
            hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            origin = self.headers.get("Origin")
            return host in hosts and (not origin or origin in {"http://" + h for h in hosts}) and self.headers.get("Sec-Fetch-Site") != "cross-site"

        def do_GET(self):
            if not self.allowed():
                return self.send(403, {"error": "Local same-origin requests only"})
            path = urlsplit(self.path).path
            if path == "/api/config":
                return self.send(200, {"creator": True, "application": "Python creator + dual verification", "version": "0.1.0"})
            if path == "/api/example":
                return self.send(200, {"archive": base64.b64encode((state / "example.axmpack.zip").read_bytes()).decode(),
                    "trusted_key": base64.b64encode((state / "trusted-demo.pub").read_bytes()).decode(),
                    "filename": "axm-portability.axmpack.zip", "trust_mode": "separately provisioned demonstration public key"})
            assets = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"), "/style.css": ("style.css", "text/css; charset=utf-8")}
            if path in assets:
                name, mime = assets[path]
                return self.send(200, (ROOT / "creator-web" / name).read_bytes(), mime)
            self.send(404, {"error": "Not found"})

        def do_POST(self):
            if not self.allowed():
                return self.send(403, {"error": "Local same-origin requests only"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= MAX_ARCHIVE * 2:
                    raise ValueError("Request size refused")
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise ValueError("JSON request required")
                body = loads(self.rfile.read(size))
                if self.path == "/api/compile":
                    archive, verdict = compile_pack(body.get("title"), body.get("source"), body.get("quotes"),
                        (state / "identity/demo.key").read_bytes(), (state / "trusted-demo.pub").read_bytes(),
                        decode(body["previous_archive"]) if body.get("previous_archive") else None, go)
                    return self.send(200, {"archive": base64.b64encode(archive).decode(),
                        "trusted_key": base64.b64encode((state / "trusted-demo.pub").read_bytes()).decode(),
                        "filename": "my-knowledge.axmpack.zip", "view": verdict})
                archive = decode(body.get("archive", ""))
                key = decode(body.get("trusted_key", ""), 1344)
                if self.path == "/api/open":
                    return self.send(200, inspect_pack(archive, key, go))
                if self.path == "/api/challenge":
                    return self.send(200, challenge(archive, key, body.get("mode", ""), go))
                self.send(404, {"error": "Not found"})
            except Exception as exc:
                self.send(400, {"error": str(exc)[:600]})
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--go-verifier", required=True)
    parser.add_argument("--port", type=int, default=8796)
    args = parser.parse_args()
    server = HTTPServer(("127.0.0.1", args.port), make_handler(args.state, args.go_verifier))
    server.timeout = 20
    print(f"AXM creator: http://127.0.0.1:{server.server_port}", flush=True)
    server.serve_forever()

if __name__ == "__main__":
    main()
