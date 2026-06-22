"""Ranking strategies for suggestions.

Two modes, both consuming the same candidate list produced by the trie:

  BASIC (60% requirement): sort purely by all-time `count`. Historically popular queries win.

  RECENCY-AWARE (the 20% "trending" enhancement): blend all-time popularity with a *recency
  boost*. The boost is a time-decayed recent-activity score (see services/trending.py) so a query
  that is being searched a lot *right now* climbs, but a query that was hot only briefly decays
  back down and is never permanently over-ranked.

      final_score = count + RECENCY_WEIGHT * recent_score(query)

We keep the formula additive and simple on purpose: it is easy to explain in the viva and easy to
reason about (recency can only *raise* a query, never bury a genuinely popular one).
"""
from __future__ import annotations

from typing import Callable, Dict, List, Tuple

Candidate = Tuple[str, int, float]  # (query, count, last_ts)


def rank_basic(candidates: List[Candidate], limit: int) -> List[Dict]:
    """Candidates already arrive sorted by count desc from the trie, so this is a slice."""
    out: List[Dict] = []
    for query, count, _ts in candidates[:limit]:
        out.append({"query": query, "count": count, "score": float(count)})
    return out


def rank_recency(
    candidates: List[Candidate],
    recent_score: Callable[[str], float],
    weight: float,
    limit: int,
) -> List[Dict]:
    """Re-score the candidate pool with a recency boost, then take the top `limit`."""
    scored: List[Dict] = []
    for query, count, _ts in candidates:
        recent = recent_score(query)
        score = float(count) + weight * recent
        scored.append({"query": query, "count": count, "recent": recent, "score": score})
    scored.sort(key=lambda d: d["score"], reverse=True)
    return scored[:limit]
