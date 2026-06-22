"""Distributed suggestion cache over N Redis nodes, routed by the consistent-hash ring.

Read path:  prefix --ring--> node --> Redis GET (hit => return; miss => caller computes + set).
Write path: SETEX with a TTL so entries expire (eviction) and stale data cannot live forever.
Invalidation: when rankings change (a batch flush or trending update), the affected prefix keys
are deleted so the next read recomputes -- this is the explicit invalidation the assignment asks
for, on top of the TTL safety net.

The cache degrades gracefully: if a Redis node is unreachable, its lookups are treated as misses
and its writes are skipped (the system still works, just colder). We never reroute a key to a
different node, because that would defeat the whole point of consistent hashing.
"""
from __future__ import annotations

import json
from typing import Dict, Iterable, List, Optional, Set, Tuple

from backend import metrics
from backend.cache.ring import ConsistentHashRing

# Cap how deep we invalidate prefixes for a changed query. Short prefixes are the ones actually
# cached/hot; deeper ones are rare and the TTL cleans them up. Keeps invalidation work bounded.
INVALIDATE_MAX_PREFIX = 8
MODES = ("basic", "trending")


class DistributedCache:
    def __init__(self, settings) -> None:
        self.ttl = settings.cache_ttl_seconds
        # name -> (host, port)
        self._endpoints: Dict[str, Tuple[str, int]] = {
            name: (host, port) for name, host, port in settings.cache_nodes
        }
        self.ring = ConsistentHashRing(list(self._endpoints.keys()), vnodes=settings.ring_vnodes)
        self._clients: Dict[str, Optional["object"]] = {n: None for n in self._endpoints}

    # ---- lifecycle -------------------------------------------------------------
    def connect(self) -> None:
        """Create one Redis client per node and ping it. Unreachable nodes stay None (degraded)."""
        try:
            import redis  # imported here so the rest of the app runs even without redis installed
        except ImportError:
            print("[cache] redis package not installed -> cache disabled")
            return
        for name, (host, port) in self._endpoints.items():
            try:
                client = redis.Redis(
                    host=host,
                    port=port,
                    db=0,
                    decode_responses=True,
                    socket_connect_timeout=0.5,
                    socket_timeout=0.5,
                )
                client.ping()
                self._clients[name] = client
                print(f"[cache] connected node {name} at {host}:{port}")
            except Exception as exc:  # noqa: BLE001 - degrade gracefully
                self._clients[name] = None
                print(f"[cache] node {name} ({host}:{port}) unavailable: {exc}")

    def close(self) -> None:
        for client in self._clients.values():
            try:
                if client is not None:
                    client.close()
            except Exception:  # noqa: BLE001
                pass

    # ---- routing ---------------------------------------------------------------
    def _route(self, prefix: str) -> Tuple[Optional[str], Optional["object"]]:
        node = self.ring.get_node(prefix)  # consistent hashing decides the owner
        return node, (self._clients.get(node) if node else None)

    @staticmethod
    def _key(prefix: str, mode: str) -> str:
        return f"suggest:{mode}:{prefix}"

    # ---- read / write ----------------------------------------------------------
    def get(self, prefix: str, mode: str) -> Optional[List[Dict]]:
        _node, client = self._route(prefix)
        if client is None:
            metrics.note_cache(hit=False)
            return None
        try:
            raw = client.get(self._key(prefix, mode))
        except Exception:  # noqa: BLE001
            metrics.note_cache(hit=False)
            return None
        if raw is None:
            metrics.note_cache(hit=False)
            return None
        metrics.note_cache(hit=True)
        return json.loads(raw)

    def set(self, prefix: str, mode: str, suggestions: List[Dict]) -> None:
        _node, client = self._route(prefix)
        if client is None:
            return
        try:
            client.setex(self._key(prefix, mode), self.ttl, json.dumps(suggestions))
        except Exception:  # noqa: BLE001
            pass

    # ---- invalidation ----------------------------------------------------------
    @staticmethod
    def prefixes_for_query(query: str) -> Set[str]:
        upto = min(len(query), INVALIDATE_MAX_PREFIX)
        return {query[:i] for i in range(1, upto + 1)}

    def invalidate_prefixes(self, prefixes: Iterable[str]) -> int:
        deleted = 0
        for prefix in prefixes:
            _node, client = self._route(prefix)
            if client is None:
                continue
            try:
                deleted += client.delete(*[self._key(prefix, m) for m in MODES])
            except Exception:  # noqa: BLE001
                pass
        return deleted

    def invalidate_for_queries(self, queries: Iterable[str]) -> int:
        affected: Set[str] = set()
        for q in queries:
            affected |= self.prefixes_for_query(q)
        return self.invalidate_prefixes(affected)

    # ---- debug -----------------------------------------------------------------
    def debug(self, prefix: str, mode: str = "basic") -> Dict:
        node, client = self._route(prefix)
        host, port = self._endpoints.get(node, (None, None))
        info: Dict = {
            "prefix": prefix,
            "mode": mode,
            "key": self._key(prefix, mode),
            "owner_node": node,
            "endpoint": f"{host}:{port}" if host else None,
            "node_available": client is not None,
        }
        if client is None:
            info["status"] = "unavailable"
            return info
        try:
            raw = client.get(self._key(prefix, mode))
            ttl = client.ttl(self._key(prefix, mode))
            info["hit"] = raw is not None
            info["ttl_seconds"] = ttl if ttl is not None else None
            info["value"] = json.loads(raw) if raw else None
        except Exception as exc:  # noqa: BLE001
            info["status"] = f"error: {exc}"
        return info

    def stats(self) -> Dict:
        return {
            "nodes": list(self._endpoints.keys()),
            "available": [n for n, c in self._clients.items() if c is not None],
            "vnodes_per_node": self.ring.vnodes,
        }
