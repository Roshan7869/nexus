"""Unified context fusion — RRF-weighted blending of results from N backends.

Phase 5 target: 7 backends with per-backend weights and TTL.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from server.context_harness.models import HarnessResult

RRF_K = 60.0


@dataclass
class BackendDef:
    """Definition of a single RRF backend."""

    name: str
    weight: float
    fetch: Callable[[str, int], list[HarnessResult]]
    cache_ttl: float = 300.0  # seconds


@dataclass
class CacheEntry:
    result: list[HarnessResult]
    cached_at: float


class RRFFuser:
    """RRF fusion engine supporting N backends with per-backend weights and caching."""

    def __init__(self) -> None:
        self._backends: dict[str, BackendDef] = {}
        self._cache: dict[str, CacheEntry] = {}

    def register(self, backend: BackendDef) -> None:
        self._backends[backend.name] = backend

    def unregister(self, name: str) -> None:
        self._backends.pop(name, None)

    @property
    def backend_names(self) -> list[str]:
        return list(self._backends.keys())

    def _cache_key(self, backend: str, query: str) -> str:
        raw = f"{backend}:{query}".encode()
        return hashlib.sha256(raw).hexdigest()

    def fuse(
        self,
        query: str,
        max_results: int = 10,
        backends: list[str] | None = None,
    ) -> list[HarnessResult]:
        """Run selected backends and fuse via RRF.

        Args:
            query: search query
            max_results: max fused results
            backends: subset of backends to query (None = all)

        Returns:
            fused, deduplicated results sorted by RRF score descending
        """
        names = backends if backends is not None else list(self._backends.keys())
        all_results: dict[str, HarnessResult] = {}
        result_sources: dict[str, set[str]] = {}

        for name in names:
            bd = self._backends.get(name)
            if bd is None:
                continue

            # Cache check
            ck = self._cache_key(name, query)
            cached = self._cache.get(ck)
            if cached and (time.time() - cached.cached_at) < bd.cache_ttl:
                results = cached.result
            else:
                try:
                    results = bd.fetch(query, max_results)
                except Exception:
                    logger = __import__("logging").getLogger(__name__)
                    logger.exception("Backend %s failed", name)
                    results = []
                self._cache[ck] = CacheEntry(results, time.time())

            for rank, r in enumerate(results):
                rid = f"{name}::{hashlib.sha256(r.content.encode()).hexdigest()}"
                score = bd.weight * r.score * (1.0 / (RRF_K + rank))

                if rid in all_results:
                    existing = all_results[rid]
                    score += existing.score
                    sources = result_sources.setdefault(rid, set())
                    sources.add(name)
                    all_results[rid] = HarnessResult(
                        source=",".join(sorted(sources | {name})),
                        content=existing.content,
                        score=score,
                        metadata={**existing.metadata, **r.metadata},
                    )
                else:
                    result_sources[rid] = {name}
                    all_results[rid] = HarnessResult(
                        source=name,
                        content=r.content,
                        score=score,
                        metadata=r.metadata,
                    )

        sorted_results = sorted(all_results.values(), key=lambda x: -x.score)
        return sorted_results[:max_results]

    def clear_cache(self, backend: str | None = None) -> None:
        if backend:
            self._cache = {k: v for k, v in self._cache.items() if not k.startswith(f"{backend}:")}
        else:
            self._cache.clear()


# ── Legacy shim ────────────────────────────────────────────────────────────

_fuser: RRFFuser | None = None


def get_fuser() -> RRFFuser:
    global _fuser
    if _fuser is None:
        _fuser = RRFFuser()
    return _fuser


def fuse(
    nexus_results: list[HarnessResult],
    internet_results: list[HarnessResult],
    memory_results: list[HarnessResult],
    max_results: int = 10,
) -> list[HarnessResult]:
    """Legacy 3-backend fuse (backward compatible)."""
    fuser = get_fuser()

    def _nexus_fetch(q: str, k: int) -> list[HarnessResult]:
        return [r for r in nexus_results if q.lower() in r.content.lower()][:k]

    def _internet_fetch(q: str, k: int) -> list[HarnessResult]:
        return [r for r in internet_results if q.lower() in r.content.lower()][:k]

    def _memory_fetch(q: str, k: int) -> list[HarnessResult]:
        return [r for r in memory_results if q.lower() in r.content.lower()][:k]

    fuser.register(BackendDef("nexus", 1.0, _nexus_fetch))
    fuser.register(BackendDef("internet", 1.0, _internet_fetch))
    fuser.register(BackendDef("memory", 1.0, _memory_fetch))

    fuser.clear_cache()
    return fuser.fuse("", max_results, backends=["nexus", "internet", "memory"])


def rank_by_relevance(
    results: list[dict], query: str, score_key: str = "score"
) -> list[dict]:
    query_words = set(query.lower().split())

    def _relevance(r: dict) -> float:
        base = r.get(score_key, 0.0)
        content = (
            r.get("content", "")
            or r.get("description", "")
            or r.get("note", "")
            or ""
        )
        overlap = len(query_words & set(content.lower().split()))
        return base + (overlap * 0.1)

    return sorted(results, key=_relevance, reverse=True)
