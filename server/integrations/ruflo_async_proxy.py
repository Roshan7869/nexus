"""ruflo_async_proxy.py — Async circuit-breaker wrapper for the ruflo proxy.

Wraps the synchronous :class:`server.ruflo_proxy.RufloProxy` so that every
ruflo tool invocation is protected by a per-tool circuit breaker and a
5-second timeout.  Existing synchronous callers can continue using
:func:`server.ruflo_proxy.call_ruflo_tool` unchanged.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional

from server.resilience.circuit_breaker import CircuitBreakerOpen, get_breaker
from server.ruflo_proxy import RufloProxy, RufloProxyError, get_proxy

_RUFLO_CALL_TIMEOUT = 5.0


async def acall_ruflo_tool(tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> str:
    """Invoke a ruflo tool asynchronously with breaker + timeout protection.

    Returns a JSON string compatible with :func:`server.ruflo_proxy.call_ruflo_tool`.
    On circuit-open or timeout, the result contains ``isError=True`` instead of
    raising, so the MCP tool surface degrades gracefully.
    """
    proxy = get_proxy()
    resp = await proxy.acall_tool(tool_name, arguments)

    if resp.get("error"):
        return json.dumps({
            "ruflo_tool": tool_name,
            "result": resp["error"]["message"],
            "isError": True,
            "errorCode": resp["error"].get("code", "ERROR"),
        }, indent=2)

    result = resp.get("result", {})
    content = result.get("content", [])
    parts = [c.get("text", "") for c in content if c.get("type") == "text"]
    text = "\n".join(parts)

    return json.dumps({
        "ruflo_tool": tool_name,
        "result": text,
        "isError": result.get("isError", False),
    }, indent=2)


async def health_check_ping(proxy: Optional[RufloProxy] = None) -> bool:
    """Re-exported async health ping from the underlying proxy."""
    from server.ruflo_proxy import health_check_ping as _ping
    return await _ping(proxy)
