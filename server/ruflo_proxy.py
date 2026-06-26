"""ruflo_proxy.py — NEXUS proxy for ruflo MCP tools.

Spawns ruflo MCP as a managed subprocess, queries its 261 tools,
and provides a forwarding layer so NEXUS can be the single MCP entry point.
"""
import json, os, shutil, subprocess, sys, time, threading
from pathlib import Path
from typing import Dict, List, Optional

_RUFLO_CMD = shutil.which("ruflo")
if not _RUFLO_CMD:
    _RUFLO_CMD = str(Path.home() / ".npm-global/bin/ruflo")
if not os.path.exists(_RUFLO_CMD):
    _RUFLO_CMD = str(Path.home() / ".npm/bin/ruflo")
if not os.path.exists(_RUFLO_CMD):
    _RUFLO_CMD = "ruflo"


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
    
    def call_tool(self, tool_name: str, arguments: Dict = None) -> Dict:
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
            return self._read_response()
    
    def get_tools(self) -> List[Dict]:
        if not self._tools:
            self.list_tools()
        return self._tools
    
    def stop(self) -> None:
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
    return _proxy


def get_ruflo_tools() -> List[Dict]:
    return get_proxy().get_tools()


def call_ruflo_tool(tool_name: str, arguments: Dict = None) -> str:
    proxy = get_proxy()
    resp = proxy.call_tool(tool_name, arguments)
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
