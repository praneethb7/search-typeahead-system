"""Load test + measurement for the running typeahead server.

It reports the three numbers the assignment asks for:
  1) suggestion latency  -> p50 / p95 / p99 (measured client-side)
  2) cache hit rate       -> read from the server's /metrics after a warm pass
  3) write reduction      -> fire many /search submissions, then show submissions vs DB writes

Prereqs: the server must be running (uvicorn backend.main:app) and Redis up (docker compose up -d).

    python scripts/benchmark.py
    python scripts/benchmark.py --suggest-requests 5000 --concurrency 64 --search-submissions 5000
"""
from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import time

import httpx

# Prefixes a real user might type. Mix of 1-3 char prefixes for broad cache/trie coverage.
PREFIXES = [
    "i", "ip", "ipa", "iph", "a", "an", "and", "j", "ja", "jav", "p", "py", "pyt", "pi", "piz",
    "r", "ru", "run", "s", "st", "sto", "sa", "sam", "m", "ma", "mac", "n", "ne", "net", "c",
    "co", "cof", "cr", "cre", "f", "fl", "fli", "g", "go", "y", "yo", "yog", "e", "el", "ele",
    "b", "bi", "bit", "d", "do", "doc", "k", "ku", "kub", "t", "te", "tes",
]

# A small set of queries to hammer with /search so the buffer aggregates heavily.
SEARCH_QUERIES = [
    "iphone 15 pro", "java tutorial", "python for beginners", "running shoes online",
    "pizza near me", "stock market today", "netflix login", "macbook pro price",
    "bitcoin price", "yoga for beginners", "coffee near me", "flight tickets",
    "samsung galaxy review", "docker tutorial", "kubernetes basics", "electric car price",
    "best gaming mouse", "credit card offers", "tesla model 3", "android phone",
]


def percentile(values, p):
    if not values:
        return 0.0
    vals = sorted(values)
    k = (len(vals) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(vals) - 1)
    if lo == hi:
        return vals[lo]
    return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)


async def measure_suggest(client, base_url, total, concurrency, recency):
    sem = asyncio.Semaphore(concurrency)
    latencies = []
    errors = 0

    async def one():
        nonlocal errors
        prefix = random.choice(PREFIXES)
        async with sem:
            t0 = time.perf_counter()
            try:
                r = await client.get(
                    f"{base_url}/suggest", params={"q": prefix, "recency": str(recency).lower()}
                )
                r.raise_for_status()
            except Exception:  # noqa: BLE001
                errors += 1
                return
            latencies.append((time.perf_counter() - t0) * 1000.0)

    await asyncio.gather(*[one() for _ in range(total)])
    return latencies, errors


async def submit_searches(client, base_url, total, concurrency):
    sem = asyncio.Semaphore(concurrency)

    async def one():
        query = random.choice(SEARCH_QUERIES)
        async with sem:
            try:
                await client.post(f"{base_url}/search", json={"query": query})
            except Exception:  # noqa: BLE001
                pass

    await asyncio.gather(*[one() for _ in range(total)])


def report_latency(title, latencies, errors):
    print(f"\n{title}")
    if not latencies:
        print("  no successful requests (is the server running?)")
        return
    print(f"  requests ok : {len(latencies)}   errors: {errors}")
    print(f"  mean        : {statistics.mean(latencies):.2f} ms")
    print(f"  p50         : {percentile(latencies, 0.50):.2f} ms")
    print(f"  p95         : {percentile(latencies, 0.95):.2f} ms")
    print(f"  p99         : {percentile(latencies, 0.99):.2f} ms")
    print(f"  max         : {max(latencies):.2f} ms")


async def main_async(args):
    async with httpx.AsyncClient(timeout=10.0) as client:
        # sanity check
        try:
            await client.get(f"{args.base_url}/healthz")
        except Exception:  # noqa: BLE001
            print(f"Cannot reach {args.base_url}. Start the server first:\n"
                  f"  uvicorn backend.main:app")
            return

        print("=" * 64)
        print("SEARCH TYPEAHEAD — BENCHMARK")
        print("=" * 64)

        # 1) COLD pass (caches empty-ish) then WARM pass (same prefixes -> cache hits)
        cold, cold_err = await measure_suggest(
            client, args.base_url, args.suggest_requests, args.concurrency, args.recency
        )
        report_latency("[/suggest] cold pass (mostly cache misses)", cold, cold_err)

        warm, warm_err = await measure_suggest(
            client, args.base_url, args.suggest_requests, args.concurrency, args.recency
        )
        report_latency("[/suggest] warm pass (mostly cache hits)", warm, warm_err)

        # 2) cache hit rate (server-side, across both passes)
        m = (await client.get(f"{args.base_url}/metrics")).json()
        cache = m.get("cache", {})
        print("\n[cache] server-side hit rate")
        print(f"  hits: {cache.get('hits')}  misses: {cache.get('misses')}  "
              f"hit_rate: {cache.get('hit_rate')}")

        # 3) write reduction via batching
        print(f"\n[batch] submitting {args.search_submissions} searches over "
              f"{len(SEARCH_QUERIES)} distinct queries ...")
        await submit_searches(client, args.base_url, args.search_submissions, args.concurrency)
        # give the background writer time to flush the final batch
        await asyncio.sleep(args.flush_wait)
        m2 = (await client.get(f"{args.base_url}/metrics")).json()
        batch = m2.get("batch", {})
        db = m2.get("db", {})
        print("  submissions received        :", batch.get("submissions_received"))
        print("  rows written via batches    :", batch.get("rows_written_via_batches"))
        print("  DB write transactions       :", db.get("write_transactions"))
        print("  flushes                     :", batch.get("flushes"))
        print("  estimated write reduction   :", batch.get("estimated_write_reduction"))
        subs = batch.get("submissions_received") or 0
        txns = db.get("write_transactions") or 0
        if subs and txns:
            print(f"  -> {subs} searches collapsed into {txns} DB transactions "
                  f"({subs / txns:.0f}x fewer commits)")

        print("\nPaste these numbers into docs/PERFORMANCE.md.")


def main():
    parser = argparse.ArgumentParser(description="Benchmark the typeahead server.")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--suggest-requests", type=int, default=3000)
    parser.add_argument("--concurrency", type=int, default=32)
    parser.add_argument("--search-submissions", type=int, default=3000)
    parser.add_argument("--recency", action="store_true", help="benchmark recency-aware mode")
    parser.add_argument("--flush-wait", type=float, default=3.0)
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
