"""Handle a submitted search.

A submission does three cheap, in-memory things and returns immediately:
  1. records the query for recency/trending,
  2. enqueues a +1 count into the batch writer (NOT a synchronous DB write),
  3. returns the dummy {"message": "Searched"} response.

The actual DB update happens later, in one batched transaction (see services/batch_writer.py).
That is what keeps search submission fast and the database write rate low.
"""
from __future__ import annotations

from typing import Optional

from backend.services.suggest_service import normalize_prefix


class SearchService:
    def __init__(self, batch_writer, trending=None) -> None:
        self.batch_writer = batch_writer
        self.trending = trending

    def submit(self, raw_query: Optional[str]) -> str:
        query = normalize_prefix(raw_query)  # same normalization as suggestions: trim + lowercase
        if not query:
            return ""  # ignore blank submissions but still return a dummy response upstream
        # recency first (so trending reflects the search even before the batch flush)
        if self.trending is not None:
            self.trending.record(query)
        # enqueue the count increment for the batch writer to flush later
        self.batch_writer.record(query)
        return query
