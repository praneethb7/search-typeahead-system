"""Pydantic request/response models (drive validation + the auto Swagger docs at /docs)."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class Suggestion(BaseModel):
    query: str
    count: int
    score: float
    recent: Optional[float] = Field(
        default=None, description="recency boost contribution (only in recency-aware mode)"
    )


class SuggestResponse(BaseModel):
    prefix: str
    suggestions: List[Suggestion]
    source: str = Field(description="where the result came from: cache | index | empty")


class SearchRequest(BaseModel):
    query: str = Field(..., description="the query the user submitted")


class SearchResponse(BaseModel):
    message: str = "Searched"


class TrendingItem(BaseModel):
    query: str
    recent: float


class TrendingResponse(BaseModel):
    trending: List[TrendingItem]
