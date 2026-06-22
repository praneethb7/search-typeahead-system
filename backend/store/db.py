"""SQLite primary (source-of-truth) store for query-count data.

Why SQLite: file-based, zero-setup, ACID, and it gives us *real* DB read/write counters so we can
prove the write reduction from batching. The single table is intentionally simple:

    queries(query TEXT PRIMARY KEY, count INTEGER, last_searched_ts REAL)

`query` is the natural primary key (and, conceptually, the sharding key if we ever scaled out).
All count updates go through `batch_upsert`, which performs ONE transaction for a whole batch of
buffered searches -- that is the heart of the "batch writes / write reduction" requirement.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from typing import Dict, Iterable, List, Tuple

from backend.config import settings

# Counters used by /metrics to report DB pressure. Reads and writes are tracked separately so we
# can show how few writes actually hit disk once batching is on.
_counters_lock = threading.Lock()
_counters = {
    "rows_read": 0,        # rows pulled from the DB (e.g. full load at startup)
    "rows_written": 0,     # rows inserted/updated across all flushes
    "write_transactions": 0,  # number of flush transactions (each is one fsync-ish commit)
}


def _bump(key: str, n: int = 1) -> None:
    with _counters_lock:
        _counters[key] += n


def db_stats() -> Dict[str, int]:
    with _counters_lock:
        return dict(_counters)


class SqliteStore:
    """Thread-safe wrapper around a single SQLite connection.

    A single connection + lock is plenty for a local demo and keeps the consistency story simple
    (no connection-pool surprises). WAL mode lets readers and the writer coexist smoothly.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS queries (
                    query            TEXT PRIMARY KEY,
                    count            INTEGER NOT NULL DEFAULT 0,
                    last_searched_ts REAL    NOT NULL DEFAULT 0
                )
                """
            )
            self._conn.commit()

    # ---- reads ----------------------------------------------------------------
    def load_all(self) -> List[Tuple[str, int, float]]:
        """Return every (query, count, last_searched_ts). Used to build the in-memory trie."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT query, count, last_searched_ts FROM queries"
            ).fetchall()
        _bump("rows_read", len(rows))
        return rows

    def row_count(self) -> int:
        with self._lock:
            (n,) = self._conn.execute("SELECT COUNT(*) FROM queries").fetchone()
        return int(n)

    def get(self, query: str) -> Tuple[str, int, float] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT query, count, last_searched_ts FROM queries WHERE query = ?",
                (query,),
            ).fetchone()
        _bump("rows_read", 1 if row else 0)
        return row

    # ---- writes ---------------------------------------------------------------
    def batch_upsert(self, deltas: Dict[str, int], ts: float | None = None) -> int:
        """Apply a batch of count *increments* in ONE transaction.

        `deltas` maps query -> how much to add to its count (repeated searches already aggregated
        by the buffer). Existing queries are incremented; new ones are inserted. Returns the number
        of rows written. This single method is what turns N search submissions into 1 DB commit.
        """
        if not deltas:
            return 0
        ts = ts if ts is not None else time.time()
        rows = [(q, int(d), ts) for q, d in deltas.items()]
        with self._lock:
            self._conn.executemany(
                """
                INSERT INTO queries (query, count, last_searched_ts)
                VALUES (?, ?, ?)
                ON CONFLICT(query) DO UPDATE SET
                    count            = count + excluded.count,
                    last_searched_ts = excluded.last_searched_ts
                """,
                rows,
            )
            self._conn.commit()
        _bump("rows_written", len(rows))
        _bump("write_transactions", 1)
        return len(rows)

    def bulk_insert(self, items: Iterable[Tuple[str, int]], chunk: int = 5000) -> int:
        """Fast initial load (used by ingest.py). Replaces counts rather than incrementing."""
        ts = time.time()
        total = 0
        batch: List[Tuple[str, int, float]] = []
        with self._lock:
            for query, count in items:
                batch.append((query, int(count), ts))
                if len(batch) >= chunk:
                    self._conn.executemany(
                        "INSERT OR REPLACE INTO queries (query, count, last_searched_ts) "
                        "VALUES (?, ?, ?)",
                        batch,
                    )
                    total += len(batch)
                    batch.clear()
            if batch:
                self._conn.executemany(
                    "INSERT OR REPLACE INTO queries (query, count, last_searched_ts) "
                    "VALUES (?, ?, ?)",
                    batch,
                )
                total += len(batch)
            self._conn.commit()
        return total

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# Module-level singleton used by the app and scripts.
store = SqliteStore(settings.db_path)
