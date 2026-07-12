"""Tests for LRU-K embedding cache telemetry (PERF-6)."""
from __future__ import annotations

import sys
from collections import deque
from pathlib import Path

import numpy as np
import pytest

NEXUS_ROOT = Path("/home/roshan/nexus")
sys.path.insert(0, str(NEXUS_ROOT))
sys.path.insert(0, str(NEXUS_ROOT / "server"))

from router_v2.cache_telemetry import CacheTelemetry, LRUKCache, get_cache_stats
from router_v2 import cache_telemetry as _cache_telemetry_module


@pytest.fixture(autouse=True)
def _clear_cache_registry():
    """Start each test with an empty LRU-K cache registry."""
    _cache_telemetry_module._cache_registry.clear()
    yield


class _ControlledClock:
    """Manual clock for deterministic LRU-K eviction tests."""

    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def tick(self, delta: float = 1.0) -> None:
        self.t += delta


def test_cache_telemetry_counters() -> None:
    tel = CacheTelemetry()
    assert tel.hit_rate == 0.0
    tel.record_hit()
    tel.record_miss()
    tel.record_hit()
    assert tel.hits == 2
    assert tel.misses == 1
    assert tel.hit_rate == pytest.approx(2 / 3)


def test_lru_k_basic_lookup_and_hit_rate() -> None:
    clock = _ControlledClock()
    cache = LRUKCache(max_size=4, k=2)
    cache._clock = clock

    vec = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    cache.put("a", vec)

    hit, got = cache._cache_lookup("a")
    assert hit
    assert np.array_equal(got, vec)
    assert cache._telemetry.hit_rate == 1.0

    miss, got = cache._cache_lookup("missing")
    assert not miss
    assert got is None
    assert cache._telemetry.hit_rate == 0.5


def test_lru_k_eviction() -> None:
    """LRU-K should evict the key whose K-th most recent access is oldest."""
    clock = _ControlledClock()
    cache = LRUKCache(max_size=3, k=2)
    cache._clock = clock

    def put(key: str) -> None:
        cache.put(key, np.array([ord(key)], dtype=np.float32))
        clock.tick()

    def lookup(key: str) -> bool:
        hit, _ = cache._cache_lookup(key)
        clock.tick()
        return hit

    # Seed three keys; each has one access so far.
    put("a")
    put("b")
    put("c")

    # Promote a and b to second accesses so they become "established".
    assert lookup("a")
    assert lookup("b")

    # c still has only one access, so it has the lowest LRU-K score and
    # should be evicted when d is inserted.
    put("d")

    assert "c" not in cache._data
    assert {"a", "b", "d"} == set(cache._data.keys())

    # c is now a miss; a/b/d remain hits.
    assert not lookup("c")
    assert lookup("a")
    assert lookup("b")
    assert lookup("d")


def test_lru_k_eviction_respects_k_history() -> None:
    """Established keys with older K-th access are evicted before newer ones."""
    clock = _ControlledClock()
    cache = LRUKCache(max_size=3, k=2)
    cache._clock = clock

    # Seed established histories directly so we can isolate the eviction
    # scoring without the churn of establishing a brand-new key in a full cache.
    cache._data = {
        "a": np.array([1.0], dtype=np.float32),
        "b": np.array([2.0], dtype=np.float32),
        "c": np.array([3.0], dtype=np.float32),
    }
    cache._history = {
        "a": deque([0.0, 4.0], maxlen=2),  # 2nd-most-recent = 0
        "b": deque([1.0, 5.0], maxlen=2),  # 2nd-most-recent = 1
        "c": deque([2.0, 6.0], maxlen=2),  # 2nd-most-recent = 2
    }

    # Insert a fourth established key and trigger eviction.
    cache._data["d"] = np.array([4.0], dtype=np.float32)
    cache._history["d"] = deque([3.0, 7.0], maxlen=2)
    cache._evict_lru_k()

    # a has the oldest 2nd-most-recent access, so it is evicted.
    assert "a" not in cache._data
    assert {"b", "c", "d"} == set(cache._data.keys())


def test_hit_rate_exposed() -> None:
    """get_cache_stats() and per-cache get_stats() expose hit_rate."""
    cache = LRUKCache(max_size=8, k=2)
    vec = np.array([1.0, 2.0], dtype=np.float32)

    cache.put("x", vec)
    hit, _ = cache._cache_lookup("x")
    assert hit
    cache._cache_lookup("y")  # miss

    stats = cache.get_stats()
    assert "hits" in stats
    assert "misses" in stats
    assert "hit_rate" in stats
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["hit_rate"] == pytest.approx(0.5)

    global_stats = get_cache_stats()
    assert "cache_hit_rate" in global_stats or "hit_rate" in global_stats
    assert global_stats["hit_rate"] == pytest.approx(0.5)


def test_get_cache_stats_aggregates_multiple_caches() -> None:
    # Clear the weak registry by deleting existing references.
    caches = [LRUKCache(max_size=4, k=2) for _ in range(2)]
    caches[0].put("a", np.array([1.0], dtype=np.float32))
    caches[0]._cache_lookup("a")
    caches[1]._cache_lookup("b")  # miss

    stats = get_cache_stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["hit_rate"] == pytest.approx(0.5)
    assert stats["caches"] == 2
    del caches
