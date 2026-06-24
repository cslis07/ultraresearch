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
    p.add_argument("--format", choices=("json", "md"), default="json",
                   help="Output format (default json).")
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
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    return 0 if items else 1


if __name__ == "__main__":
    sys.exit(main())
