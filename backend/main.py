"""FastAPI application: wires the store, trie index, distributed cache, trending tracker and
batch writer together, exposes the APIs, and serves the frontend.

Startup builds the in-memory trie from SQLite, connects the Redis cache nodes, and launches the
background batch-flush loop. Shutdown flushes any buffered writes so nothing is lost on a clean
stop.
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend import metrics
from backend.cache.cache import DistributedCache
from backend.config import settings
from backend.index.trie import Trie
from backend.models import (
    SearchRequest,
    SearchResponse,
    SuggestResponse,
    TrendingResponse,
)
from backend.services.batch_writer import BatchWriter
from backend.services.search_service import SearchService
from backend.services.suggest_service import SuggestionService, normalize_prefix
from backend.services.trending import TrendingTracker
from backend.store.db import db_stats, store

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

# ---- singletons (constructed cheaply; real I/O happens in the lifespan below) ----------------
index = Trie()
cache = DistributedCache(settings)
trending = TrendingTracker(settings)
batch_writer = BatchWriter(
    store=store, index=index, cache=cache, trending=trending, settings=settings
)
suggest_service = SuggestionService(
    index,
    limit=settings.suggest_limit,
    recency_weight=settings.recency_weight,
    cache=cache,
    trending=trending,
)
search_service = SearchService(batch_writer=batch_writer, trending=trending)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Build the in-memory trie from the primary store (one full read at boot).
    t0 = time.time()
    index.build(store.load_all())
    print(f"[startup] built trie from {index.size:,} queries in {time.time() - t0:.1f}s")
    # Connect the distributed cache (pings each Redis node; degrades gracefully if one is down).
    cache.connect()
    # Start the background batch-flush loop.
    await batch_writer.start()
    yield
    # Clean shutdown: flush buffered writes, then close connections.
    await batch_writer.stop()
    cache.close()


app = FastAPI(title="Search Typeahead", version="1.0.0", lifespan=lifespan)


# ---- latency middleware (feeds p50/p95/p99 in /metrics) --------------------------------------
@app.middleware("http")
async def timing_middleware(request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    metrics.record_latency(request.url.path, elapsed_ms)
    response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.2f}"
    # Local dev/demo: never let the browser cache HTML/CSS/JS, so edits always show up.
    response.headers["Cache-Control"] = "no-store"
    return response


# ---- APIs ------------------------------------------------------------------------------------
@app.get("/suggest", response_model=SuggestResponse, tags=["suggest"])
def suggest(
    q: str = Query("", description="the prefix the user has typed"),
    recency: bool = Query(True, description="use recency-aware ranking (false = pure count)"),
):
    """Up to 10 prefix-matching suggestions, sorted by score (recency-aware by default)."""
    return suggest_service.suggest(q, recency=recency)


@app.post("/search", response_model=SearchResponse, tags=["search"])
def search(req: SearchRequest):
    """Dummy search: records the query (recency + batched count update) and returns 'Searched'."""
    search_service.submit(req.query)
    return SearchResponse(message="Searched")


@app.get("/trending", response_model=TrendingResponse, tags=["trending"])
def get_trending(limit: int = Query(10, ge=1, le=50)):
    """Queries that are hot right now, by time-decayed recent activity."""
    return {"trending": trending.top(limit)}


@app.get("/cache/debug", tags=["cache"])
def cache_debug(
    prefix: str = Query(..., description="prefix key to inspect"),
    mode: str = Query("basic", description="basic | trending"),
):
    """Show which cache node owns the prefix (consistent hashing) and whether it is a hit/miss."""
    return cache.debug(normalize_prefix(prefix), mode)


@app.get("/metrics", tags=["metrics"])
def get_metrics():
    """Latency percentiles, cache hit rate, DB read/write counts, and batch-writer stats."""
    snap = metrics.snapshot()
    snap["index_size"] = index.size
    snap["store_rows"] = store.row_count()
    snap["db"] = db_stats()
    snap["batch"] = batch_writer.stats()
    return snap


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"status": "ok"}


# ---- frontend (served from the same origin -> no CORS) ---------------------------------------
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/", include_in_schema=False)
def serve_ui():
    return FileResponse(str(FRONTEND_DIR / "index.html"))
