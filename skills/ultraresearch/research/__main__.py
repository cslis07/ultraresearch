#!/usr/bin/env python3
"""CLI entrypoint for the ultraresearch multi-source collector.

Usage:
    python3 -m research "QUERY" [--sources hn,reddit,devto,github,arxiv,x]
                                [--since 7d] [--limit 15] [--format json|md]

Examples:
    python3 -m research "AI coding agents" --sources hn,reddit,github --since 7d
    python3 -m research "화장품 브랜드" --sources x,reddit --format md
    python3 -m research "rust web framework" --sources all --limit 20 --format json

Exit codes:
    0  at least one source returned items
    1  every requested source failed or returned nothing
    2  CLI arg error
"""
from __future__ import annotations

import argparse
import json
import sys

from .collectors import collect, COLLECTORS, DEFAULT_SOURCES
from .normalize import iso, now_utc

# Windows consoles default to a legacy codepage (e.g. cp949) that cannot encode
# em-dashes or Korean in non-ASCII output. Force UTF-8 so json/md print cleanly.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass


# X has no public keyword-search API, so the script cannot collect it directly.
# It hands the agent a deterministic discovery recipe instead.
def _x_route(query: str) -> dict:
    return {
        "reason": "X/Twitter has no public keyword-search endpoint — discovery must go through the agent.",
        "do": [
            f'WebSearch: {query} (site:x.com OR site:twitter.com)',
            f'WebSearch: {query} when:7d  (recent chatter)',
            "For each tweet URL found, fetch the real post via the bundled engine:",
            '    python3 -m engine "<tweet_url>"   # Phase-0 hits tweet-result/oEmbed, no auth',
            "If you only have a handle, read their timeline:",
            '    python3 -m engine "https://x.com/<handle>"   # Phase-0 syndication timeline',
        ],
        "note": "Treat a brand/topic as 'hot on X' only after corroborating across multiple distinct posts.",
    }


def _parse_sources(raw: str) -> list[str]:
    if raw.strip().lower() == "all":
        return list(COLLECTORS.keys())
    return [s.strip().lower() for s in raw.split(",") if s.strip()]


def _digest_md(payload: dict) -> str:
    out = [f"# ultraresearch — {payload['query']}",
           f"_since {payload['since'] or 'any'} · {payload['generated_at']} · "
           f"{len(payload['items'])} items_\n"]
    items = payload["items"]
    for src in payload["sources_requested"]:
        group = [it for it in items if it["source"] == src]
        if not group:
            continue
        out.append(f"## {src}  ({len(group)})")
        for it in group[:10]:
            sig = []
            if it.get("score") is not None:
                sig.append(f"▲{it['score']}")
            if it.get("comments") is not None:
                sig.append(f"💬{it['comments']}")
            if it.get("age_hours") is not None:
                sig.append(f"{it['age_hours']}h")
            meta = " · ".join(sig)
            out.append(f"- [{it['title']}]({it['url']})" + (f"  _{meta}_" if meta else ""))
        out.append("")
    routes = payload.get("agent_routes", {})
    if "x" in routes:
        out.append("## x  (agent-driven)")
        out.append(f"> {routes['x']['reason']}")
        for step in routes["x"]["do"]:
            out.append(f"- {step}")
        out.append("")
    failed = [d for d in payload["diagnostics"] if not d["ok"]]
    if failed:
        out.append("## ⚠ diagnostics")
        for d in failed:
            out.append(f"- **{d['source']}**: {d['note']}")
    return "\n".join(out)


# --- deterministic synthesis (--format report) -------------------------------
# Detect items shared across sources. Three signature flavours, broadest first:
#   1) host+first-path (catches the same GitHub repo seen via HN + GitHub + X)
#   2) normalized URL (already deduped by collectors.dedupe, but cross-source
#      duplicates that survive get caught here)
#   3) coarse title slug (fallback for non-URL repeats)
from urllib.parse import urlsplit as _us
import re as _re


def _repo_signature(url: str) -> str:
    """github.com/owner/repo (any path) -> 'github.com/owner/repo'. Else host+path[:2]."""
    if not url:
        return ""
    try:
        p = _us(url)
    except ValueError:
        return ""
    host = (p.hostname or "").lower().lstrip("www.")
    parts = [s for s in (p.path or "").split("/") if s]
    return f"{host}/" + "/".join(parts[:2]).lower() if parts else host


def _title_slug(title: str) -> str:
    return _re.sub(r"\s+", " ", _re.sub(r"[^\w\s가-힣]+", " ", (title or "").lower())).strip()[:60]


def _cluster_cross_source(items: list[dict]) -> list[dict]:
    """Group items by repo/title signature; return clusters with ≥2 distinct sources."""
    buckets: dict[str, list[dict]] = {}
    for it in items:
        sig = _repo_signature(it.get("url") or "") or f"title:{_title_slug(it.get('title') or '')}"
        if not sig:
            continue
        buckets.setdefault(sig, []).append(it)
    clusters = []
    for sig, group in buckets.items():
        sources = {it["source"] for it in group}
        if len(sources) >= 2:
            best = max(group, key=lambda it: (it.get("score") or 0, it.get("age_hours") or 0) and -(it.get("age_hours") or 0))
            clusters.append({"sig": sig, "sources": sorted(sources), "items": group, "lead": best})
    clusters.sort(key=lambda c: (-len(c["sources"]), -(c["lead"].get("score") or 0)))
    return clusters


def _signal_str(it: dict) -> str:
    sig = []
    if it.get("score") is not None: sig.append(f"▲{it['score']}")
    if it.get("comments") is not None: sig.append(f"💬{it['comments']}")
    if it.get("age_hours") is not None: sig.append(f"{it['age_hours']}h")
    return " · ".join(sig)


def _report_md(payload: dict) -> str:
    items = payload["items"]
    n = len(items)
    diag = payload["diagnostics"]
    ok_srcs = [d["source"] for d in diag if d["ok"]]
    bad_srcs = [d for d in diag if not d["ok"]]
    clusters = _cluster_cross_source(items)

    out = [f"# ultraresearch · cited report — {payload['query']}",
           f"_since {payload['since'] or 'any'} · {payload['generated_at']} · "
           f"{n} items from {len(ok_srcs)} sources · {len(clusters)} cross-source clusters_\n"]

    # TL;DR — three deterministic lines: scale, strongest signal, single-source warning
    out.append("## TL;DR")
    if clusters:
        top = clusters[0]
        out.append(f"- **Strongest signal**: `{top['sig']}` surfaced in {len(top['sources'])} "
                   f"sources ({', '.join(top['sources'])}) — [{top['lead']['title'][:80]}]({top['lead']['url']})")
    else:
        out.append("- **No cross-source clusters found** — every item appears in a single source. "
                   "Treat all findings as tentative and widen `--since` or add sources.")
    out.append(f"- **Sources reached**: {', '.join(ok_srcs) or 'none'}"
               + (f"  ·  blocked: {', '.join(d['source'] for d in bad_srcs)}" if bad_srcs else ""))
    out.append(f"- **Cross-verified ratio**: {sum(len(c['items']) for c in clusters)}/{n} items "
               f"(R2 — single-source items are unverified)\n")

    # Cross-verified themes table
    if clusters:
        out.append("## Cross-verified (R2: ≥2 independent sources)")
        out.append("| # | Item | Sources | Signal | Link |")
        out.append("|---|------|---------|--------|------|")
        for i, c in enumerate(clusters[:10], 1):
            led = c["lead"]
            sig = _signal_str(led) or "—"
            out.append(f"| {i} | {led['title'][:60]} | {', '.join(c['sources'])} | {sig} "
                       f"| [link]({led['url']}) |")
        out.append("")

    # Per-source highlights (top 5 by hotness)
    out.append("## Source highlights")
    for src in payload["sources_requested"]:
        group = [it for it in items if it["source"] == src]
        if not group:
            continue
        out.append(f"### {src}  ({len(group)})")
        for it in group[:5]:
            sig = _signal_str(it)
            out.append(f"- [{it['title']}]({it['url']})" + (f"  _{sig}_" if sig else ""))
        out.append("")

    # Unverified (single-source) — R2 warning surface
    clustered_urls = {it.get("url") for c in clusters for it in c["items"]}
    singles = [it for it in items if it.get("url") not in clustered_urls]
    if singles:
        out.append("## ⚠ Unverified / single-source (R2 — handle as tentative)")
        for it in singles[:8]:
            sig = _signal_str(it)
            out.append(f"- _{it['source']}_ · [{it['title']}]({it['url']})"
                       + (f"  _{sig}_" if sig else ""))
        if len(singles) > 8:
            out.append(f"- _…and {len(singles) - 8} more single-source items_")
        out.append("")

    # X agent route
    routes = payload.get("agent_routes", {})
    if "x" in routes:
        out.append("## X (agent-driven discovery — corroborate cross-verified themes)")
        out.append(f"> {routes['x']['reason']}")
        for step in routes["x"]["do"]:
            out.append(f"- {step}")
        out.append("")

    # Diagnostics (R5 — name what failed, don't bury it)
    if bad_srcs:
        out.append("## Limits (R5 — blocked sources reported, not hidden)")
        for d in bad_srcs:
            out.append(f"- **{d['source']}**: {d['note']}")
    return "\n".join(out)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python3 -m research",
                                description="Multi-source trend/research collector.")
    p.add_argument("query", help="What to research (free text; Korean OK).")
    p.add_argument("--sources", default=",".join(DEFAULT_SOURCES),
                   help=f"Comma list or 'all'. Known: {','.join(COLLECTORS)},x "
                        f"(default: {','.join(DEFAULT_SOURCES)}).")
    p.add_argument("--since", default="7d",
                   help="Recency window: 24h, 7d, 2w, 1m, or '' for no cutoff (default 7d).")
    p.add_argument("--limit", type=int, default=15, help="Max items per source (default 15).")
    p.add_argument("--format", choices=("json", "md", "report"), default="json",
                   help="Output format. 'report' auto-synthesizes a cross-verified "
                        "cited markdown report (R2 — items in ≥2 sources surfaced as "
                        "verified; single-source items flagged unverified).")
    p.add_argument("--max-workers", type=int, default=6, help="Concurrent collectors.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    requested = _parse_sources(args.sources)
    scriptable = [s for s in requested if s in COLLECTORS]
    since = args.since if args.since.strip() else None

    items, diag, by_source = collect(
        args.query, sources=scriptable, since=since,
        limit=args.limit, max_workers=args.max_workers,
    )

    payload = {
        "query": args.query,
        "since": since,
        "generated_at": iso(now_utc()),
        "sources_requested": requested,
        "by_source": by_source,
        "items": [it.to_dict() for it in items],
        "diagnostics": diag,
        "agent_routes": {},
    }
    if "x" in requested:
        payload["agent_routes"]["x"] = _x_route(args.query)

    if args.format == "md":
        print(_digest_md(payload))
    elif args.format == "report":
        print(_report_md(payload))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    return 0 if items else 1


if __name__ == "__main__":
    sys.exit(main())
