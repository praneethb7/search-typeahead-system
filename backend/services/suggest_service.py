"""The suggestion flow: normalize -> cache -> (on miss) trie + ranking -> cache.

This is the single place that ties together the cache and the in-memory index, so the
"cache before falling back to the primary index" requirement lives in one readable method.

Dependencies (cache, trending) are *injected* rather than imported, so:
  - this module has no hard dependency on Redis or the trending tracker (easy to unit test), and
  - the basic version works with cache=None / trending=None (used before those features exist).
"""
from __future__ import annotations

from typing import Dict, List, Optional

from backend.index import ranking


def normalize_prefix(raw: Optional[str]) -> str:
    """Handle empty/missing/mixed-case input gracefully: trim and lowercase."""
    if not raw:
        return ""
    return raw.strip().lower()


class SuggestionService:
    def __init__(
        self,
        index,
        *,
        limit: int,
        recency_weight: float,
        cache=None,
        trending=None,
    ) -> None:
        self.index = index
        self.limit = limit
        self.recency_weight = recency_weight
        self.cache = cache          # distributed cache (optional)
        self.trending = trending    # recency tracker (optional)

    def suggest(self, raw_prefix: Optional[str], *, recency: bool = False) -> Dict:
        prefix = normalize_prefix(raw_prefix)
        if not prefix:
            return {"prefix": "", "suggestions": [], "source": "empty"}

        mode = "trending" if (recency and self.trending is not None) else "basic"

        # 1) cache first (the low-latency path). The cache routes `prefix` to one of the
        #    Redis nodes via consistent hashing internally.
        if self.cache is not None:
            cached = self.cache.get(prefix, mode)
            if cached is not None:
                return {"prefix": prefix, "suggestions": cached, "source": "cache"}

        # 2) miss -> compute from the trie candidate pool + ranking.
        candidates = self.index.candidates(prefix)  # top-K by count, sorted desc
        if mode == "trending":
            suggestions = ranking.rank_recency(
                candidates,
                recent_score=self.trending.recent_score,
                weight=self.recency_weight,
                limit=self.limit,
            )
        else:
            suggestions = ranking.rank_basic(candidates, self.limit)

        # 3) populate the cache for next time (with a TTL handled by the cache layer).
        if self.cache is not None:
            self.cache.set(prefix, mode, suggestions)

        return {"prefix": prefix, "suggestions": suggestions, "source": "index"}
