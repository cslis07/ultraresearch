"""Per-source collectors for the ultraresearch harness.

Design goals:
  * Zero-install for the open sources. Hacker News, dev.to, GitHub, and arXiv all
    publish unauthenticated JSON/Atom that is not WAF-gated, so they go through
    Python's stdlib `urllib` and need nothing installed.
  * Block-resistant where it counts. Reddit gates its API, so its collector uses
    curl_cffi TLS impersonation, falling back to the bundled insane-search engine.
  * Never crash the run. A failing source records a diagnostic and returns [],
    so one dead endpoint never sinks the whole research pass.

Each collector signature: collect_x(query, *, since, limit, diag) -> list[Item]
where `diag` is a list the collector appends one status dict to.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional
from urllib.parse import quote, quote_plus
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from .normalize import (
    Item, cutoff_dt, now_utc, parse_since, reddit_time_window, to_iso,
    enrich, dedupe, rank_within_source,
)

# Make the bundled engine importable (skills/ultraresearch/ is the engine's root).
_SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SKILL_ROOT not in sys.path:
    sys.path.insert(0, _SKILL_ROOT)

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
       "(KHTML, like Gecko) Version/17.4 Safari/605.1.15")


# --- HTTP helpers ------------------------------------------------------------
def _http(url: str, *, headers: Optional[dict] = None, timeout: int = 20) -> str:
    h = {"User-Agent": _UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9,ko;q=0.8"}
    if headers:
        h.update(headers)
    req = Request(url, headers=h)
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return raw.decode("utf-8", errors="replace")


def _http_json(url: str, *, headers: Optional[dict] = None, timeout: int = 20):
    return json.loads(_http(url, headers=headers, timeout=timeout))


def _cffi_text(url: str, *, impersonate: str = "safari", timeout: int = 20,
               accept: str = "application/atom+xml,application/xml,*/*;q=0.8") -> Optional[str]:
    """Reddit-grade fetch: real TLS fingerprint. Returns None if curl_cffi absent/fails."""
    try:
        from curl_cffi import requests as r  # type: ignore
    except Exception:
        return None
    try:
        resp = r.get(url, impersonate=impersonate, timeout=timeout,  # type: ignore[arg-type]
                     headers={"Accept": accept,
                              "Accept-Language": "en-US,en;q=0.9,ko;q=0.8"},
                     allow_redirects=True)
        if resp.status_code == 200 and resp.text:
            return resp.text
    except Exception:
        return None
    return None


def _engine_text(url: str, *, selectors: Optional[list] = None, timeout: int = 25) -> Optional[str]:
    """Last-resort fetch through the bundled insane-search escalation engine."""
    try:
        from engine import fetch  # type: ignore
    except Exception:
        return None
    try:
        res = fetch(url, success_selectors=selectors, timeout=timeout, enable_playwright=False)
        return res.content if res.ok else None
    except Exception:
        return None


def _matches(query: str, *fields: Optional[str]) -> bool:
    """Cheap relevance filter for sources whose API has no free-text search."""
    terms = [t for t in query.lower().split() if len(t) > 1]
    if not terms:
        return True
    hay = " ".join(f.lower() for f in fields if f)
    return any(t in hay for t in terms)


# --- Hacker News (Algolia, open JSON) ----------------------------------------
def collect_hn(query: str, *, since: Optional[str], limit: int, diag: list) -> list[Item]:
    ts = cutoff_dt(since)
    numeric = f"&numericFilters=created_at_i>{int(ts.timestamp())}" if ts else ""
    url = (f"https://hn.algolia.com/api/v1/search?query={quote(query)}"
           f"&tags=story&hitsPerPage={limit}{numeric}")
    try:
        data = _http_json(url)
    except Exception as e:
        diag.append({"source": "hn", "ok": False, "note": f"{type(e).__name__}: {e}"})
        return []
    items: list[Item] = []
    for h in data.get("hits", []):
        oid = h.get("objectID")
        link = h.get("url") or f"https://news.ycombinator.com/item?id={oid}"
        items.append(Item(
            source="hn", title=h.get("title") or h.get("story_title") or "(untitled)",
            url=link, author=h.get("author"), score=h.get("points"),
            comments=h.get("num_comments"),
            created_at=to_iso(h.get("created_at_i") or h.get("created_at")),
            snippet=(h.get("story_text") or "")[:280] or None,
            query=query, route="hn:algolia"))
    diag.append({"source": "hn", "ok": True, "note": f"{len(items)} stories via algolia"})
    return items


# --- Reddit (search.rss via TLS impersonation) -------------------------------
_ATOM = "{http://www.w3.org/2005/Atom}"


def collect_reddit(query: str, *, since: Optional[str], limit: int, diag: list) -> list[Item]:
    window = reddit_time_window(since)
    url = (f"https://www.reddit.com/search.rss?q={quote_plus(query)}"
           f"&sort=top&t={window}&limit={limit}")
    text = _cffi_text(url)
    route = "reddit:search.rss(cffi)"
    if not text:
        text = _engine_text(url)
        route = "reddit:search.rss(engine)"
    if not text:
        diag.append({"source": "reddit", "ok": False,
                     "note": "blocked — install curl_cffi>=0.15 (pip install -U curl_cffi) "
                             "or run the bundled engine; Reddit gates plain requests"})
        return []
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        diag.append({"source": "reddit", "ok": False, "note": f"rss parse: {e}"})
        return []
    items: list[Item] = []
    for entry in root.findall(f"{_ATOM}entry"):
        title = entry.findtext(f"{_ATOM}title") or "(untitled)"
        link_el = entry.find(f"{_ATOM}link")
        link = link_el.get("href") if link_el is not None else None
        author = entry.findtext(f"{_ATOM}author/{_ATOM}name")
        updated = entry.findtext(f"{_ATOM}updated") or entry.findtext(f"{_ATOM}published")
        if not link or "/comments/" not in link:
            continue  # keep real posts; drop subreddit/user-profile result rows
        items.append(Item(
            source="reddit", title=title.strip(), url=link, author=author,
            score=None, comments=None, created_at=to_iso(updated),
            snippet=None, query=query, route=route))
    diag.append({"source": "reddit", "ok": True,
                 "note": f"{len(items)} posts via {route} (t={window}; RSS has no score)"})
    return items[:limit]


# --- dev.to (open JSON; filter top articles by query) ------------------------
def collect_devto(query: str, *, since: Optional[str], limit: int, diag: list) -> list[Item]:
    hours = parse_since(since)
    days = max(1, math.ceil(hours / 24)) if hours else 30
    days = min(days, 365)
    url = f"https://dev.to/api/articles?per_page={max(limit * 3, 30)}&top={days}"
    try:
        data = _http_json(url)
    except Exception as e:
        diag.append({"source": "devto", "ok": False, "note": f"{type(e).__name__}: {e}"})
        return []
    items: list[Item] = []
    for a in data:
        tags = " ".join(a.get("tag_list") or [])
        if not _matches(query, a.get("title"), a.get("description"), tags):
            continue
        user = a.get("user") or {}
        items.append(Item(
            source="devto", title=a.get("title") or "(untitled)",
            url=a.get("url"), author=user.get("name") or user.get("username"),
            score=a.get("positive_reactions_count"), comments=a.get("comments_count"),
            created_at=to_iso(a.get("published_at") or a.get("published_timestamp")),
            snippet=(a.get("description") or "")[:280] or None,
            query=query, route="devto:articles"))
        if len(items) >= limit:
            break
    diag.append({"source": "devto", "ok": True,
                 "note": f"{len(items)} articles (top {days}d, query-filtered)"})
    return items


# --- GitHub (open search API; star-sorted, recently pushed) ------------------
def collect_github(query: str, *, since: Optional[str], limit: int, diag: list) -> list[Item]:
    ts = cutoff_dt(since)
    pushed = f"+pushed:>{ts.strftime('%Y-%m-%d')}" if ts else ""
    url = (f"https://api.github.com/search/repositories?q={quote(query)}{pushed}"
           f"&sort=stars&order=desc&per_page={limit}")
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        data = _http_json(url, headers=headers)
    except HTTPError as e:
        note = "rate-limited (set GITHUB_TOKEN to raise the limit)" if e.code == 403 else f"HTTP {e.code}"
        diag.append({"source": "github", "ok": False, "note": note})
        return []
    except Exception as e:
        diag.append({"source": "github", "ok": False, "note": f"{type(e).__name__}: {e}"})
        return []
    items: list[Item] = []
    for r in data.get("items", []):
        owner = (r.get("owner") or {}).get("login")
        items.append(Item(
            source="github", title=r.get("full_name") or r.get("name") or "(repo)",
            url=r.get("html_url"), author=owner, score=r.get("stargazers_count"),
            comments=r.get("open_issues_count"),
            created_at=to_iso(r.get("pushed_at") or r.get("created_at")),
            snippet=(r.get("description") or "")[:280] or None,
            query=query, route="github:search"))
    diag.append({"source": "github", "ok": True, "note": f"{len(items)} repos (stars desc)"})
    return items


# --- arXiv (open Atom API) ---------------------------------------------------
def collect_arxiv(query: str, *, since: Optional[str], limit: int, diag: list) -> list[Item]:
    url = (f"http://export.arxiv.org/api/query?search_query=all:{quote(query)}"
           f"&sortBy=submittedDate&sortOrder=descending&max_results={limit}")
    try:
        text = _http(url)
        root = ET.fromstring(text)
    except Exception as e:
        diag.append({"source": "arxiv", "ok": False, "note": f"{type(e).__name__}: {e}"})
        return []
    items: list[Item] = []
    for entry in root.findall(f"{_ATOM}entry"):
        title = (entry.findtext(f"{_ATOM}title") or "(untitled)").strip().replace("\n", " ")
        link = entry.findtext(f"{_ATOM}id")
        authors = [a.findtext(f"{_ATOM}name") for a in entry.findall(f"{_ATOM}author")]
        published = entry.findtext(f"{_ATOM}published")
        summary = (entry.findtext(f"{_ATOM}summary") or "").strip().replace("\n", " ")
        items.append(Item(
            source="arxiv", title=title, url=link,
            author=", ".join(a for a in authors if a)[:120] or None,
            score=None, comments=None, created_at=to_iso(published),
            snippet=summary[:280] or None, query=query, route="arxiv:api"))
    diag.append({"source": "arxiv", "ok": True, "note": f"{len(items)} papers (newest first)"})
    return items


# --- Bluesky (AT Protocol searchPosts — scriptable social keyword search) -----
_BSKY_NSFW_LABELS = {"porn", "sexual", "nudity", "graphic-media", "sexual-figurative"}
_BSKY_NSFW_TAGS = ("#nsfw", "#nsfwbsky", "#porn", "#gayporn", "#gaysex", "#gaycum",
                   "#goonsky", "#goon", "#gooner", "#lewd", "#hentai", "#onlyfans", "#nsfwart")


def _bsky_is_nsfw(post: dict, rec: dict) -> bool:
    """Public Bluesky search is adult-content-heavy; drop self-labeled NSFW posts."""
    vals = {l.get("val") for l in (post.get("labels") or []) if isinstance(l, dict)}
    self_labels = (rec.get("labels") or {})
    if isinstance(self_labels, dict):
        vals |= {l.get("val") for l in (self_labels.get("values") or []) if isinstance(l, dict)}
    if vals & _BSKY_NSFW_LABELS:
        return True
    txt = (rec.get("text") or "").lower()
    return any(tag in txt for tag in _BSKY_NSFW_TAGS)


def collect_bluesky(query: str, *, since: Optional[str], limit: int, diag: list) -> list[Item]:
    # Use api.bsky.app, NOT public.api.bsky.app: the latter 403s from some regions
    # (e.g. Korea, via a BunnyCDN-KR edge). The AppView gates on TLS, so go cffi.
    ts = cutoff_dt(since)
    since_q = f"&since={quote(ts.strftime('%Y-%m-%dT%H:%M:%SZ'))}" if ts else ""
    url = (f"https://api.bsky.app/xrpc/app.bsky.feed.searchPosts?q={quote(query)}"
           f"&limit={min(limit, 100)}&sort=top{since_q}")
    text = _cffi_text(url, accept="application/json")
    if not text:
        text = _engine_text(url)
    if not text:
        diag.append({"source": "bluesky", "ok": False,
                     "note": "blocked — needs curl_cffi>=0.15; verify api.bsky.app reachable"})
        return []
    try:
        posts = json.loads(text).get("posts", [])
    except Exception as e:
        diag.append({"source": "bluesky", "ok": False, "note": f"json: {e}"})
        return []
    items: list[Item] = []
    dropped = 0
    for p in posts:
        author = (p.get("author") or {}).get("handle")
        rec = p.get("record") or {}
        if _bsky_is_nsfw(p, rec):  # public search is NSFW-heavy; keep brand research clean
            dropped += 1
            continue
        uri = p.get("uri") or ""
        rkey = uri.rsplit("/", 1)[-1] if uri else ""
        weburl = f"https://bsky.app/profile/{author}/post/{rkey}" if (author and rkey) else uri
        items.append(Item(
            source="bluesky", title=(rec.get("text") or "(post)").strip().replace("\n", " ")[:120],
            url=weburl, author=author, score=p.get("likeCount"),
            comments=p.get("replyCount"), created_at=to_iso(rec.get("createdAt")),
            snippet=(rec.get("text") or "")[:280] or None, query=query, route="bluesky:searchPosts"))
    note = f"{len(items)} posts via api.bsky.app (likes=score)"
    if dropped:
        note += f"; dropped {dropped} NSFW"
    diag.append({"source": "bluesky", "ok": True, "note": note})
    return items


# --- Lobsters (no working search.json -> hottest.json + query filter) ---------
def collect_lobsters(query: str, *, since: Optional[str], limit: int, diag: list) -> list[Item]:
    try:
        data = _http_json("https://lobste.rs/hottest.json")
    except Exception as e:
        diag.append({"source": "lobsters", "ok": False, "note": f"{type(e).__name__}: {e}"})
        return []
    items: list[Item] = []
    for s in data:
        tags = " ".join(s.get("tags") or [])
        if not _matches(query, s.get("title"), tags):
            continue
        link = s.get("url") or s.get("comments_url")
        items.append(Item(
            source="lobsters", title=s.get("title") or "(story)", url=link,
            author=(s.get("submitter_user") or {}).get("username")
            if isinstance(s.get("submitter_user"), dict) else s.get("submitter_user"),
            score=s.get("score"), comments=s.get("comment_count"),
            created_at=to_iso(s.get("created_at")),
            snippet=(s.get("description_plain") or s.get("description") or "")[:280] or None,
            query=query, route="lobsters:hottest"))
        if len(items) >= limit:
            break
    diag.append({"source": "lobsters", "ok": True,
                 "note": f"{len(items)} stories (hottest, query-filtered; no time window)"})
    return items


# --- Naver search (blog + news tabs, no auth — Korean coverage) ---------------
# search.naver.com tabs change markup often. We anchor on POST URL regex and
# climb the DOM for the title — far more stable than tab-specific CSS selectors.
_NAVER_BLOG_RE = re.compile(r"https?://(?:m\.)?blog\.naver\.com/[\w-]+/\d{8,}")
_NAVER_NEWS_RE = re.compile(r"https?://n\.news\.naver\.com/(?:mnews/)?article/[\w/.\-?=&]+")


_NAVER_TITLE_JUNK = {
    "네이버뉴스", "Keep에 바로가기", "Keep", "보기", "펼치기", "광고",
    "네이버페이", "더보기", "이전", "다음",
}


def _naver_title_for(anchor, fallback: str) -> str:
    """Find the headline near a Naver search-result anchor.

    Naver search cards put the article URL on a utility anchor (text =
    "네이버뉴스") and the real headline on a separate span/strong. Walk up to
    the card container, then take the first stripped string that looks like a
    headline (12-140 chars, not a known junk label, not numeric, not a URL).
    """
    title = (anchor.get_text(" ", strip=True) or "").strip()
    if title and title not in _NAVER_TITLE_JUNK and 12 <= len(title) <= 140:
        return title[:120]
    card = anchor
    for _ in range(8):
        card = card.find_parent()
        if card is None:
            break
        for s in card.stripped_strings:
            s = s.strip()
            if (12 <= len(s) <= 140 and s not in _NAVER_TITLE_JUNK
                    and not s.isdigit() and not s.startswith("http")):
                return s[:120]
    return (title or fallback)[:120]


def _naver_extract(html: str, post_re: re.Pattern, query: str, route: str,
                   limit: int) -> list[Item]:
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except Exception:
        return []
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    items: list[Item] = []
    for a in soup.find_all("a", href=True):
        m = post_re.search(a["href"])
        if not m:
            continue
        url = m.group(0)
        if url in seen:
            continue
        seen.add(url)
        title = _naver_title_for(a, fallback="(post)")
        items.append(Item(
            source="naver", title=title, url=url,
            author=None, score=None, comments=None, created_at=None,
            snippet=None, query=query, route=route))
        if len(items) >= limit:
            break
    return items


def collect_naver(query: str, *, since: Optional[str], limit: int, diag: list) -> list[Item]:
    headers = {"Accept-Language": "ko-KR,ko;q=0.9,en;q=0.5"}
    tabs = [
        ("blog", "search.naver:blog", _NAVER_BLOG_RE,
         f"https://search.naver.com/search.naver?where=blog&query={quote_plus(query)}&sort=1"),
        ("news", "search.naver:news", _NAVER_NEWS_RE,
         f"https://search.naver.com/search.naver?where=news&query={quote_plus(query)}&sort=1"),
    ]
    per_tab = max(1, limit // 2 + limit % 2)
    items: list[Item] = []
    counts: dict[str, int] = {}
    for tab, route, post_re, url in tabs:
        try:
            from curl_cffi import requests as r  # type: ignore
            x = r.get(url, impersonate="safari", timeout=15, headers=headers,
                      allow_redirects=True)
            html = x.text if x.status_code == 200 else None
        except Exception:
            html = None
        if not html:
            html = _engine_text(url)
        if not html:
            counts[tab] = 0
            continue
        got = _naver_extract(html, post_re, query, route, per_tab)
        items.extend(got)
        counts[tab] = len(got)
    if items:
        diag.append({"source": "naver", "ok": True,
                     "note": (f"{len(items)} (blog={counts.get('blog',0)}, "
                              f"news={counts.get('news',0)}); sort=date desc; "
                              f"no post-level dates/scores from search page")})
    else:
        diag.append({"source": "naver", "ok": False,
                     "note": "no posts extracted — needs curl_cffi + beautifulsoup4; "
                             "or Naver may have changed search markup"})
    return items


# --- registry & orchestrator -------------------------------------------------
COLLECTORS: dict[str, Callable[..., list[Item]]] = {
    "hn": collect_hn,
    "reddit": collect_reddit,
    "bluesky": collect_bluesky,
    "devto": collect_devto,
    "github": collect_github,
    "lobsters": collect_lobsters,
    "arxiv": collect_arxiv,
    "naver": collect_naver,
}

DEFAULT_SOURCES = ["hn", "reddit", "bluesky", "devto", "github"]


def collect(query: str, *, sources: list[str], since: Optional[str] = None,
            limit: int = 15, max_workers: int = 6):
    """Run the requested source collectors concurrently and return one payload."""
    diag: list = []
    selected = [s for s in sources if s in COLLECTORS]
    unknown = [s for s in sources if s not in COLLECTORS and s != "x"]
    for u in unknown:
        diag.append({"source": u, "ok": False, "note": "unknown source — skipped"})

    results: list[Item] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(COLLECTORS[s], query, since=since, limit=limit, diag=diag): s
            for s in selected
        }
        for fut in as_completed(futures):
            try:
                results.extend(fut.result())
            except Exception as e:  # collector swallowed it already, but be safe
                diag.append({"source": futures[fut], "ok": False, "note": f"crash: {e}"})

    results = enrich(results)
    results = dedupe(results)
    results = rank_within_source(results)

    by_source: dict[str, int] = {}
    for it in results:
        by_source[it.source] = by_source.get(it.source, 0) + 1

    return results, diag, by_source
