"""Tests for server.integrations.rate_limited_proxy."""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import pytest

from server.integrations.rate_limited_proxy import (
    MemoryStore,
    QuotaExceeded,
    QuotaTracker,
    RateLimitError,
    TokenBucket,
    call_rate_limited,
    cashfree_bucket,
    cashfree_quota,
    get_quota_status,
)


@pytest.mark.asyncio
async def test_token_bucket_throttles() -> None:
    """A burst of 1 followed by N calls should be paced by the fill rate."""
    bucket = TokenBucket(rate=10.0, burst=1)
    iterations = 5
    start = time.monotonic()
    for _ in range(iterations):
        await bucket.acquire()
    elapsed = time.monotonic() - start

    # With burst=1, first call is free; remaining 4 need 0.4s at 10/s.
    assert elapsed >= 0.35, f"expected throttling, elapsed={elapsed:.3f}s"


@pytest.mark.asyncio
async def test_quota_tracks_daily_usage() -> None:
    """QuotaTracker increments usage and blocks when the daily limit is hit."""
    store = MemoryStore()
    quota = QuotaTracker(
        key="test:daily", store=store, daily_limit=5, ttl_seconds=60
    )

    assert await quota.consume() is True
    assert await quota.consume() is True

    status = await get_quota_status(quota)
    assert status["used"] == 2
    assert status["remaining"] == 3
    assert status["daily_limit"] == 5
    assert status["key"] == "test:daily"

    # Consume the remaining 3 tokens.
    assert await quota.consume(3) is True
    # Next request would exceed the limit.
    assert await quota.consume(1) is False

    final = await get_quota_status(quota)
    assert final["used"] == 5
    assert final["remaining"] == 0


@pytest.mark.asyncio
async def test_cashfree_defaults() -> None:
    """Cashfree defaults match the documented production limits."""
    bucket = cashfree_bucket()
    assert bucket.rate == 10.0
    assert bucket.burst == 20

    quota = cashfree_quota()
    assert quota.key == "cashfree:daily"
    expected_limit = int(os.getenv("CASHFREE_DAILY_QUOTA", "10000"))
    assert quota.daily_limit == expected_limit

    status = await get_quota_status(quota)
    assert status["used"] == 0
    assert status["remaining"] == expected_limit


@pytest.mark.asyncio
async def test_call_rate_limited_wrapper_enforces_quota() -> None:
    """call_rate_limited applies both rate limiting and quota enforcement."""
    calls: list[tuple[str, dict]] = []

    async def fake_caller(tool_name: str, arguments: dict) -> dict[str, Any]:
        calls.append((tool_name, arguments))
        return {"ok": True}

    bucket = TokenBucket(rate=100.0, burst=10)
    quota = QuotaTracker(
        key="wrapper:test", store=MemoryStore(), daily_limit=2, ttl_seconds=60
    )

    result = await call_rate_limited(
        "cashfree_payout",
        {"amount": 100},
        bucket=bucket,
        quota=quota,
        caller=fake_caller,
    )
    assert result == {"ok": True}
    assert calls == [("cashfree_payout", {"amount": 100})]

    # Exhaust remaining quota.
    await call_rate_limited(
        "cashfree_payout",
        {"amount": 200},
        bucket=bucket,
        quota=quota,
        caller=fake_caller,
    )

    # Third call should raise QuotaExceeded.
    with pytest.raises(QuotaExceeded):
        await call_rate_limited(
            "cashfree_payout",
            {"amount": 300},
            bucket=bucket,
            quota=quota,
            caller=fake_caller,
        )


@pytest.mark.asyncio
async def test_token_bucket_timeout_raises() -> None:
    """TokenBucket.acquire with a short timeout raises RateLimitError."""
    bucket = TokenBucket(rate=1.0, burst=1)
    await bucket.acquire()  # consume the only token

    with pytest.raises(RateLimitError):
        await bucket.acquire(timeout=0.05)
