"""Prefix trie with a precomputed top-K candidate list at every node.

WHY A TRIE: typeahead is a *prefix* problem. A trie answers "all queries starting with P" by
walking |P| characters -- O(len(prefix)), independent of dataset size. To also make it fast to
rank, every node caches the top-K most popular completions in its subtree, so a lookup is just
"walk to the node, read its cached list". This is the classic candidate-generation step.

WHY top-K *candidates* (K = 50) and not exactly 10: the basic ranking needs only the top 10 by
count, but the enhanced (recency-aware) ranking re-scores candidates with a recency boost, so it
needs a slightly larger pool to re-rank within. 50 is plenty for surfacing 10 results.

MONOTONIC COUNTS: in this system a query's count only ever *increases* (searches add to it). That
property lets us:
  - build the cache in O(N * avg_len) with NO per-node sorting (insert queries in descending count
    order, so each node's list fills big-to-small automatically), and
  - apply runtime increments exactly with a cheap "promote along the path" update.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Tuple

DEFAULT_POOL = 50  # candidate-list size kept at each node


class _Node:
    __slots__ = ("children", "top")

    def __init__(self) -> None:
        self.children: Dict[str, "_Node"] = {}
        # `top` holds query strings, kept sorted by count descending, capped at the pool size.
        self.top: List[str] = []


class Trie:
    def __init__(self, pool: int = DEFAULT_POOL) -> None:
        self.root = _Node()
        self.pool = pool
        self.scores: Dict[str, int] = {}     # query -> count
        self.last_ts: Dict[str, float] = {}  # query -> last_searched_ts
        self.size = 0

    # ---- build -----------------------------------------------------------------
    def build(self, rows: Iterable[Tuple[str, int, float]]) -> None:
        """(Re)build the whole trie from (query, count, last_ts) rows.

        Sorting by count DESC first means we can append to each node's `top` without ever sorting:
        whatever we append is always <= everything already there, so the list stays sorted.
        """
        self.root = _Node()
        self.scores = {}
        self.last_ts = {}
        rows = sorted(rows, key=lambda r: r[1], reverse=True)
        for query, count, ts in rows:
            self.scores[query] = count
            self.last_ts[query] = ts
            node = self.root
            if len(node.top) < self.pool:
                node.top.append(query)
            for ch in query:
                child = node.children.get(ch)
                if child is None:
                    child = _Node()
                    node.children[ch] = child
                node = child
                if len(node.top) < self.pool:
                    node.top.append(query)
        self.size = len(self.scores)

    # ---- runtime increment -----------------------------------------------------
    def update(self, query: str, new_count: int, ts: float) -> None:
        """Set a query's count to `new_count` (a higher value) and refresh affected node lists.

        Called when a batch flush raises a query's count. Because counts only rise, a query can
        only move *up*, so promoting it along its prefix path keeps every node's top-K exact.
        """
        self.scores[query] = new_count
        self.last_ts[query] = ts
        node = self.root
        self._promote(node, query)
        for ch in query:
            child = node.children.get(ch)
            if child is None:
                child = _Node()
                node.children[ch] = child
            node = child
            self._promote(node, query)
        self.size = len(self.scores)

    def _promote(self, node: _Node, query: str) -> None:
        # COPY-ON-WRITE: we build a brand-new sorted list and swap it in with a single atomic
        # reference assignment (safe under the GIL). Readers in candidates() therefore always see
        # a complete, consistent list -- never one being sorted/mutated in place -- so the
        # background batch flush can update rankings without locking the hot read path.
        top = node.top
        if query in top:
            candidates = top
        elif len(top) < self.pool:
            candidates = top + [query]
        elif self.scores[query] > self.scores[top[-1]]:
            candidates = top[:-1] + [query]  # evict the weakest, add this one
        else:
            return  # not strong enough to enter the pool
        node.top = sorted(candidates, key=lambda q: self.scores[q], reverse=True)

    # ---- read ------------------------------------------------------------------
    def candidates(self, prefix: str, n: int | None = None) -> List[Tuple[str, int, float]]:
        """Return up to `n` (query, count, last_ts) candidates for `prefix`, sorted by count desc.

        Empty/whitespace prefix -> [] (handled gracefully). Prefix with no matches -> []. Input is
        expected already normalized (lowercased) by the caller.
        """
        if not prefix:
            return []
        node = self.root
        for ch in prefix:
            node = node.children.get(ch)
            if node is None:
                return []  # no query starts with this prefix
        n = n if n is not None else self.pool
        return [(q, self.scores[q], self.last_ts.get(q, 0.0)) for q in node.top[:n]]
