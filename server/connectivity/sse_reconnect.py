"""Resilient SSE (Server-Sent Events) client and server helpers.

Provides:
- SSEClient: async client with automatic reconnect and exponential backoff.
- SSEHeartbeat / event_stream_with_heartbeat: server-side heartbeat injection.

The SSE wire format (per the HTML/SSE spec) is parsed into dicts keyed by
field name (``event``, ``data``, ``id``, ``retry``, ...). Multiple ``data:``
lines on a single event are joined with ``\\n`` to match the spec.
"""
from __future__ import annotations

import asyncio
import random
from typing import Any, AsyncGenerator, Optional

import httpx

# Tunable defaults -----------------------------------------------------------
DEFAULT_BACKOFF_BASE = 0.5
DEFAULT_BACKOFF_CAP = 16.0
DEFAULT_MAX_RETRIES = 5
DEFAULT_HEARTBEAT_INTERVAL = 15.0
HEARTBEAT_COMMENT = ":heartbeat\n\n"


class ExponentialBackoff:
    """Deterministic exponential schedule with optional jitter.

    ``sequence(n)`` returns the *unjittered* schedule (handy for tests/asserts);
    ``next_delay()`` advances an internal attempt counter and returns a jittered
    delay in ``[0, min(base * 2**attempt, cap)]``.
    """

    def __init__(
        self,
        base: float = DEFAULT_BACKOFF_BASE,
        cap: float = DEFAULT_BACKOFF_CAP,
    ) -> None:
        self.base = base
        self.cap = cap
        self._attempt = 0

    def _delay_for(self, attempt: int) -> float:
        return min(self.base * (2 ** attempt), self.cap)

    def sequence(self, n: int) -> list[float]:
        """Return the unjittered schedule of the next ``n`` delays."""
        return [self._delay_for(i) for i in range(n)]

    def next_delay(self) -> float:
        """Return a jittered delay for the current attempt, then advance."""
        upper = self._delay_for(self._attempt)
        self._attempt += 1
        return random.uniform(0.0, upper)

    def reset(self) -> None:
        self._attempt = 0


class SSEHeartbeat:
    """Wrap an async generator, injecting SSE comment heartbeats when idle.

    Used as middleware::

        heartbeat = SSEHeartbeat(interval=0.5)
        async for chunk in heartbeat(upstream_generator):
            ...

    The same underlying ``anext()`` await is reused across heartbeat timeouts,
    so the wrapped generator is never cancelled mid-yield.
    """

    def __init__(self, interval: float = DEFAULT_HEARTBEAT_INTERVAL) -> None:
        self.interval = interval

    async def __call__(self, source: AsyncGenerator[str, None]) -> AsyncGenerator[str, None]:
        agen = source.__aiter__()
        # Schedule the first pull exactly once so heartbeats don't cancel it.
        next_task = asyncio.ensure_future(agen.__anext__())
        try:
            while True:
                done, _ = await asyncio.wait(
                    {next_task},
                    timeout=self.interval,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if next_task in done:
                    try:
                        item = next_task.result()
                    except StopAsyncIteration:
                        break
                    yield item
                    next_task = asyncio.ensure_future(agen.__anext__())
                else:
                    # Timed out waiting for the next chunk — keep the LLM/client
                    # connection warm with an SSE comment line.
                    yield HEARTBEAT_COMMENT
        finally:
            if not next_task.done():
                next_task.cancel()
                try:
                    await next_task
                except (asyncio.CancelledError, StopAsyncIteration, Exception):
                    pass


async def event_stream_with_heartbeat(
    source: AsyncGenerator[str, None],
    heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL,
) -> AsyncGenerator[str, None]:
    """Wrap ``source`` with SSE heartbeat injection at ``heartbeat_interval``."""
    heartbeat = SSEHeartbeat(interval=heartbeat_interval)
    async for chunk in heartbeat(source):
        yield chunk


class SSEClient:
    """Async iterator over an SSE endpoint with reconnect + Last-Event-ID resume.

    On a transport error mid-stream, the client reconnects (up to
    ``max_retries`` additional attempts) and resumes from the last received
    event id by sending the ``Last-Event-ID`` header.
    """

    def __init__(
        self,
        url: str,
        *,
        max_retries: int = DEFAULT_MAX_RETRIES,
        client: Optional[httpx.AsyncClient] = None,
        backoff: Optional[ExponentialBackoff] = None,
    ) -> None:
        self.url = url
        self.max_retries = max_retries
        self._client = client
        self._owns_client = client is None
        self._last_event_id: Optional[str] = None
        self._backoff = backoff or ExponentialBackoff()

    def __aiter__(self):
        return self._iter()

    async def _iter(self) -> AsyncGenerator[dict[str, Any], None]:
        failures = 0
        while True:
            headers: dict[str, str] = {}
            if self._last_event_id is not None:
                headers["Last-Event-ID"] = self._last_event_id
            client = self._client if self._client is not None else httpx.AsyncClient()
            try:
                async with client.stream("GET", self.url, headers=headers) as response:
                    response.raise_for_status()
                    async for event in self._parse_stream(response.aiter_lines()):
                        if event.get("id") is not None:
                            self._last_event_id = event["id"]
                        yield event
                # Stream closed cleanly — reset backoff and stop.
                self._backoff.reset()
                return
            except StopAsyncIteration:
                self._backoff.reset()
                return
            except Exception:
                failures += 1
                if failures > self.max_retries:
                    return
                # Back off before the next attempt.
                await asyncio.sleep(self._backoff.next_delay())
                continue

    @staticmethod
    async def _parse_stream(lines: AsyncGenerator[str, None]) -> AsyncGenerator[dict[str, Any], None]:
        """Parse raw SSE lines into event dicts.

        A blank line dispatches the accumulated event. ``data:`` lines are
        concatenated with ``\\n``. Comment lines (``:``) are ignored.
        """
        event: dict[str, Any] = {}
        data_lines: list[str] = []
        async for line in lines:
            stripped = line.rstrip("\r\n")
            if stripped == "":
                if data_lines:
                    event["data"] = "\n".join(data_lines)
                if event:
                    yield event
                event = {}
                data_lines = []
                continue
            if stripped.startswith(":"):
                continue  # SSE comment / heartbeat
            field, sep, value = stripped.partition(":")
            if sep and value.startswith(" "):
                value = value[1:]
            if field == "data":
                data_lines.append(value)
            elif field:
                event[field] = value
        # Flush a trailing event if the stream ended without a final blank line.
        if data_lines:
            event["data"] = "\n".join(data_lines)
        if event:
            yield event

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
