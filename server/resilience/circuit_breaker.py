"""circuit_breaker.py — Reusable circuit breaker + bulkhead module for NEXUS.

Isolates external MCP tool failures (ruflo, cashfree, etc.) so they cannot
cascade across the router. Provides per-tool circuit breakers and per-category
asyncio semaphore bulkheads with optional Redis-backed state.
"""
from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import threading
import time
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, Optional, TypeVar

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

# Optional Redis import; module works in-memory if Redis is unavailable.
try:  # pragma: no cover - best-effort import
    from redis.asyncio import Redis as AsyncRedis

    _REDIS_AVAILABLE = True
except Exception:  # pragma: no cover - fallback path
    AsyncRedis = None  # type: ignore
    _REDIS_AVAILABLE = False


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreakerOpen(Exception):
    """Raised when a call is rejected because the circuit is OPEN."""

    def __init__(self, name: str, state: CircuitState, until: Optional[float] = None):
        self.name = name
        self.state = state
        self.until = until
        msg = f"Circuit breaker '{name}' is {state.value}"
        if until:
            msg += f" (retry after {until:.3f})"
        super().__init__(msg)


class BulkheadFull(Exception):
    """Raised when a bulkhead semaphore cannot be acquired in time."""

    def __init__(self, category: str, limit: int):
        self.category = category
        self.limit = limit
        super().__init__(f"Bulkhead '{category}' full (limit={limit})")


class _RedisStateBackend:
    """Best-effort Redis state backend. Falls back to in-memory on any error."""

    def __init__(self, redis: Optional[Any], key_prefix: str = "nexus:circuit"):
        self._redis = redis
        self._key_prefix = key_prefix

    def _key(self, name: str) -> str:
        return f"{self._key_prefix}:{name}"

    async def load(self, name: str) -> Optional[Dict[str, Any]]:
        if not self._redis:
            return None
        try:
            raw = await self._redis.get(self._key(name))
            if raw:
                return json.loads(raw)
        except Exception as exc:  # pragma: no cover - network best effort
            logger.warning("Redis circuit state load failed for %s: %s", name, exc)
        return None

    async def save(self, name: str, state: Dict[str, Any]) -> None:
        if not self._redis:
            return
        try:
            await self._redis.set(self._key(name), json.dumps(state))
        except Exception as exc:  # pragma: no cover - network best effort
            logger.warning("Redis circuit state save failed for %s: %s", name, exc)

    async def reset(self, name: str) -> None:
        if not self._redis:
            return
        try:
            await self._redis.delete(self._key(name))
        except Exception as exc:  # pragma: no cover - network best effort
            logger.warning("Redis circuit state reset failed for %s: %s", name, exc)


def _make_redis() -> Optional[Any]:
    """Create an async Redis client from env if the package is installed."""
    if not _REDIS_AVAILABLE:
        return None
    url = os.environ.get("NEXUS_REDIS_URL", os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    try:
        return AsyncRedis.from_url(url, decode_responses=True, socket_connect_timeout=1)  # type: ignore[attr-defined]
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning("Could not create Redis client for circuit breaker: %s", exc)
        return None


class CircuitBreaker:
    """State machine with CLOSED -> OPEN -> HALF_OPEN -> CLOSED transitions.

    The breaker starts CLOSED. After ``fail_count`` consecutive failures it
    moves to OPEN and rejects further calls for ``reset_timeout`` seconds. The
    first call after the timeout is admitted in HALF_OPEN state: success closes
    the circuit, another failure re-opens it.
    """

    def __init__(
        self,
        name: str,
        fail_count: int = 5,
        reset_timeout: float = 30.0,
        redis: Optional[Any] = None,
    ):
        self.name = name
        self.fail_count = max(1, fail_count)
        self.reset_timeout = reset_timeout
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: Optional[float] = None
        self._lock = asyncio.Lock()
        # Sync lock so call_sync / record_*_sync can run from non-async
        # contexts (e.g. ruflo_proxy.call_ruflo_tool) without touching the
        # asyncio event loop. The async path keeps its own asyncio.Lock.
        self._sync_lock = threading.Lock()
        self._backend = _RedisStateBackend(redis)

    @property
    def state(self) -> CircuitState:
        return self._state

    def _can_attempt(self) -> bool:
        if self._state == CircuitState.CLOSED:
            return True
        if self._state == CircuitState.OPEN:
            if self._last_failure_time is None:
                return False
            if time.monotonic() - self._last_failure_time >= self.reset_timeout:
                return True
            return False
        # HALF_OPEN only allows one probe at a time; enforced by the lock below.
        return True

    async def _enter(self) -> None:
        async with self._lock:
            if self._state == CircuitState.OPEN and self._can_attempt():
                self._state = CircuitState.HALF_OPEN
                logger.info("Circuit breaker '%s' entering HALF_OPEN", self.name)

            if self._state == CircuitState.OPEN:
                until = (self._last_failure_time or time.monotonic()) + self.reset_timeout
                raise CircuitBreakerOpen(self.name, self._state, until)

            if self._state == CircuitState.HALF_OPEN:
                # Only one probe allowed. If another coroutine already entered
                # HALF_OPEN while we were waiting, reject to avoid a stampede.
                pass

    async def _record_success(self) -> None:
        async with self._lock:
            if self._state in (CircuitState.HALF_OPEN, CircuitState.OPEN):
                logger.info("Circuit breaker '%s' closing after success", self.name)
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._last_failure_time = None
            await self._backend.save(
                self.name,
                {
                    "state": self._state.value,
                    "failure_count": self._failure_count,
                    "last_failure_time": self._last_failure_time,
                },
            )

    async def _record_failure(self) -> None:
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            if self._state == CircuitState.HALF_OPEN or self._failure_count >= self.fail_count:
                old_state = self._state
                self._state = CircuitState.OPEN
                logger.warning(
                    "Circuit breaker '%s' opened after %d failures (previous state: %s)",
                    self.name,
                    self._failure_count,
                    old_state.value,
                )
            await self._backend.save(
                self.name,
                {
                    "state": self._state.value,
                    "failure_count": self._failure_count,
                    "last_failure_time": self._last_failure_time,
                },
            )

    async def call(
        self, coro_or_factory: Awaitable[_T] | Callable[[], Awaitable[_T]]
    ) -> _T:
        """Execute ``coro_or_factory`` under this circuit breaker.

        Accepts either an awaitable coroutine object or a zero-argument
        callable that returns an awaitable. The callable form is useful when
        the underlying coroutine must not be constructed while the circuit is
        OPEN (e.g. to avoid "never awaited" warnings).
        """
        await self._enter()
        coro = (
            coro_or_factory()
            if callable(coro_or_factory) and not asyncio.iscoroutine(coro_or_factory)
            else coro_or_factory
        )
        try:
            result = await coro
        except Exception:
            await self._record_failure()
            raise
        else:
            await self._record_success()
            return result

    # ── Synchronous path ─────────────────────────────────────────────────
    # Used by non-async callers (e.g. ruflo_proxy.call_ruflo_tool). Shares
    # the same state machine as the async path (self._state, _failure_count,
    # _last_failure_time) but mutates them under self._sync_lock so we never
    # touch the asyncio event loop. The Redis backend is async-only; sync
    # record methods skip the round-trip (in-memory state stays authoritative
    # for the process-local breaker registry).
    def _enter_sync(self) -> None:
        with self._sync_lock:
            if self._state == CircuitState.OPEN and self._can_attempt():
                self._state = CircuitState.HALF_OPEN
                logger.info("Circuit breaker '%s' entering HALF_OPEN (sync)", self.name)

            if self._state == CircuitState.OPEN:
                until = (self._last_failure_time or time.monotonic()) + self.reset_timeout
                raise CircuitBreakerOpen(self.name, self._state, until)

    def _record_success_sync(self) -> None:
        with self._sync_lock:
            if self._state in (CircuitState.HALF_OPEN, CircuitState.OPEN):
                logger.info("Circuit breaker '%s' closing after success (sync)", self.name)
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._last_failure_time = None

    def _record_failure_sync(self) -> None:
        with self._sync_lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            if self._state == CircuitState.HALF_OPEN or self._failure_count >= self.fail_count:
                old_state = self._state
                self._state = CircuitState.OPEN
                logger.warning(
                    "Circuit breaker '%s' opened after %d failures (sync, previous: %s)",
                    self.name, self._failure_count, old_state.value,
                )

    def call_sync(self, func: Callable[[], _T]) -> _T:
        """Execute a SYNCHRONOUS callable under this circuit breaker.

        Mirrors the async ``call`` API but uses ``self._sync_lock`` and the
        ``_record_*_sync`` mutators so it is safe to invoke from threads or
        other non-async contexts (e.g. ``ruflo_proxy.call_ruflo_tool``).
        Raises :class:`CircuitBreakerOpen` if the circuit is OPEN and not yet
        eligible for a half-open probe.
        """
        self._enter_sync()
        try:
            result = func()
        except Exception:
            self._record_failure_sync()
            raise
        self._record_success_sync()
        return result

    async def reset(self) -> None:
        """Manually close the breaker and clear failure history."""
        async with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._last_failure_time = None
            await self._backend.reset(self.name)


# Global per-name breaker registry.
_BREAKERS: Dict[str, CircuitBreaker] = {}
_BREAKER_LOCK = asyncio.Lock()


def _redis_from_kwargs(redis: Optional[Any] = None) -> Optional[Any]:
    if redis is not None:
        return redis
    if os.environ.get("NEXUS_CIRCUIT_REDIS", "0").lower() in ("1", "true", "yes"):
        return _make_redis()
    return None


async def get_breaker(
    name: str,
    fail_count: int = 5,
    reset_timeout: float = 30.0,
    redis: Optional[Any] = None,
) -> CircuitBreaker:
    """Return a per-tool circuit breaker, creating it if necessary.

    Args:
        name: Logical name for the external dependency (e.g. ``ruflo:mcp``).
        fail_count: Consecutive failures required to open the circuit.
        reset_timeout: Seconds before an OPEN circuit becomes HALF_OPEN.
        redis: Optional async Redis client. If None and
            ``NEXUS_CIRCUIT_REDIS=1`` is set, a client is created from env.
    """
    redis = _redis_from_kwargs(redis)
    if name in _BREAKERS:
        return _BREAKERS[name]
    async with _BREAKER_LOCK:
        if name not in _BREAKERS:
            _BREAKERS[name] = CircuitBreaker(
                name,
                fail_count=fail_count,
                reset_timeout=reset_timeout,
                redis=redis,
            )
        return _BREAKERS[name]


def get_breaker_sync(
    name: str,
    fail_count: int = 5,
    reset_timeout: float = 30.0,
    redis: Optional[Any] = None,
) -> CircuitBreaker:
    """Synchronous variant for non-async callers."""
    redis = _redis_from_kwargs(redis)
    if name not in _BREAKERS:
        _BREAKERS[name] = CircuitBreaker(
            name,
            fail_count=fail_count,
            reset_timeout=reset_timeout,
            redis=redis,
        )
    return _BREAKERS[name]


class Bulkhead:
    """Per-category asyncio.Semaphore wrapper."""

    _REGISTRY: Dict[str, "Bulkhead"] = {}
    _LOCK = asyncio.Lock()

    def __init__(self, category: str, limit: int = 10):
        self.category = category
        self.limit = max(1, limit)
        self._semaphore = asyncio.Semaphore(self.limit)

    async def acquire(self, timeout: Optional[float] = None) -> None:
        """Acquire the bulkhead semaphore. Raises BulkheadFull on timeout."""
        if timeout is None:
            await self._semaphore.acquire()
            return
        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=timeout)
        except asyncio.TimeoutError:
            raise BulkheadFull(self.category, self.limit) from None

    def release(self) -> None:
        self._semaphore.release()

    async def __aenter__(self) -> "Bulkhead":
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.release()

    @classmethod
    async def get(cls, category: str, limit: int = 10) -> "Bulkhead":
        if category in cls._REGISTRY:
            return cls._REGISTRY[category]
        async with cls._LOCK:
            if category not in cls._REGISTRY:
                cls._REGISTRY[category] = Bulkhead(category, limit)
            return cls._REGISTRY[category]

    @classmethod
    def get_sync(cls, category: str, limit: int = 10) -> "Bulkhead":
        if category not in cls._REGISTRY:
            cls._REGISTRY[category] = Bulkhead(category, limit)
        return cls._REGISTRY[category]


def breaker_protected(
    name: str,
    fail_count: int = 5,
    reset_timeout: float = 30.0,
    bulkhead_category: Optional[str] = None,
    bulkhead_limit: int = 10,
    bulkhead_timeout: Optional[float] = None,
    redis: Optional[Any] = None,
):
    """Decorator factory: circuit breaker + optional asyncio semaphore bulkhead.

    Args:
        name: Circuit breaker name.
        fail_count: Failures before opening.
        reset_timeout: Seconds before allowing a half-open probe.
        bulkhead_category: If set, wrap calls in a Bulkhead for this category.
        bulkhead_limit: Max concurrent calls for the bulkhead.
        bulkhead_timeout: Seconds to wait for bulkhead admission.
        redis: Optional async Redis client for shared breaker state.
    """

    def decorator(func: Callable[..., Awaitable[_T]]) -> Callable[..., Awaitable[_T]]:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> _T:
            breaker = await get_breaker(name, fail_count, reset_timeout, redis)

            def _factory() -> Awaitable[_T]:
                async def _invoke() -> _T:
                    if bulkhead_category:
                        bulkhead = await Bulkhead.get(bulkhead_category, bulkhead_limit)
                        await bulkhead.acquire(timeout=bulkhead_timeout)
                        try:
                            return await func(*args, **kwargs)
                        finally:
                            bulkhead.release()
                    return await func(*args, **kwargs)

                return _invoke()

            return await breaker.call(_factory)

        return wrapper

    return decorator
