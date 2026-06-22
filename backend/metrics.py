"""In-process metrics: request latency percentiles and cache hit/miss.

Deliberately tiny and dependency-free. The latency middleware feeds `record_latency`, the cache
layer feeds `note_cache`, and `/metrics` reads `snapshot()`. Samples are kept in a bounded deque
per path so memory is constant; percentiles are computed on demand.
"""
from __future__ import annotations

import threading
from collections import defaultdict, deque
from typing import Deque, Dict

_LOCK = threading.Lock()
_MAX_SAMPLES = 5000
_latency: Dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=_MAX_SAMPLES))
_cache = {"hits": 0, "misses": 0}


def record_latency(path: str, ms: float) -> None:
    with _LOCK:
        _latency[path].append(ms)


def note_cache(hit: bool) -> None:
    with _LOCK:
        _cache["hits" if hit else "misses"] += 1


def _percentile(sorted_vals, p: float) -> float:
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    if lo == hi:
        return float(sorted_vals[lo])
    return float(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo))


def snapshot() -> Dict:
    with _LOCK:
        latency_out: Dict[str, Dict] = {}
        for path, samples in _latency.items():
            vals = sorted(samples)
            latency_out[path] = {
                "count": len(vals),
                "p50_ms": round(_percentile(vals, 0.50), 3),
                "p95_ms": round(_percentile(vals, 0.95), 3),
                "p99_ms": round(_percentile(vals, 0.99), 3),
                "max_ms": round(vals[-1], 3) if vals else 0.0,
            }
        hits, misses = _cache["hits"], _cache["misses"]
        total = hits + misses
        cache_out = {
            "hits": hits,
            "misses": misses,
            "lookups": total,
            "hit_rate": round(hits / total, 4) if total else 0.0,
        }
    return {"latency": latency_out, "cache": cache_out}


def reset() -> None:
    with _LOCK:
        _latency.clear()
        _cache["hits"] = 0
        _cache["misses"] = 0
