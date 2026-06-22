# Performance Report

> How to (re)produce these numbers:
> ```bash
> docker compose up -d
> uvicorn backend.main:app                 # in one terminal
> python scripts/benchmark.py              # in another terminal
> ```
> The table below shows a **sample run**. Replace the numbers with your own from
> `scripts/benchmark.py` and the `/metrics` endpoint before submitting.

---

## 1. Suggestion latency (`GET /suggest`)

Measured client-side by `scripts/benchmark.py` (3,000 requests, concurrency 32), two passes:
a **cold** pass (caches mostly empty) and a **warm** pass (same prefixes → cache hits).

| Pass | mean | p50 | p95 | p99 | max |
|---|---|---|---|---|---|
| Cold (mostly misses) | ~3.1 ms | ~2.4 ms | ~6.8 ms | ~11.5 ms | ~28 ms |
| Warm (mostly hits)   | ~1.2 ms | ~0.9 ms | ~2.6 ms | ~4.7 ms  | ~14 ms |

*Sample numbers — yours will vary with hardware.* The warm pass is faster because answers come
straight from Redis instead of being recomputed from the trie.

**Why it is fast even on a miss:** the trie answers a prefix in `O(len(prefix))` and each node
already holds its top-K candidates, so a miss is a short walk + a small re-rank, not a scan of
100k+ rows.

---

## 2. Cache hit rate

Read from the server's `/metrics` after the two passes:

```
hits: 4710   misses: 290   hit_rate: 0.942
```

The first time a prefix is seen it is a miss (computed from the trie, then cached with a TTL);
repeats within the TTL are hits. Real typeahead traffic is extremely repetitive (everyone types
`i`, `ip`, `iph`…), so hit rates are high. *Replace with your measured value.*

---

## 3. Write reduction from batching

`scripts/benchmark.py` submits **N** searches spread over a small set of distinct queries, then
reads `/metrics`:

| Metric | Sample value |
|---|---|
| Search submissions received | 3,000 |
| Rows written via batches | 60 |
| DB write transactions (commits) | 4 |
| Estimated write reduction | 0.98 (≈ 98%) |
| Collapse ratio | 3,000 searches → 4 commits (**~750× fewer commits**) |

**Why:** repeated queries are aggregated in the in-memory buffer (`"iphone" ×100` → one
`+100`), and the buffer is flushed in a single transaction every `BATCH_INTERVAL_SECONDS`
(default 2s) or when it reaches `BATCH_MAX_SIZE` distinct queries. Without batching, each search
would be its own synchronous DB write.

**Failure trade-off:** buffered increments live in memory, so a crash between flushes loses at
most one interval's worth of counts. We flush on clean shutdown; for stronger durability we would
add an append-only WAL and replay it on startup (trading some of the write savings back).

---

## 4. Consistent-hashing distribution (evidence)

`ConsistentHashRing.distribution(sample_keys)` shows prefix keys spread across the 3 Redis nodes,
and `GET /cache/debug?prefix=<p>` shows the owning node for any prefix. Example:

```
GET /cache/debug?prefix=iph  -> owner_node: c1 (localhost:6380), hit: true,  ttl_seconds: 23
GET /cache/debug?prefix=jav  -> owner_node: c2 (localhost:6381), hit: false
GET /cache/debug?prefix=run  -> owner_node: c0 (localhost:6379), hit: true,  ttl_seconds: 11
```

With 150 virtual nodes per physical node, a sample of prefixes lands roughly evenly (≈ 1/3 each).
Because routing is by consistent hashing, removing one node only remaps that node's ~1/3 of keys
instead of nearly all of them.
