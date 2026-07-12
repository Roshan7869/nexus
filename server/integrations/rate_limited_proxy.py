"""rate_limited_proxy.py — async token-bucket + daily quota tracking.

Generic wrapper for any rate-limited MCP integration. Cashfree is used as the
named example with production defaults: 10 req/s, burst 20, daily quota from
``CASHFREE_DAILY_QUOTA`` (default 10_000).
"""
from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Optional, Protocol, Union


class RateLimitError(Exception):
    """Raised when the token bucket cannot grant tokens in a bounded window."""


class QuotaExceeded(Exception):
    """Raised when the daily quota has been exhausted."""


class QuotaStore(Protocol):
    """Minimal store contract for ``QuotaTracker``.

    Implemented by ``MemoryStore`` and satisfied by ``redis.asyncio.Redis``.
    """

    async def incr(self, key: str, amount: int = 1) -> int: ...
    async def get(self, key: str) -> Union[int, bytes, None]: ...
    async def expire(self, key: str, seconds: int) -> Any: ...


class MemoryStore:
    """In-memory quota store for tests and single-process deployments."""

    def __init__(self) -> None:
        self._data: Dict[str, int] = {}

    async def incr(self, key: str, amount: int = 1) -> int:
        self._data[key] = self._data.get(key, 0) + amount
        return self._data[key]

    async def get(self, key: str) -> int:
        return self._data.get(key, 0)

    async def expire(self, key: str, seconds: int) -> None:
        pass


class TokenBucket:
    """Async token bucket with configurable fill rate and burst capacity.

    Usage as context manager::

        bucket = TokenBucket(rate=10.0, burst=20)
        async with bucket:
            await call_cashfree(...)

    Or explicit acquire::

        await bucket.acquire()
        await call_cashfree(...)
    """

    def __init__(self, rate: float, burst: int) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        if burst < 1:
            raise ValueError("burst must be >= 1")
        self.rate = float(rate)
        self.burst = int(burst)
        self._tokens: float = float(burst)
        self._last_update = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: int = 1, timeout: Optional[float] = None) -> bool:
        """Acquire *tokens* from the bucket, waiting if necessary.

        Args:
            tokens: number of tokens to consume (default 1).
            timeout: max seconds to wait; ``None`` waits as long as needed.

        Returns:
            ``True`` if tokens were acquired.

        Raises:
            RateLimitError: if *timeout* is set and expires before tokens are available.
        """
        if tokens < 1:
            raise ValueError("tokens must be >= 1")

        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_update
            self._tokens = min(self.burst, self._tokens + elapsed * self.rate)
            self._last_update = now

            if self._tokens >= tokens:
                self._tokens -= tokens
                return True

            deficit = tokens - self._tokens
            wait_time = deficit / self.rate
            if timeout is not None and wait_time > timeout:
                raise RateLimitError(
                    f"Rate limit: need {wait_time:.3f}s, timeout {timeout:.3f}s"
                )

            await asyncio.sleep(wait_time)
            self._tokens = 0.0
            self._last_update = time.monotonic()
            return True

    async def __aenter__(self) -> "TokenBucket":
        await self.acquire()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class QuotaTracker:
    """Track daily usage against a hard limit using a pluggable store.

    The store may be ``MemoryStore`` or a ``redis.asyncio.Redis`` client. When
    Redis is used, the counter key is set to expire at ``ttl_seconds`` so it
    resets automatically each day.
    """

    def __init__(
        self,
        key: str,
        store: QuotaStore,
        daily_limit: int,
        ttl_seconds: int = 86400,
    ) -> None:
        if daily_limit < 1:
            raise ValueError("daily_limit must be >= 1")
        self.key = key
        self.store = store
        self.daily_limit = int(daily_limit)
        self.ttl_seconds = int(ttl_seconds)

    async def consume(self, tokens: int = 1) -> bool:
        """Attempt to consume *tokens* from the daily quota.

        Returns:
            ``True`` if quota remains, ``False`` if the request would exceed it.
        """
        if tokens < 1:
            raise ValueError("tokens must be >= 1")

        used = await self.store.get(self.key) or 0
        used = self._coerce_int(used)
        if used + tokens > self.daily_limit:
            return False

        new_used = await self.store.incr(self.key, tokens)
        new_used = self._coerce_int(new_used)

        # Set TTL on first write so the key auto-resets daily.
        if new_used <= tokens:
            await self.store.expire(self.key, self.ttl_seconds)

        return new_used <= self.daily_limit

    async def status(self) -> Dict[str, Any]:
        """Return current quota usage."""
        used = await self.store.get(self.key) or 0
        used = self._coerce_int(used)
        remaining = max(0, self.daily_limit - used)
        return {
            "key": self.key,
            "daily_limit": self.daily_limit,
            "used": used,
            "remaining": remaining,
            "reset_seconds": self.ttl_seconds,
            "reset_at": self._reset_iso(),
        }

    @staticmethod
    def _coerce_int(value: Union[int, bytes, None]) -> int:
        if value is None:
            return 0
        if isinstance(value, bytes):
            return int(value.decode())
        return int(value)

    def _reset_iso(self) -> str:
        return (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )


async def call_rate_limited(
    tool_name: str,
    arguments: dict,
    bucket: TokenBucket,
    quota: QuotaTracker,
    caller: Callable[[str, dict], Awaitable[Any]],
) -> Any:
    """Call ``caller(tool_name, arguments)`` under rate + quota controls.

    Args:
        tool_name: MCP tool name.
        arguments: JSON-serializable arguments dict.
        bucket: ``TokenBucket`` enforcing req/s rate limits.
        quota: ``QuotaTracker`` enforcing daily quota.
        caller: async callable invoked as ``await caller(tool_name, arguments)``.

    Returns:
        Whatever *caller* returns.

    Raises:
        QuotaExceeded: if the daily quota is exhausted.
    """
    if not await quota.consume():
        raise QuotaExceeded(
            f"Daily quota exceeded for {quota.key}: limit={quota.daily_limit}"
        )

    async with bucket:
        return await caller(tool_name, arguments)


async def get_quota_status(quota: QuotaTracker) -> Dict[str, Any]:
    """Return the current quota status for *quota*."""
    return await quota.status()


# ── Cashfree defaults ───────────────────────────────────────────────────────

CASHFREE_RATE = 10.0
CASHFREE_BURST = 20
CASHFREE_DAILY_QUOTA_DEFAULT = 10_000


def cashfree_bucket(rate: Optional[float] = None, burst: Optional[int] = None) -> TokenBucket:
    """Return a ``TokenBucket`` with Cashfree production defaults."""
    return TokenBucket(
        rate=rate if rate is not None else CASHFREE_RATE,
        burst=burst if burst is not None else CASHFREE_BURST,
    )


def cashfree_quota(
    store: Optional[QuotaStore] = None,
    daily_limit: Optional[int] = None,
) -> QuotaTracker:
    """Return a ``QuotaTracker`` for Cashfree daily usage.

    The daily limit is read from ``CASHFREE_DAILY_QUOTA`` if *daily_limit* is
    not supplied.
    """
    if daily_limit is None:
        daily_limit = int(os.getenv("CASHFREE_DAILY_QUOTA", CASHFREE_DAILY_QUOTA_DEFAULT))
    return QuotaTracker(
        key="cashfree:daily",
        store=store if store is not None else MemoryStore(),
        daily_limit=daily_limit,
        ttl_seconds=86400,
    )
