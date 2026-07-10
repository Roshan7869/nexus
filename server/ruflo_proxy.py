"""ruflo_proxy.py — NEXUS proxy for ruflo MCP tools.

Spawns ruflo MCP as a managed subprocess, queries its 261 tools,
and provides a forwarding layer so NEXUS can be the single MCP entry point.
"""
import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from server.resilience.circuit_breaker import (
    CircuitBreakerOpen,
    breaker_protected,
    get_breaker,
    get_breaker_sync,
)

_RUFLO_CMD = shutil.which("ruflo")
if not _RUFLO_CMD:
    _RUFLO_CMD = str(Path.home() / ".npm-global/bin/ruflo")
if not os.path.exists(_RUFLO_CMD):
    _RUFLO_CMD = str(Path.home() / ".npm/bin/ruflo")
if not os.path.exists(_RUFLO_CMD):
    _RUFLO_CMD = "ruflo"

logger = logging.getLogger(__name__)

_RUFLO_CALL_TIMEOUT = 5.0
_RUFLO_HEALTH_INTERVAL = 30.0


class RufloProxyError(Exception):
    pass


class RufloProxy:
    """Manages a ruflo MCP subprocess and forwards tool calls."""

    def __init__(self, ruflo_cmd: str = None):
        self.ruflo_cmd = ruflo_cmd or _RUFLO_CMD
        self.proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._next_id = 100
        self._tools: List[Dict] = []
        self._initialized = False

        # Health-check background state (thread-safe).
        self._health_lock = threading.Lock()
        self._health_thread: Optional[threading.Thread] = None
        self._health_loop: Optional[asyncio.AbstractEventLoop] = None
        self._health_stop_event: Optional[asyncio.Event] = None
        self._health_task: Optional[asyncio.Task] = None

    def _read_response(self, timeout: float = 15.0) -> Dict:
        import select
        line = b""
        start = time.time()
        while time.time() - start < timeout:
            r, _, _ = select.select([self.proc.stdout], [], [], 0.1)
            if r:
                chunk = self.proc.stdout.readline()
                if chunk:
                    line += chunk
                    try:
                        return json.loads(line.decode())
                    except json.JSONDecodeError:
                        continue
            if self.proc.poll() is not None:
                break
        if line:
            try:
                return json.loads(line.decode())
            except json.JSONDecodeError:
                pass
        raise RufloProxyError(f"No valid MCP response within {timeout}s")

    def start(self) -> None:
        if self.proc and self.proc.poll() is None:
            return
        self.proc = subprocess.Popen(
            [self.ruflo_cmd, "mcp", "start"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        time.sleep(1.5)
        if self.proc.poll() is not None:
            err = self.proc.stderr.read().decode()
            raise RufloProxyError(f"ruflo MCP exited: {err}")

    def initialize(self) -> Dict:
        self.start()
        with self._lock:
            req = json.dumps({
                "jsonrpc": "2.0", "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "nexus", "version": "1.0.0"}
                },
                "id": 1
            }) + "\n"
            self.proc.stdin.write(req.encode())
            self.proc.stdin.flush()
            resp = self._read_response()
            self._initialized = True
            return resp

    def list_tools(self) -> List[Dict]:
        with self._lock:
            if not self._initialized:
                self.initialize()
            self._next_id += 1
            req = json.dumps({
                "jsonrpc": "2.0", "method": "tools/list",
                "id": self._next_id
            }) + "\n"
            self.proc.stdin.write(req.encode())
            self.proc.stdin.flush()
            resp = self._read_response()
            tools = resp.get("result", {}).get("tools", [])
            self._tools = tools
            return tools

    def call_tool(
        self, tool_name: str, arguments: Optional[Dict] = None, timeout: float = _RUFLO_CALL_TIMEOUT
    ) -> Dict:
        with self._lock:
            if not self._initialized:
                self.initialize()
            self._next_id += 1
            params = {"name": tool_name}
            if arguments:
                params["arguments"] = arguments
            req = json.dumps({
                "jsonrpc": "2.0", "method": "tools/call",
                "params": params,
                "id": self._next_id
            }) + "\n"
            self.proc.stdin.write(req.encode())
            self.proc.stdin.flush()
            return self._read_response(timeout=timeout)

    async def acall_tool(self, tool_name: str, arguments: Optional[Dict] = None) -> Dict:
        """Async circuit-breaker-protected call with a 5-second timeout.

        The breaker is named ``ruflo:{tool_name}`` so a failing tool only
        degrades itself, not the whole ruflo backend.
        """
        breaker = await get_breaker(f"ruflo:{tool_name}", fail_count=5, reset_timeout=30.0)

        async def _invoke() -> Dict:
            loop = asyncio.get_running_loop()
            return await asyncio.wait_for(
                loop.run_in_executor(None, self.call_tool, tool_name, arguments),
                timeout=_RUFLO_CALL_TIMEOUT,
            )

        try:
            return await breaker.call(_invoke)
        except CircuitBreakerOpen as exc:
            logger.warning("Ruflo tool %s rejected by circuit breaker: %s", tool_name, exc)
            return {
                "error": {
                    "code": "CIRCUIT_OPEN",
                    "message": str(exc),
                },
                "result": None,
            }
        except asyncio.TimeoutError:
            logger.warning("Ruflo tool %s timed out after %.1fs", tool_name, _RUFLO_CALL_TIMEOUT)
            return {
                "error": {
                    "code": "TIMEOUT",
                    "message": f"ruflo tool {tool_name} timed out after {_RUFLO_CALL_TIMEOUT}s",
                },
                "result": None,
            }
        except Exception as exc:
            logger.warning("Ruflo tool %s failed: %s", tool_name, exc)
            return {
                "error": {
                    "code": "ERROR",
                    "message": str(exc),
                },
                "result": None,
            }

    def get_tools(self) -> List[Dict]:
        if not self._tools:
            self.list_tools()
        return self._tools

    def _start_health_check_thread(self) -> None:
        """Start a daemon thread that runs the async health-check loop."""
        with self._health_lock:
            if self._health_thread is not None:
                return

            def _run() -> None:
                loop = asyncio.new_event_loop()
                self._health_loop = loop
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(self._health_check_loop())
                finally:
                    loop.close()
                    self._health_loop = None

            self._health_thread = threading.Thread(target=_run, daemon=True, name="ruflo-health")
            self._health_thread.start()
            logger.info("Started ruflo proxy health-check thread")

    async def _health_check_loop(self) -> None:
        self._health_stop_event = asyncio.Event()
        while not self._health_stop_event.is_set():
            try:
                healthy = await health_check_ping(self)
                if not healthy:
                    logger.warning("Ruflo proxy health check failed")
            except Exception as exc:
                logger.warning("Ruflo proxy health check error: %s", exc)
            try:
                await asyncio.wait_for(
                    self._health_stop_event.wait(),
                    timeout=_RUFLO_HEALTH_INTERVAL,
                )
            except asyncio.TimeoutError:
                pass

    def _stop_health_checks(self) -> None:
        with self._health_lock:
            if self._health_stop_event and self._health_loop:
                self._health_loop.call_soon_threadsafe(self._health_stop_event.set)
            self._health_thread = None

    def stop(self) -> None:
        self._stop_health_checks()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        self._initialized = False
        self._tools = []


# Singleton
_proxy: Optional[RufloProxy] = None


def get_proxy() -> RufloProxy:
    global _proxy
    if _proxy is None:
        _proxy = RufloProxy()
        _proxy.initialize()
        _proxy._start_health_check_thread()
    return _proxy


def get_ruflo_tools() -> List[Dict]:
    return get_proxy().get_tools()


def call_ruflo_tool(tool_name: str, arguments: Dict = None) -> str:
    proxy = get_proxy()
    # Wire the SYNC path through the per-tool circuit breaker so a failing
    # ruflo tool cannot cascade across the router when invoked synchronously.
    # Mirrors the async acall_tool() contract: breaker name `ruflo:{tool_name}`,
    # fail_count=5, reset_timeout=30s. OPEN breaker → structured error JSON,
    # NOT a raised exception (matches acall_tool's return shape so callers
    # don't need a separate sync error path).
    breaker = get_breaker_sync(f"ruflo:{tool_name}", fail_count=5, reset_timeout=30.0)
    try:
        resp = breaker.call_sync(lambda: proxy.call_tool(tool_name, arguments))
    except CircuitBreakerOpen as exc:
        logger.warning("Ruflo tool %s rejected by circuit breaker (sync): %s", tool_name, exc)
        return json.dumps({
            "ruflo_tool": tool_name,
            "result": "",
            "isError": True,
            "error": {"code": "CIRCUIT_OPEN", "message": str(exc)},
        }, indent=2)
    return _format_ruflo_response(tool_name, resp)


def _format_ruflo_response(tool_name: str, resp: Dict) -> str:
    result = resp.get("result", {})
    content = result.get("content", [])

    parts = []
    for c in content:
        if c.get("type") == "text":
            parts.append(c.get("text", ""))
    text = "\n".join(parts)

    if resp.get("error"):
        raise RufloProxyError(f"ruflo error: {resp['error']}")

    return json.dumps({
        "ruflo_tool": tool_name,
        "result": text,
        "isError": result.get("isError", False),
    }, indent=2)


async def async_call_ruflo_tool(tool_name: str, arguments: Dict = None) -> str:
    """Async circuit-breaker + timeout wrapper around :func:`call_ruflo_tool`.

    Returns the same JSON string shape as the synchronous helper but guarantees
    the call travels through ``RufloProxy.acall_tool`` (per-tool breaker and
    5-second timeout).
    """
    proxy = get_proxy()
    resp = await proxy.acall_tool(tool_name, arguments)
    return _format_ruflo_response(tool_name, resp)


async def health_check_ping(proxy: Optional[RufloProxy] = None) -> bool:
    """Ping ruflo with a lightweight call. Returns True if responsive.

    Uses ``tools/list`` because it exercises the MCP transport without
    invoking a heavy ruflo tool.  A 5-second timeout prevents hangs.
    """
    target = proxy or get_proxy()
    try:
        loop = asyncio.get_running_loop()
        await asyncio.wait_for(
            loop.run_in_executor(None, target.list_tools),
            timeout=_RUFLO_CALL_TIMEOUT,
        )
        return True
    except Exception as exc:
        logger.debug("Ruflo health check ping failed: %s", exc)
        return False
