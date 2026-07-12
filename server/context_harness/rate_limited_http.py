"""Rate-limited httpx.AsyncClient wrapper for academic channel API calls.

Wires `server/integrations/rate_limited_proxy.TokenBucket` to prevent
bursts from hitting API rate limits (arXiv: 1 req/3s, S2: 1 req/s, 
GitHub: 10 req/s unauthenticated, PapersWithCode: 1 req/s).
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional


class RateLimitedClient:
    """Wraps an httpx.AsyncClient with a token-bucket rate limiter.

    Usage::

        async with RateLimitedClient(rate=1.0, burst=3) as client:
            response = await client.get("https://api.example.com/search")
    """

    def __init__(self, rate: float, burst: int = 3):
        self.rate = float(rate)
        self.burst = int(burst)
        self._tokens: float = float(burst)
        self._last_update = time.monotonic()
        self._lock = asyncio.Lock()
        self._client: "httpx.AsyncClient | None" = None  # type: ignore[name-defined]

    async def _acquire(self, timeout: Optional[float] = None) -> bool:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_update
            self._tokens = min(float(self.burst), self._tokens + elapsed * self.rate)
            self._last_update = now

            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True

            deficit = 1.0 - self._tokens
            wait_time = deficit / self.rate
            if timeout is not None and wait_time > timeout:
                return False
            await asyncio.sleep(wait_time)
            self._tokens = 0.0
            self._last_update = time.monotonic()
            return True

    async def __aenter__(self) -> "RateLimitedClient":
        import httpx
        self._client = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, *args) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def get(self, url: str, **kwargs):
        await self._acquire()
        if not self._client:
            raise RuntimeError("RateLimitedClient not entered")
        return await self._client.get(url, **kwargs)

    async def get_json(self, url: str, **kwargs) -> dict:
        await self._acquire()
        if not self._client:
            raise RuntimeError("RateLimitedClient not entered")
        resp = await self._client.get(url, **kwargs)
        resp.raise_for_status()
        return resp.json()


# Pre-configured clients per API
ARXIV_RATE = 0.33   # 1 req per 3 seconds (arXiv's polite-use policy)
S2_RATE = 1.0       # 1 req/s (S2 unauthenticated)
GITHUB_RATE = 10.0  # 10 req/s (GitHub unauthenticated)
PWC_RATE = 1.0      # 1 req/s (PapersWithCode)


def create_arxiv_client() -> RateLimitedClient:
    return RateLimitedClient(rate=ARXIV_RATE, burst=1)

def create_s2_client() -> RateLimitedClient:
    return RateLimitedClient(rate=S2_RATE, burst=3)

def create_github_client() -> RateLimitedClient:
    return RateLimitedClient(rate=GITHUB_RATE, burst=5)

def create_pwc_client() -> RateLimitedClient:
    return RateLimitedClient(rate=PWC_RATE, burst=3)
