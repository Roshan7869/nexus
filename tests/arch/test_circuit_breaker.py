import asyncio
from typing import Awaitable

import pytest

from server.resilience.circuit_breaker import (
    Bulkhead,
    BulkheadFull,
    CircuitBreaker,
    CircuitBreakerOpen,
    CircuitState,
    breaker_protected,
    get_breaker,
)


class AlwaysFail(Exception):
    pass


@pytest.fixture(autouse=True)
def _reset_registry():
    """Keep the global breaker/bulkhead registries clean between tests."""
    from server.resilience import circuit_breaker as cb

    cb._BREAKERS.clear()
    Bulkhead._REGISTRY.clear()
    yield
    cb._BREAKERS.clear()
    Bulkhead._REGISTRY.clear()


@pytest.mark.asyncio
async def test_opens_after_five_failures():
    breaker = await get_breaker("failing-tool", fail_count=5, reset_timeout=60.0)
    assert breaker.state == CircuitState.CLOSED

    calls = 0

    def make_fail() -> Awaitable[None]:
        async def _fail() -> None:
            nonlocal calls
            calls += 1
            raise AlwaysFail("boom")

        return _fail()

    # Four failures keep the breaker CLOSED while it accumulates the counter.
    for i in range(4):
        with pytest.raises(AlwaysFail):
            await breaker.call(make_fail)
        assert breaker.state == CircuitState.CLOSED, f"state after {i + 1} failures"

    # The fifth consecutive failure opens the circuit.
    with pytest.raises(AlwaysFail):
        await breaker.call(make_fail)
    assert breaker.state == CircuitState.OPEN

    # Subsequent calls are rejected fast without executing the underlying coroutine.
    with pytest.raises(CircuitBreakerOpen):
        await breaker.call(make_fail)
    assert calls == 5  # breaker stopped invoking after open


@pytest.mark.asyncio
async def test_half_open_after_reset():
    breaker = CircuitBreaker("probe-tool", fail_count=2, reset_timeout=0.1)
    assert breaker.state == CircuitState.CLOSED

    def make_fail() -> Awaitable[None]:
        async def _fail() -> None:
            raise AlwaysFail("boom")

        return _fail()

    async def ok() -> str:
        return "success"

    # Open the breaker.
    with pytest.raises(AlwaysFail):
        await breaker.call(make_fail)
    with pytest.raises(AlwaysFail):
        await breaker.call(make_fail)
    assert breaker.state == CircuitState.OPEN

    # Wait for reset_timeout, then the next call is admitted as HALF_OPEN.
    await asyncio.sleep(0.15)
    result = await breaker.call(ok)
    assert result == "success"
    assert breaker.state == CircuitState.CLOSED

    # Re-open, wait, then a failing HALF_OPEN probe re-opens the circuit.
    with pytest.raises(AlwaysFail):
        await breaker.call(make_fail)
    with pytest.raises(AlwaysFail):
        await breaker.call(make_fail)
    assert breaker.state == CircuitState.OPEN
    await asyncio.sleep(0.15)
    with pytest.raises(AlwaysFail):
        await breaker.call(make_fail)
    assert breaker.state == CircuitState.OPEN


@pytest.mark.asyncio
async def test_bulkhead_limits_concurrency():
    bulkhead = await Bulkhead.get("limited", limit=2)

    async def slow_task(name: str, duration: float = 0.2) -> str:
        await bulkhead.acquire()
        try:
            await asyncio.sleep(duration)
            return name
        finally:
            bulkhead.release()

    # Three concurrent tasks; two run immediately, the third waits.
    results = await asyncio.gather(
        slow_task("a"),
        slow_task("b"),
        slow_task("c"),
    )
    assert sorted(results) == ["a", "b", "c"]

    # With a short timeout the third task is rejected.
    async def timed_task() -> None:
        await bulkhead.acquire(timeout=0.05)
        try:
            await asyncio.sleep(0.2)
        finally:
            bulkhead.release()

    # Hold both slots.
    await bulkhead.acquire()
    await bulkhead.acquire()
    with pytest.raises(BulkheadFull):
        await bulkhead.acquire(timeout=0.05)
    bulkhead.release()
    bulkhead.release()


@pytest.mark.asyncio
async def test_breaker_protected_decorator_uses_breaker_and_bulkhead():
    calls = []

    @breaker_protected(
        "decorated-tool",
        fail_count=2,
        reset_timeout=60.0,
        bulkhead_category="external",
        bulkhead_limit=1,
    )
    async def unstable(should_fail: bool) -> str:
        calls.append(should_fail)
        if should_fail:
            raise AlwaysFail("nope")
        return "ok"

    # Two failures open the breaker.
    with pytest.raises(AlwaysFail):
        await unstable(True)
    with pytest.raises(AlwaysFail):
        await unstable(True)
    with pytest.raises(CircuitBreakerOpen):
        await unstable(True)

    assert len(calls) == 2


@pytest.mark.asyncio
async def test_breaker_manual_reset():
    breaker = CircuitBreaker("reset-tool", fail_count=1, reset_timeout=600.0)

    def make_fail() -> Awaitable[None]:
        async def _fail() -> None:
            raise AlwaysFail("boom")

        return _fail()

    with pytest.raises(AlwaysFail):
        await breaker.call(make_fail)
    assert breaker.state == CircuitState.OPEN

    await breaker.reset()
    assert breaker.state == CircuitState.CLOSED
