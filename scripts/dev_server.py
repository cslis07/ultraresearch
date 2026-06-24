#!/usr/bin/env python3
"""Local dev server that emulates Vercel's routing for ultraresearch.

  /api/research?...  -> api/research.py handler (the same one Vercel runs)
  /                  -> public/index.html
  /<anything>        -> public/<anything> if it exists

Run: py scripts/dev_server.py [--port 3009]
"""
from __future__ import annotations

import argparse
import os
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from io import BytesIO

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "skills", "ultraresearch"))

# Reuse the actual Vercel handler so the dev surface matches prod 1:1
from api.research import handler as ApiHandler  # noqa: E402


PUBLIC = os.path.join(ROOT, "public")
_MIME = {".html": "text/html", ".css": "text/css", ".js": "application/javascript",
         ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png",
         ".jpg": "image/jpeg", ".ico": "image/x-icon", ".txt": "text/plain"}


class _ApiBridge(ApiHandler):
    """Run the Vercel-style handler against an existing connection."""
    def __init__(self, parent: BaseHTTPRequestHandler, path: str):
        self.rfile = parent.rfile
        self.wfile = parent.wfile
        self.request_version = parent.request_version
        self.requestline = parent.requestline
        self.client_address = parent.client_address
        self.command = parent.command
        self.path = path
        self.headers = parent.headers


class Dev(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("[dev] " + (fmt % args) + "\n")

    def do_OPTIONS(self):  # noqa: N802
        if self.path.startswith("/api/research"):
            _ApiBridge(self, self.path).do_OPTIONS()
        else:
            self.send_response(204); self.end_headers()

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/research"):
            _ApiBridge(self, self.path).do_GET()
            return
        rel = "index.html" if self.path == "/" else self.path.lstrip("/").split("?", 1)[0]
        fp = os.path.normpath(os.path.join(PUBLIC, rel))
        if not fp.startswith(PUBLIC) or not os.path.isfile(fp):
            self.send_response(404); self.end_headers(); self.wfile.write(b"not found"); return
        ctype = _MIME.get(os.path.splitext(fp)[1].lower(), "application/octet-stream")
        with open(fp, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=3009)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    for s in (sys.stdout, sys.stderr):
        try: s.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except Exception: pass
    print(f"ultraresearch dev server -> http://{args.host}:{args.port}/")
    HTTPServer((args.host, args.port), Dev).serve_forever()


if __name__ == "__main__":
    main()
