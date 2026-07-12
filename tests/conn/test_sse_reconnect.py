"""Tests for SSE reconnect/backoff and heartbeat helpers (CONN-4)."""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from server.connectivity.sse_reconnect import (
    ExponentialBackoff,
    SSEClient,
    SSEHeartbeat,
    event_stream_with_heartbeat,
)


class TestExponentialBackoff:
    def test_exponential_backoff_sequence(self):
        """Delays follow 0.5, 1, 2, 4, 8, 16, 16... capped at 16s."""
        backoff = ExponentialBackoff(base=0.5, cap=16.0)
        seq = backoff.sequence(8)
        assert seq == [0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 16.0, 16.0]

    def test_next_delay_is_within_jitter_bounds(self):
        """next_delay returns a value in [0, capped_delay]."""
        backoff = ExponentialBackoff(base=0.5, cap=16.0)
        for i in range(6):
            delay = backoff.next_delay()
            expected_max = min(0.5 * (2 ** i), 16.0)
            assert 0.0 <= delay <= expected_max


class TestHeartbeatInjection:
    @pytest.mark.asyncio
    async def test_heartbeat_injected(self):
        """Idle generator receives SSE heartbeat comments."""
        async def slow_generator():
            await asyncio.sleep(0.05)
            yield "data: first\n\n"
            await asyncio.sleep(0.05)
            yield "data: second\n\n"

        chunks = []
        async for chunk in event_stream_with_heartbeat(slow_generator(), heartbeat_interval=0.02):
            chunks.append(chunk)

        assert any(chunk == ":heartbeat\n\n" for chunk in chunks)
        assert "data: first\n\n" in chunks
        assert "data: second\n\n" in chunks

    @pytest.mark.asyncio
    async def test_heartbeat_middleware_callable(self):
        """SSEHeartbeat can be used as middleware around an async generator."""
        async def tiny_generator():
            await asyncio.sleep(0.03)
            yield "data: ok\n\n"

        heartbeat = SSEHeartbeat(interval=0.01)
        chunks = [chunk async for chunk in heartbeat(tiny_generator())]
        assert ":heartbeat\n\n" in chunks
        assert "data: ok\n\n" in chunks

    @pytest.mark.asyncio
    async def test_no_heartbeat_when_active(self):
        """Frequent output prevents heartbeat injection."""
        async def fast_generator():
            for i in range(5):
                yield f"data: {i}\n\n"

        chunks = [chunk async for chunk in event_stream_with_heartbeat(fast_generator(), heartbeat_interval=0.05)]
        assert ":heartbeat\n\n" not in chunks
        assert len(chunks) == 5


class MockStreamContext:
    """Async context manager returned by a mocked httpx.AsyncClient.stream()."""

    def __init__(self, items):
        self.items = items

    async def __aenter__(self):
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.aiter_lines = self._make_aiter_lines()
        return response

    def _make_aiter_lines(self):
        items = self.items

        async def aiter_lines():
            for item in items:
                if isinstance(item, BaseException):
                    raise item
                yield item

        return aiter_lines

    async def __aexit__(self, exc_type, exc, tb):
        return False


class TestSSEClientReconnect:
    @pytest.mark.asyncio
    async def test_reconnect_after_disconnect(self):
        """SSEClient reconnects and resumes yielding events after a transport failure."""
        mock_client = MagicMock(spec=httpx.AsyncClient)
        mock_client.stream = MagicMock(side_effect=[
            MockStreamContext(["data: hello\n", "\n", RuntimeError("boom")]),
            MockStreamContext(["data: world\n", "\n"]),
        ])

        sse = SSEClient("http://test/events", max_retries=3, client=mock_client)

        events = []
        async for event in sse:
            events.append(event)

        assert len(events) == 2
        assert events[0]["data"] == "hello"
        assert events[1]["data"] == "world"
        assert mock_client.stream.call_count == 2
        await sse.close()

    @pytest.mark.asyncio
    async def test_max_retries_exceeded(self):
        """SSEClient stops after max_retries consecutive failures."""
        mock_client = MagicMock(spec=httpx.AsyncClient)
        mock_client.stream = MagicMock(side_effect=httpx.ConnectError("always fails"))

        sse = SSEClient("http://test/events", max_retries=2, client=mock_client)

        events = [event async for event in sse]
        assert events == []
        # Initial attempt + 2 retries = 3 calls.
        assert mock_client.stream.call_count == 3
        await sse.close()

    @pytest.mark.asyncio
    async def test_parse_multiline_event(self):
        """SSE parser handles multi-line data fields."""
        async def lines():
            yield "event: update\n"
            yield "id: 42\n"
            yield "data: line one\n"
            yield "data: line two\n"
            yield "\n"

        response = MagicMock()
        response.aiter_lines = lines

        events = []
        async for event in SSEClient._parse_stream(response.aiter_lines()):
            events.append(event)

        assert len(events) == 1
        assert events[0]["event"] == "update"
        assert events[0]["id"] == "42"
        assert events[0]["data"] == "line one\nline two"

    @pytest.mark.asyncio
    async def test_last_event_id_resumed_on_reconnect(self):
        """SSEClient sends Last-Event-ID header on reconnect."""
        mock_client = MagicMock(spec=httpx.AsyncClient)
        mock_client.stream = MagicMock(side_effect=[
            MockStreamContext(["id: 7\n", "data: hello\n", "\n", RuntimeError("boom")]),
            MockStreamContext([]),
        ])

        sse = SSEClient("http://test/events", max_retries=2, client=mock_client)
        events = [event async for event in sse]
        assert events[0]["id"] == "7"

        calls = mock_client.stream.call_args_list
        assert calls[0].kwargs.get("headers", {}).get("Last-Event-ID") is None
        assert calls[1].kwargs.get("headers", {}).get("Last-Event-ID") == "7"
        await sse.close()
