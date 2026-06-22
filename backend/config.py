"""Central configuration.

All tunables live here and are overridable via environment variables (.env). Keeping a single
typed config object means every module reads the *same* values and the viva story ("where does
the TTL / batch size come from?") has one clear answer.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Tuple

from dotenv import load_dotenv

# Load .env if present. Real env vars always win over the file.
load_dotenv()


def _get(name: str, default: str) -> str:
    return os.getenv(name, default)


def _parse_nodes(raw: str) -> List[Tuple[str, str, int]]:
    """Parse "c0|localhost|6379,c1|localhost|6380" -> [("c0","localhost",6379), ...]."""
    nodes: List[Tuple[str, str, int]] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        name, host, port = entry.split("|")
        nodes.append((name.strip(), host.strip(), int(port)))
    return nodes


@dataclass(frozen=True)
class Settings:
    # ---- primary store ----
    db_path: str = _get("DB_PATH", "data/typeahead.db")

    # ---- suggestions ----
    suggest_limit: int = int(_get("SUGGEST_LIMIT", "10"))

    # ---- distributed cache ----
    cache_nodes: List[Tuple[str, str, int]] = field(
        default_factory=lambda: _parse_nodes(
            _get("CACHE_NODES", "c0|localhost|6379,c1|localhost|6380,c2|localhost|6381")
        )
    )
    cache_ttl_seconds: int = int(_get("CACHE_TTL_SECONDS", "30"))
    ring_vnodes: int = int(_get("RING_VNODES", "150"))

    # ---- batch writes ----
    batch_max_size: int = int(_get("BATCH_MAX_SIZE", "500"))
    batch_interval_seconds: float = float(_get("BATCH_INTERVAL_SECONDS", "2"))

    # ---- trending / recency-aware ranking ----
    trending_window_seconds: int = int(_get("TRENDING_WINDOW_SECONDS", "600"))
    trending_bucket_seconds: int = int(_get("TRENDING_BUCKET_SECONDS", "60"))
    trending_halflife_seconds: float = float(_get("TRENDING_HALFLIFE_SECONDS", "300"))
    recency_weight: float = float(_get("RECENCY_WEIGHT", "8.0"))


# Single shared instance imported everywhere.
settings = Settings()
