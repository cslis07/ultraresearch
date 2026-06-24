"""Vercel serverless handler for ultraresearch.

GET /api/research?q=QUERY&sources=hn,github,lobsters,arxiv,devto
                  &since=7d&limit=10&format=json|md|report

The Python runtime on Vercel installs requirements.txt at build time. curl_cffi
is shipped as a binary wheel and works in the lambda; the bundled insane-search
engine's Playwright fallback does NOT (no chromium on the runtime), and is
disabled by default in this handler.

Honest IP caveat: Vercel egress is data-center IP space. Several block-resistant
sources lose their resistance from there - notably Naver (which actively blocks
Vercel ranges; learned from naver-land-app) and to a lesser extent Reddit,
Bluesky-from-some-regions, and X. The handler runs whatever sources you ask for
and reports failures in `diagnostics`; deciding which sources are safe for your
deployment is a config choice, not the engine's call.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlsplit

# Make the bundled skill package importable. On Vercel the project root is the
# CWD of the function; in local dev we get the same shape from the repo root.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SKILL = os.path.join(_ROOT, "skills", "ultraresearch")
for p in (_SKILL, _ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

# Force UTF-8 stdout/stderr (same patch as the CLI — important on any platform
# whose default is not UTF-8).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass

from research import cache as cache_mod  # type: ignore  # noqa: E402
from research.collectors import collect, COLLECTORS, DEFAULT_SOURCES  # type: ignore  # noqa: E402
from research.normalize import iso, now_utc  # type: ignore  # noqa: E402
from research.__main__ import _digest_md, _report_md, _x_route  # type: ignore  # noqa: E402

# /tmp is the only writable path on Vercel's lambda; cache lives there per
# warm-instance. Cold starts re-do the fan-out, which is fine — that's what TTL
# is for, and the cap is in CACHE_TTL.
DEFAULT_CACHE_DIR = os.environ.get("ULTRARESEARCH_CACHE_DIR", "/tmp/ultraresearch-cache")
DEFAULT_CACHE_TTL = int(os.environ.get("ULTRARESEARCH_CACHE_TTL", "600"))
MAX_LIMIT = int(os.environ.get("ULTRARESEARCH_MAX_LIMIT", "30"))
MAX_SOURCES = int(os.environ.get("ULTRARESEARCH_MAX_SOURCES", "8"))


def _qp(qs: dict, key: str, default: str = "") -> str:
    v = qs.get(key)
    return (v[0] if v else default).strip()


def _parse_sources(raw: str) -> list[str]:
    if not raw:
        return list(DEFAULT_SOURCES)
    if raw.strip().lower() == "all":
        return list(COLLECTORS.keys())
    return [s.strip().lower() for s in raw.split(",") if s.strip()]


def _build_payload(q: str, sources_raw: str, since: str, limit_raw: str) -> dict:
    requested = _parse_sources(sources_raw)
    requested = requested[:MAX_SOURCES]  # cap to avoid runaway fan-out
    scriptable = [s for s in requested if s in COLLECTORS]

    try:
        limit = max(1, min(MAX_LIMIT, int(limit_raw or "10")))
    except ValueError:
        limit = 10
    since = since or "7d"

    cached = cache_mod.load(DEFAULT_CACHE_DIR, q, scriptable, since, limit, DEFAULT_CACHE_TTL)
    if cached is not None:
        cached["_cache"] = "hit"
        return cached

    items, diag, by_source = collect(q, sources=scriptable, since=since, limit=limit)
    payload = {
        "query": q, "since": since, "generated_at": iso(now_utc()),
        "sources_requested": requested, "by_source": by_source,
        "items": [it.to_dict() for it in items],
        "diagnostics": diag, "agent_routes": {},
    }
    if "x" in requested:
        payload["agent_routes"]["x"] = _x_route(q)
    cache_mod.store(DEFAULT_CACHE_DIR, q, scriptable, since, limit, payload)
    payload["_cache"] = "miss"
    return payload


class handler(BaseHTTPRequestHandler):  # noqa: N801  Vercel convention
    def _send(self, status: int, body: str, content_type: str = "application/json"):
        b = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        # CORS: this is a public read-only research endpoint
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        # CDN cache hint — pair the in-function /tmp cache with edge caching
        self.send_header("Cache-Control", "s-maxage=300, stale-while-revalidate=600")
        self.end_headers()
        self.wfile.write(b)

    def do_OPTIONS(self):  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):  # noqa: N802
        try:
            # Vercel's Python entrypoint runtime strips its mount prefix
            # (/api/research), so self.path arrives as "/?q=..." for API calls
            # and bare "/" for landing. Branch on whether a query string is
            # present: query => API, no query => serve the static index.
            split = urlsplit(self.path)
            if not split.query:
                root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                idx = os.path.join(root, "public", "index.html")
                if os.path.isfile(idx):
                    with open(idx, "r", encoding="utf-8") as fh:
                        self._send(200, fh.read(), "text/html")
                    return
            qs = parse_qs(split.query, keep_blank_values=False)
            q = _qp(qs, "q")
            if not q:
                self._send(400, json.dumps({
                    "error": "missing query parameter 'q'",
                    "example": "/api/research?q=AI%20coding%20agent&sources=hn,github&since=7d&format=report",
                    "available_sources": list(COLLECTORS.keys()) + ["x"],
                }, ensure_ascii=False))
                return
            fmt = (_qp(qs, "format", "json") or "json").lower()
            payload = _build_payload(q, _qp(qs, "sources"), _qp(qs, "since"), _qp(qs, "limit"))
            if fmt == "md":
                self._send(200, _digest_md(payload), "text/markdown")
            elif fmt == "report":
                self._send(200, _report_md(payload), "text/markdown")
            else:
                self._send(200, json.dumps(payload, ensure_ascii=False, indent=2))
        except Exception as e:
            self._send(500, json.dumps({
                "error": f"{type(e).__name__}: {e}",
                "trace": traceback.format_exc().splitlines()[-5:],
            }, ensure_ascii=False))
