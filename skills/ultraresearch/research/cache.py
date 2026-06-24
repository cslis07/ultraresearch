"""File-based query cache for ultraresearch.

Keyed on (query, sources, since, limit). The cache exists so that repeated
research queries don't hammer external APIs during iteration, and so a web
deployment can serve hot queries from /tmp without re-doing the fan-out.

Intentionally simple: one JSON file per key, mtime is the TTL clock. No
locking — concurrent writes overwrite each other harmlessly (same key
implies same result).
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Optional


def _key(query: str, sources: list[str], since: Optional[str], limit: int) -> str:
    canon = json.dumps({"q": query.strip().lower(), "s": sorted(sources),
                        "since": since or "", "limit": limit},
                       ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(canon.encode("utf-8")).hexdigest()[:16]


def load(cache_dir: Optional[str], query: str, sources: list[str],
         since: Optional[str], limit: int, ttl: int) -> Optional[dict]:
    if not cache_dir or ttl <= 0:
        return None
    path = Path(cache_dir) / f"{_key(query, sources, since, limit)}.json"
    if not path.exists():
        return None
    if (time.time() - path.stat().st_mtime) > ttl:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def store(cache_dir: Optional[str], query: str, sources: list[str],
          since: Optional[str], limit: int, payload: dict) -> None:
    if not cache_dir:
        return
    try:
        os.makedirs(cache_dir, exist_ok=True)
        path = Path(cache_dir) / f"{_key(query, sources, since, limit)}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass  # cache failures must never break a real query
