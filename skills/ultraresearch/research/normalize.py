"""Item schema, time helpers, dedup, and ranking for the research collector.

Every collector returns a list of `Item`. This module owns the normalized shape
so the agent gets one consistent record type across Hacker News, Reddit, dev.to,
GitHub, and arXiv — regardless of how each source spells its fields.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from typing import Optional
from urllib.parse import urlsplit, urlunsplit


# --- item schema -------------------------------------------------------------
@dataclass
class Item:
    source: str                          # "hn" | "reddit" | "devto" | "github" | "arxiv"
    title: str
    url: str
    author: Optional[str] = None
    score: Optional[int] = None          # native popularity signal (points/stars/reactions)
    comments: Optional[int] = None
    created_at: Optional[str] = None     # ISO-8601 UTC
    snippet: Optional[str] = None
    query: Optional[str] = None          # the search angle that surfaced this item
    route: Optional[str] = None          # provenance: how it was fetched
    age_hours: Optional[float] = None
    hotness: Optional[float] = None       # within-source sort key (NOT cross-source comparable)

    def to_dict(self) -> dict:
        return asdict(self)


# --- time helpers ------------------------------------------------------------
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_SINCE_RE = re.compile(r"^\s*(\d+)\s*([hdwm])\s*$", re.IGNORECASE)
_UNIT_HOURS = {"h": 1, "d": 24, "w": 24 * 7, "m": 24 * 30}


def parse_since(since: Optional[str]) -> Optional[float]:
    """'24h' / '7d' / '2w' / '1m' -> hours. None/'' -> None (no cutoff)."""
    if not since:
        return None
    m = _SINCE_RE.match(since)
    if not m:
        return None
    return float(m.group(1)) * _UNIT_HOURS[m.group(2).lower()]


def cutoff_dt(since: Optional[str], ref: Optional[datetime] = None) -> Optional[datetime]:
    hours = parse_since(since)
    if hours is None:
        return None
    return (ref or now_utc()) - timedelta(hours=hours)


def reddit_time_window(since: Optional[str]) -> str:
    """Map a --since value to Reddit's t= bucket (hour/day/week/month/year/all)."""
    hours = parse_since(since)
    if hours is None:
        return "all"
    if hours <= 1:
        return "hour"
    if hours <= 24:
        return "day"
    if hours <= 24 * 7:
        return "week"
    if hours <= 24 * 31:
        return "month"
    if hours <= 24 * 366:
        return "year"
    return "all"


def to_iso(value) -> Optional[str]:
    """Best-effort coercion of common timestamp shapes to ISO-8601 UTC."""
    if value is None:
        return None
    if isinstance(value, (int, float)):  # unix epoch seconds
        return iso(datetime.fromtimestamp(value, tz=timezone.utc))
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return iso(dt)
        except ValueError:
            return value  # leave as-is; caller still has a string
    return None


# --- ranking & dedup ---------------------------------------------------------
def _age_hours(created_at: Optional[str], ref: datetime) -> Optional[float]:
    if not created_at:
        return None
    try:
        s = created_at[:-1] + "+00:00" if created_at.endswith("Z") else created_at
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return max(0.0, (ref - dt).total_seconds() / 3600.0)


def _hotness(score: Optional[int], age_hours: Optional[float]) -> float:
    """HN-style gravity. Within-source only — raw scores differ per platform."""
    s = float(score or 0)
    a = age_hours if age_hours is not None else 48.0
    return (s + 1.0) / pow(a + 2.0, 1.5)


def enrich(items: list[Item], ref: Optional[datetime] = None) -> list[Item]:
    ref = ref or now_utc()
    for it in items:
        it.age_hours = _age_hours(it.created_at, ref)
        if it.age_hours is not None:
            it.age_hours = round(it.age_hours, 1)
        it.hotness = round(_hotness(it.score, it.age_hours), 4)
    return items


def _norm_url(url: str) -> str:
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return url.strip().lower()
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = p.path.rstrip("/").lower()
    return urlunsplit((p.scheme.lower() or "https", host, path, "", ""))


def dedupe(items: list[Item]) -> list[Item]:
    """Collapse the same link surfaced by several queries; keep the richer record."""
    best: dict[str, Item] = {}
    for it in items:
        key = _norm_url(it.url) if it.url else f"{it.source}:{it.title.lower()}"
        cur = best.get(key)
        if cur is None or (it.score or 0) > (cur.score or 0):
            if cur is not None and cur.query and it.query and cur.query != it.query:
                it.query = f"{cur.query}; {it.query}"  # remember it matched multiple angles
            best[key] = it
    return list(best.values())


def rank_within_source(items: list[Item]) -> list[Item]:
    return sorted(items, key=lambda it: (it.hotness or 0.0), reverse=True)
