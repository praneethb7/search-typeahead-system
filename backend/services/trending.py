"""Recency tracker: powers both `/trending` and the recency boost in suggestion ranking.

THE FOUR THINGS THE ASSIGNMENT ASKS US TO EXPLAIN:

1. How recent searches are tracked:
   We keep a SLIDING WINDOW of fixed-size time buckets (e.g. 1-minute buckets over a 10-minute
   window). Each search increments its query's counter in the *current* bucket. Buckets that fall
   out of the window are dropped, so memory stays bounded.

2. How recent activity affects ranking:
   `recent_score(query)` sums the query's per-bucket counts, each multiplied by an EXPONENTIAL
   DECAY weight based on the bucket's age (half-life configurable). Newer activity counts for more.
   Suggestion ranking then does:  final = all_time_count + RECENCY_WEIGHT * recent_score.

3. How we avoid permanently over-ranking a short-lived spike:
   Because old buckets both decay (half-life) AND eventually leave the window entirely, a query
   that was hot for five minutes returns to its baseline afterwards. Nothing is pinned to the top.

4. How the cache stays consistent when rankings change:
   Recency-mode suggestions are cached with a short TTL, and every batch flush invalidates the
   affected prefixes (see services/batch_writer.py), so changed rankings surface quickly.
"""
from __future__ import annotations

import threading
import time
from typing import Dict, List


class TrendingTracker:
    def __init__(self, settings) -> None:
        self.bucket_seconds = max(1, int(settings.trending_bucket_seconds))
        self.num_buckets = max(
            1, int(settings.trending_window_seconds // self.bucket_seconds)
        )
        self.halflife = float(settings.trending_halflife_seconds)
        self._buckets: Dict[int, Dict[str, int]] = {}  # bucket_index -> {query: count}
        self._lock = threading.Lock()

    def _bucket_index(self, now: float) -> int:
        return int(now // self.bucket_seconds)

    def _weight(self, age_seconds: float) -> float:
        if self.halflife <= 0:
            return 1.0
        return 0.5 ** (age_seconds / self.halflife)

    def _prune(self, current_index: int) -> None:
        oldest_allowed = current_index - self.num_buckets + 1
        for idx in [i for i in self._buckets if i < oldest_allowed]:
            del self._buckets[idx]

    # ---- write -----------------------------------------------------------------
    def record(self, query: str, n: int = 1) -> None:
        idx = self._bucket_index(time.time())
        with self._lock:
            self._prune(idx)
            bucket = self._buckets.setdefault(idx, {})
            bucket[query] = bucket.get(query, 0) + n

    # ---- read ------------------------------------------------------------------
    def recent_score(self, query: str) -> float:
        """Time-decayed recent activity for a single query (used by recency-aware ranking)."""
        idx = self._bucket_index(time.time())
        oldest = idx - self.num_buckets + 1
        score = 0.0
        with self._lock:
            for bidx, counts in self._buckets.items():
                if bidx < oldest:
                    continue
                c = counts.get(query)
                if c:
                    score += c * self._weight((idx - bidx) * self.bucket_seconds)
        return score

    def top(self, limit: int = 10) -> List[Dict]:
        """The currently trending queries, by decayed recent activity."""
        idx = self._bucket_index(time.time())
        oldest = idx - self.num_buckets + 1
        agg: Dict[str, float] = {}
        with self._lock:
            for bidx, counts in self._buckets.items():
                if bidx < oldest:
                    continue
                w = self._weight((idx - bidx) * self.bucket_seconds)
                for q, c in counts.items():
                    agg[q] = agg.get(q, 0.0) + c * w
        ranked = sorted(agg.items(), key=lambda kv: kv[1], reverse=True)[:limit]
        return [{"query": q, "recent": round(s, 4)} for q, s in ranked]
