"""Consistent-hash ring used to pick which cache node owns a given prefix key.

WHY CONSISTENT HASHING (not `hash(key) % N`):
  With plain modulo, adding or removing one cache node changes N and therefore remaps almost
  EVERY key -> a cache stampede. Consistent hashing places both nodes and keys on one hash circle
  and assigns each key to the next node clockwise, so adding/removing a node only moves the keys
  in that node's arc (~1/N of keys). Virtual nodes (many points per physical node) smooth out the
  distribution so no single node gets an unfairly large arc.
"""
from __future__ import annotations

import bisect
import hashlib
from typing import Dict, List, Optional


def _hash(key: str) -> int:
    return int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16)


class ConsistentHashRing:
    def __init__(self, nodes: List[str], vnodes: int = 150) -> None:
        self.vnodes = vnodes
        self._ring: Dict[int, str] = {}   # point on the circle -> node name
        self._points: List[int] = []      # sorted circle points for binary search
        self.nodes: List[str] = []
        for node in nodes:
            self.add_node(node)

    def add_node(self, node: str) -> None:
        if node in self.nodes:
            return
        self.nodes.append(node)
        for i in range(self.vnodes):
            point = _hash(f"{node}#{i}")
            self._ring[point] = node
            bisect.insort(self._points, point)

    def remove_node(self, node: str) -> None:
        if node not in self.nodes:
            return
        self.nodes.remove(node)
        for i in range(self.vnodes):
            point = _hash(f"{node}#{i}")
            self._ring.pop(point, None)
            idx = bisect.bisect_left(self._points, point)
            if idx < len(self._points) and self._points[idx] == point:
                self._points.pop(idx)

    def get_node(self, key: str) -> Optional[str]:
        """Return the node that owns `key`: the first ring point clockwise from hash(key)."""
        if not self._points:
            return None
        h = _hash(key)
        idx = bisect.bisect(self._points, h)
        if idx == len(self._points):  # wrapped past the largest point -> back to the start
            idx = 0
        return self._ring[self._points[idx]]

    def distribution(self, sample_keys: List[str]) -> Dict[str, int]:
        """Helper for logs/demo: how many of `sample_keys` land on each node."""
        counts = {n: 0 for n in self.nodes}
        for k in sample_keys:
            node = self.get_node(k)
            if node is not None:
                counts[node] += 1
        return counts
