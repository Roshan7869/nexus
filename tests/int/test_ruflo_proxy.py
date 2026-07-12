"""Tests for ruflo proxy circuit breaker + timeout + health ping (INT-1)."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from server.resilience.circuit_breaker import (
    CircuitBreakerOpen,
    CircuitState,
    get_breaker,
)
from server.ruflo_proxy import RufloProxy, RufloProxyError, health_check_ping
from server.integrations.ruflo_async_proxy import acall_ruflo_tool


@pytest.fixture(autouse=True)
def _reset_registry():
    """Keep the global breaker registry and proxy singleton clean between tests."""
    import server.resilience.circuit_breaker as cb
    import server.ruflo_proxy as rp

    cb._BREAKERS.clear()
    old_proxy = rp._proxy
    rp._proxy = None
    yield
    cb._BREAKERS.clear()
    rp._proxy = old_proxy


@pytest.fixture
def fake_proxy():
    """Return a RufloProxy with a mocked subprocess (no real ruflo spawn)."""
    proxy = RufloProxy(ruflo_cmd="/bin/true")
    proxy._initialized = True
    proxy.proc = MagicMock()
    proxy.proc.poll.return_value = None
    return proxy


@pytest.mark.asyncio
async def test_hung_call_blocked(fake_proxy):
    """A call that exceeds 5 seconds is cancelled and returns a structured error."""

    def slow_call(*args, **kwargs):
        time.sleep(10.0)
        return {"result": {"content": [{"type": "text", "text": "too late"}]}}

    fake_proxy.call_tool = slow_call

    start = time.monotonic()
    resp = await fake_proxy.acall_tool("slow_tool", {"arg": 1})
    elapsed = time.monotonic() - start

    assert elapsed < 6.5, f"hung call was not blocked quickly: {elapsed:.2f}s"
    assert resp.get("error", {}).get("code") == "TIMEOUT"
    assert "timed out" in resp["error"]["message"]


@pytest.mark.asyncio
async def test_failed_tool_auto_degraded(fake_proxy):
    """Repeated failures trip the per-tool circuit breaker."""

    def failing_call(*args, **kwargs):
        raise RufloProxyError("ruflo exploded")

    fake_proxy.call_tool = failing_call
    tool_name = "flaky_tool"

    # Five failures should open the circuit.
    for _ in range(5):
        resp = await fake_proxy.acall_tool(tool_name, {})
        assert resp.get("error") is not None

    breaker = await get_breaker(f"ruflo:{tool_name}")
    assert breaker.state == CircuitState.OPEN

    # The next call is rejected immediately without invoking the tool.
    start = time.monotonic()
    resp = await fake_proxy.acall_tool(tool_name, {})
    elapsed = time.monotonic() - start

    assert elapsed < 0.5, f"open-circuit call should be fast: {elapsed:.2f}s"
    assert resp.get("error", {}).get("code") == "CIRCUIT_OPEN"


@pytest.mark.asyncio
async def test_health_check_ping(fake_proxy):
    """health_check_ping returns True when list_tools responds and False on failure."""

    def ok_list_tools():
        return [{"name": "ruflo_system_status"}]

    fake_proxy.list_tools = ok_list_tools
    assert await health_check_ping(fake_proxy) is True

    def failing_list_tools():
        raise RufloProxyError("no response")

    fake_proxy.list_tools = failing_list_tools
    assert await health_check_ping(fake_proxy) is False


@pytest.mark.asyncio
async def test_acall_ruflo_tool_json_shape_on_success(fake_proxy):
    """acall_ruflo_tool returns the same JSON shape as the sync helper."""

    def ok_call(tool_name, arguments):
        return {
            "result": {
                "content": [{"type": "text", "text": "hello from ruflo"}],
                "isError": False,
            }
        }

    fake_proxy.call_tool = ok_call
    with patch("server.integrations.ruflo_async_proxy.get_proxy", return_value=fake_proxy):
        text = await acall_ruflo_tool("echo", {"msg": "hi"})

    payload = json.loads(text)
    assert payload["ruflo_tool"] == "echo"
    assert payload["result"] == "hello from ruflo"
    assert payload["isError"] is False


@pytest.mark.asyncio
async def test_acall_ruflo_tool_json_shape_on_timeout(fake_proxy):
    """acall_ruflo_tool surfaces timeout as an isError JSON response."""

    def slow_call(*args, **kwargs):
        time.sleep(10)
        return {"result": "never"}

    fake_proxy.call_tool = slow_call
    with patch("server.integrations.ruflo_async_proxy.get_proxy", return_value=fake_proxy):
        text = await acall_ruflo_tool("hang", {})

    payload = json.loads(text)
    assert payload["ruflo_tool"] == "hang"
    assert payload["isError"] is True
    assert payload["errorCode"] == "TIMEOUT"
