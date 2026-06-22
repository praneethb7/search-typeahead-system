"""Batch writer: collects search submissions in memory and flushes them to SQLite in bulk.

WHY: writing to the primary store on every single search would make the DB the bottleneck and
add latency to each request. Instead:

  - /search just adds +1 to an in-memory buffer (a dict query -> pending_count). Repeated queries
    are AGGREGATED here, so 100 searches of "iphone" become a single "+100".
  - A background loop flushes the whole buffer in ONE transaction when it gets big
    (BATCH_MAX_SIZE distinct queries) OR after BATCH_INTERVAL_SECONDS, whichever comes first.
  - On flush we also update the in-memory trie counts (so suggestions reflect the new popularity)
    and invalidate the affected cache prefixes (so stale cached rankings are dropped).

FAILURE TRADE-OFF (must be discussed in the viva): the buffer is volatile. If the process crashes
between flushes, the un-flushed increments are lost. The exposure is bounded by BATCH_INTERVAL /
BATCH_MAX_SIZE. For exact-once durability we would add an append-only write-ahead log and replay
it on restart -- at the cost of a disk write per search (giving back some of the latency we saved).
For a popularity counter, losing a few seconds of increments is an acceptable trade for the huge
write reduction. We flush on clean shutdown to avoid losing data in the normal case.
"""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Dict


class BatchWriter:
    def __init__(self, store, index, cache, trending, settings) -> None:
        self.store = store
        self.index = index
        self.cache = cache
        self.trending = trending
        self.max_size = int(settings.batch_max_size)
        self.interval = float(settings.batch_interval_seconds)

        self._buffer: Dict[str, int] = {}
        self._lock = threading.Lock()
        self._task: asyncio.Task | None = None
        self._stop: asyncio.Event | None = None
        self._last_flush = time.time()

        # counters for the write-reduction metric
        self.submissions = 0      # every +1 recorded (would-be DB writes without batching)
        self.rows_flushed = 0     # rows actually written across all flushes (with batching)
        self.flush_count = 0

    # ---- ingest (hot path, called from /search) --------------------------------
    def record(self, query: str, n: int = 1) -> None:
        with self._lock:
            self._buffer[query] = self._buffer.get(query, 0) + n
            self.submissions += n

    def _buffer_size(self) -> int:
        with self._lock:
            return len(self._buffer)

    # ---- background loop -------------------------------------------------------
    async def start(self) -> None:
        self._stop = asyncio.Event()
        self._last_flush = time.time()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        assert self._stop is not None
        self._stop.set()
        try:
            await self._task
        except Exception:  # noqa: BLE001
            pass
        await self.flush()  # final flush so a clean shutdown loses nothing

    async def _run(self) -> None:
        assert self._stop is not None
        tick = min(self.interval, 0.5)
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=tick)
            except asyncio.TimeoutError:
                pass
            time_due = (time.time() - self._last_flush) >= self.interval
            size_due = self._buffer_size() >= self.max_size
            if time_due or size_due:
                await self.flush()

    # ---- flush -----------------------------------------------------------------
    async def flush(self) -> int:
        # atomically take the current buffer and reset it
        with self._lock:
            if not self._buffer:
                self._last_flush = time.time()
                return 0
            deltas = self._buffer
            self._buffer = {}

        ts = time.time()
        # 1) one bulk transaction to the primary store (off the event loop)
        await asyncio.to_thread(self.store.batch_upsert, deltas, ts)

        # 2) keep the in-memory trie in sync (counts only go up -> cheap promote)
        for query, delta in deltas.items():
            new_count = self.index.scores.get(query, 0) + delta
            self.index.update(query, new_count, ts)

        # 3) invalidate cached suggestions whose ranking may have changed
        if self.cache is not None:
            self.cache.invalidate_for_queries(deltas.keys())

        self.flush_count += 1
        self.rows_flushed += len(deltas)
        self._last_flush = ts
        return len(deltas)

    # ---- stats -----------------------------------------------------------------
    def stats(self) -> Dict:
        with self._lock:
            buffered = len(self._buffer)
        reduction = (
            1.0 - (self.rows_flushed / self.submissions) if self.submissions else 0.0
        )
        return {
            "submissions_received": self.submissions,
            "rows_written_via_batches": self.rows_flushed,
            "flushes": self.flush_count,
            "buffered_now": buffered,
            "batch_max_size": self.max_size,
            "batch_interval_seconds": self.interval,
            "estimated_write_reduction": round(reduction, 4),
        }
