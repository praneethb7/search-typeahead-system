# API Reference

Base URL: `http://localhost:8000` · Interactive docs (Swagger): `http://localhost:8000/docs`

All inputs are normalized server-side (trimmed + lowercased), so `IP`, ` ip `, and `ip` behave
identically. Empty, missing, mixed-case, and no-match inputs all return gracefully.

---

## `GET /suggest`

Return up to 10 prefix-matching suggestions sorted by score.

**Query params**

| name | type | default | description |
|---|---|---|---|
| `q` | string | `""` | the prefix the user typed |
| `recency` | bool | `true` | `true` = recency-aware ranking; `false` = pure all-time count |

**Example**
```bash
curl "http://localhost:8000/suggest?q=iph&recency=false"
```
```json
{
  "prefix": "iph",
  "source": "index",
  "suggestions": [
    {"query": "iphone", "count": 2000000, "score": 2000000.0, "recent": null},
    {"query": "iphone price", "count": 400000, "score": 400000.0, "recent": null},
    {"query": "iphone review", "count": 266666, "score": 266666.0, "recent": null}
  ]
}
```
`source` is `cache` (served from Redis), `index` (computed from the trie), or `empty` (blank `q`).
In recency mode each item also carries a `recent` boost value.

---

## `POST /search`

Submit a search. Returns the dummy response and records the query (recency + batched count).

**Body**
```json
{ "query": "iphone 15 pro" }
```
**Response**
```json
{ "message": "Searched" }
```
If the query is new it is inserted with an initial count on the next flush; if it exists its count
increases. The update surfaces in `/suggest` and `/trending` after the next batch flush
(≤ `BATCH_INTERVAL_SECONDS`).

---

## `GET /trending`

Currently trending queries, ranked by time-decayed recent activity.

**Query params:** `limit` (1–50, default 10).
```json
{ "trending": [ {"query": "iphone 15 pro", "recent": 12.84}, {"query": "java tutorial", "recent": 9.1} ] }
```

---

## `GET /cache/debug`

Show which cache node owns a prefix (consistent hashing) and whether it is cached.

**Query params:** `prefix` (required), `mode` (`basic` | `trending`, default `basic`).
```bash
curl "http://localhost:8000/cache/debug?prefix=iph"
```
```json
{
  "prefix": "iph", "mode": "basic", "key": "suggest:basic:iph",
  "owner_node": "c1", "endpoint": "localhost:6380", "node_available": true,
  "hit": true, "ttl_seconds": 23,
  "value": [ {"query": "iphone", "count": 2000000, "score": 2000000.0} ]
}
```

---

## `GET /metrics`

Latency percentiles, cache hit rate, DB read/write counters, and batch-writer stats.
```json
{
  "latency": { "/suggest": {"count": 6000, "p50_ms": 0.9, "p95_ms": 2.6, "p99_ms": 4.7, "max_ms": 14.0} },
  "cache":   { "hits": 4710, "misses": 290, "lookups": 5000, "hit_rate": 0.942 },
  "db":      { "rows_read": 100000, "rows_written": 60, "write_transactions": 4 },
  "batch":   { "submissions_received": 3000, "rows_written_via_batches": 60, "flushes": 4,
               "estimated_write_reduction": 0.98 },
  "index_size": 104260, "store_rows": 104260
}
```

---

## `GET /healthz`

Liveness probe → `{ "status": "ok" }`.
