"""Observable LRU-K embedding cache with hit/miss telemetry.

Provides an in-memory LRU-K cache layer that sits in front of the persistent
SQLite ``chunk_embed_cache``.  Tracks hits, misses, and rolling hit rate so the
embedding path is observable and tail latency is reduced for hot hashes.
"""
from __future__ import annotations

import threading
import time
import weakref
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class CacheTelemetry:
    """Counters and rolling hit rate for an embedding cache."""

    hits: int = 0
    misses: int = 0
    rolling_window: deque[float] = field(default_factory=lambda: deque(maxlen=100))

    def record_hit(self) -> None:
        self.hits += 1
        self.rolling_window.append(1.0)

    def record_miss(self) -> None:
        self.misses += 1
        self.rolling_window.append(0.0)

    @property
    def total(self) -> int:
        return self.hits + self.misses

    @property
    def hit_rate(self) -> float:
        return self.hits / self.total if self.total > 0 else 0.0

    @property
    def rolling_hit_rate(self) -> float:
        if not self.rolling_window:
            return 0.0
        return sum(self.rolling_window) / len(self.rolling_window)


class LRUKCache:
    """In-memory LRU-K cache for embedding vectors.

    Keeps up to ``max_size`` entries.  Each key stores the last ``k`` access
    timestamps.  Eviction picks unestablished keys (fewer than ``k``
    references) first, then the established key whose ``k``-th most recent
    access is oldest.
    """

    def __init__(self, max_size: int = 2048, k: int = 2) -> None:
        if max_size <= 0:
            raise ValueError("max_size must be positive")
        if k <= 0:
            raise ValueError("k must be positive")
        self.max_size = max_size
        self.k = k
        self._data: dict[str, np.ndarray] = {}
        self._history: dict[str, deque[float]] = {}
        self._telemetry = CacheTelemetry()
        self._lock = threading.Lock()
        self._clock = time.monotonic
        _cache_registry.add(self)

    # ---- internal eviction scoring --------------------------------------

    def _score(self, key: str) -> tuple[int, float]:
        """Return LRU-K eviction score.

        Unestablished keys (fewer than ``k`` references) are evicted before
        established ones.  Among established keys, the key whose ``k``-th most
        recent access is oldest is evicted first.
        """
        hist = self._history[key]
        if len(hist) >= self.k:
            return (1, hist[-self.k])
        return (0, hist[0])

    def _evict_lru_k(self) -> None:
        """Evict the lowest-scoring entry until we are at or below max_size."""
        while len(self._data) > self.max_size:
            victim = min(self._data.keys(), key=lambda k: self._score(k))
            del self._data[victim]
            del self._history[victim]

    # ---- public API ------------------------------------------------------

    def _cache_lookup(self, key: str) -> tuple[bool, np.ndarray | None]:
        """Lookup ``key``; update telemetry and access history.

        Returns (hit, value).  The leading underscore signals that this is the
        integration hook used by ``ChunkEmbedder``; it is safe to call publicly
        for telemetry-aware lookups.
        """
        now = self._clock()
        with self._lock:
            if key in self._data:
                self._history[key].append(now)
                self._telemetry.record_hit()
                return True, self._data[key].copy()
            self._telemetry.record_miss()
            return False, None

    def put(self, key: str, value: np.ndarray) -> None:
        """Store ``value`` under ``key`` and record an access."""
        now = self._clock()
        with self._lock:
            self._data[key] = np.asarray(value, dtype=np.float32).copy()
            if key not in self._history:
                self._history[key] = deque(maxlen=self.k)
            self._history[key].append(now)
            self._evict_lru_k()

    def get_stats(self) -> dict[str, Any]:
        """Return telemetry and sizing for this cache instance."""
        return {
            "hits": self._telemetry.hits,
            "misses": self._telemetry.misses,
            "hit_rate": self._telemetry.hit_rate,
            "rolling_hit_rate": self._telemetry.rolling_hit_rate,
            "size": len(self._data),
            "max_size": self.max_size,
            "k": self.k,
        }


# Weak registry so ``get_cache_stats()`` can aggregate live caches without
# preventing garbage collection.
_cache_registry: weakref.WeakSet[LRUKCache] = weakref.WeakSet()


def get_cache_stats() -> dict[str, Any]:
    """Aggregate telemetry across all live LRU-K caches.

    Falls back to zeroed stats when no cache is currently instantiated.
    """
    aggregated = CacheTelemetry()
    caches = list(_cache_registry)
    for cache in caches:
        aggregated.hits += cache._telemetry.hits
        aggregated.misses += cache._telemetry.misses
    return {
        "hits": aggregated.hits,
        "misses": aggregated.misses,
        "hit_rate": aggregated.hit_rate,
        "rolling_hit_rate": aggregated.rolling_hit_rate,
        "caches": len(caches),
    }
