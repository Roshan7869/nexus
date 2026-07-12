"""Pluggable backend protocol for context-harness RRF fusion system.

BackendProtocol defines the contract, BackendRegistry manages
discovery, and fuse_registered() runs all registered backends
in parallel with timeout, dedup, weight, and error isolation.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Protocol, runtime_checkable

from server.context_harness.models import HarnessResult

RRF_K = 60.0


@runtime_checkable
class BackendProtocol(Protocol):
    """Contract every context backend must satisfy."""

    name: str
    weight: float
    cache_ttl: float
    timeout_seconds: float

    async def search(self, query: str, max_results: int = 10) -> list[HarnessResult]:
        ...

    async def is_available(self) -> bool:
        ...


class BackendRegistry:
    """Pluggable registry for context backends."""

    def __init__(self) -> None:
        self._backends: dict[str, BackendProtocol] = {}

    def register(self, backend: BackendProtocol) -> None:
        if backend.name in self._backends:
            raise KeyError(f"Backend '{backend.name}' is already registered")
        self._backends[backend.name] = backend

    def get(self, name: str) -> BackendProtocol | None:
        return self._backends.get(name)

    def all(self) -> list[BackendProtocol]:
        return list(self._backends.values())

    async def available(self) -> list[BackendProtocol]:
        results = await asyncio.gather(
            *(b.is_available() for b in self._backends.values()),
            return_exceptions=True,
        )
        return [
            b for b, ok in zip(self._backends.values(), results)
            if ok is True
        ]

    def __len__(self) -> int:
        return len(self._backends)


def _content_hash(result: HarnessResult) -> str:
    return hashlib.sha256(result.content.encode("utf-8")).hexdigest()


async def fuse_registered(
    query: str,
    backends: list[BackendProtocol],
    max_results: int = 10,
) -> list[HarnessResult]:
    """Run all backends in parallel, fuse via RRF.

    Each backend call is:
      - timed out via ``asyncio.wait_for`` (per-backend timeout_seconds)
      - error-isolated (exception → empty list, never propagates)
      - weight-multiplied (result scores multiplied by backend.weight)
      - content-deduplicated (SHA256 of result.content)

    Duplicates from different backends have their scores summed
    (multi-source fusion) and sources concatenated.
    """
    started = time.monotonic()

    async def _safe_search(backend: BackendProtocol) -> list[HarnessResult]:
        try:
            results = await asyncio.wait_for(
                backend.search(query, max_results),
                timeout=backend.timeout_seconds,
            )
            elapsed = time.monotonic() - started
            for r in results:
                r.score *= backend.weight
                r.metadata.setdefault("_backend_latency_ms", round(elapsed * 1000, 1))
            return results
        except (asyncio.TimeoutError, Exception):
            return []

    backend_results: list[list[HarnessResult]] = await asyncio.gather(
        *(_safe_search(b) for b in backends),
        return_exceptions=False,
    )

    seen: dict[str, HarnessResult] = {}
    for backend, results in zip(backends, backend_results):
        for rank, r in enumerate(results):
            h = _content_hash(r)
            rrf_score = r.score * (1.0 / (RRF_K + rank))
            if h in seen:
                existing = seen[h]
                combined = HarnessResult(
                    source=f"{existing.source},{backend.name}",
                    content=existing.content,
                    score=existing.score + rrf_score,
                    metadata={**existing.metadata, **r.metadata},
                )
                seen[h] = combined
            else:
                seen[h] = HarnessResult(
                    source=backend.name,
                    content=r.content,
                    score=rrf_score,
                    metadata=r.metadata,
                )

    sorted_results = sorted(seen.values(), key=lambda x: -x.score)
    return sorted_results[:max_results]


def fuse(
    nexus_results: list[HarnessResult],
    internet_results: list[HarnessResult],
    memory_results: list[HarnessResult],
    max_results: int = 10,
) -> list[HarnessResult]:
    """Backward-compatible wrapper.

    Equivalent to fusing three hardcoded backends with equal weight.
    New code should prefer ``fuse_registered()`` with a ``BackendRegistry``.
    """
    seen: dict[str, HarnessResult] = {}
    for source_list, source_name in [
        (nexus_results, "nexus"),
        (internet_results, "internet"),
        (memory_results, "memory"),
    ]:
        for rank, r in enumerate(source_list):
            h = _content_hash(r)
            rrf_score = r.score * (1.0 / (RRF_K + rank))
            if h in seen:
                existing = seen[h]
                combined = HarnessResult(
                    source=f"{existing.source},{source_name}",
                    content=existing.content,
                    score=existing.score + rrf_score,
                    metadata={**existing.metadata, **r.metadata},
                )
                seen[h] = combined
            else:
                seen[h] = HarnessResult(
                    source=source_name,
                    content=r.content,
                    score=rrf_score,
                    metadata=r.metadata,
                )

    sorted_results = sorted(seen.values(), key=lambda x: -x.score)
    return sorted_results[:max_results]
