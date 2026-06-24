"""ultraresearch — multi-source research/trend collector.

Turns the single-URL insane-search engine into a fan-out research primitive:
one query -> concurrent collection across Hacker News, Reddit, dev.to, GitHub,
and arXiv -> normalized, deduped, ranked items the agent can verify and cite.

Public API:
    from research.collectors import collect, COLLECTORS, DEFAULT_SOURCES
    from research.normalize import Item
"""
from .normalize import Item
from .collectors import collect, COLLECTORS, DEFAULT_SOURCES

__all__ = ["Item", "collect", "COLLECTORS", "DEFAULT_SOURCES"]
