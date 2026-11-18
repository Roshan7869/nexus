"""Wenlan MCP client — drive `wenlan-mcp.exe` over stdio JSON-RPC.

Wenlan ships an MCP server (verified live: `wenlan-mcp` v0.22.1, 59 tools) that talks
to the local `wenlan-server` daemon. Rather than reverse-engineering the daemon's own
`/api/*` routes (only `/api/health` is publicly shaped; the rest is the SPA), NEXUS
speaks MCP to it — the interface the vendor actually supports.

Usage:
    with WenlanMCP() as w:
        w.list_tools()                 # 59 tool definitions
        w.recall("deployment decision")
        w.capture(content="...", title="...", type="decision")
        w.write_page(title="...", content="...")

Each instance owns one child process; reuse it for a batch of calls. Stdlib only.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

DEFAULT_EXE = Path(os.environ.get(
    "WENLAN_MCP_EXE",
    str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Wenlan" / "wenlan-mcp.exe")))

PROTOCOL_VERSION = "2025-06-18"


class WenlanUnavailable(RuntimeError):
    """Raised when the Wenlan MCP server cannot be started or answers with an error."""


def locate_exe() -> Path:
    """Find the Wenlan MCP executable (env override, then the standard install path)."""
    if DEFAULT_EXE.is_file():
        return DEFAULT_EXE
    found = shutil.which("wenlan-mcp")
    if found:
        return Path(found)
    raise WenlanUnavailable(
        f"wenlan-mcp not found at {DEFAULT_EXE}; set WENLAN_MCP_EXE")


class WenlanMCP:
    """Minimal, synchronous MCP stdio client with a single in-flight request."""

    def __init__(self, exe: Path | None = None, timeout: float = 30.0) -> None:
        self.exe = Path(exe) if exe else locate_exe()
        self.timeout = timeout
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._next_id = 0
        self._stderr_tail: list[str] = []
        self.server_info: dict = {}

    # -- lifecycle --------------------------------------------------------
    def __enter__(self) -> "WenlanMCP":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def start(self) -> None:
        if self._proc is not None:
            return
        self._proc = subprocess.Popen(
            [str(self.exe)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1)
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        init = self._rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "nexus", "version": "1.0"},
        })
        self.server_info = (init or {}).get("serverInfo") or {}
        self._notify("notifications/initialized", {})

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.stdin and proc.stdin.close()
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _drain_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        for line in proc.stderr:
            self._stderr_tail.append(line.rstrip())
            del self._stderr_tail[:-20]

    # -- json-rpc ---------------------------------------------------------
    def _notify(self, method: str, params: dict) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise WenlanUnavailable("wenlan-mcp is not running")
        self._proc.stdin.write(json.dumps(
            {"jsonrpc": "2.0", "method": method, "params": params}) + "\n")
        self._proc.stdin.flush()

    def _rpc(self, method: str, params: dict) -> dict:
        with self._lock:
            if self._proc is None or self._proc.stdin is None or self._proc.stdout is None:
                raise WenlanUnavailable("wenlan-mcp is not running")
            self._next_id += 1
            rid = self._next_id
            self._proc.stdin.write(json.dumps(
                {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}) + "\n")
            self._proc.stdin.flush()

            deadline = time.time() + self.timeout
            while time.time() < deadline:
                line = self._proc.stdout.readline()
                if not line:
                    raise WenlanUnavailable(
                        "wenlan-mcp closed the stream; stderr tail: "
                        + " | ".join(self._stderr_tail[-3:]))
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("id") != rid:
                    continue  # notification or unrelated response
                if "error" in msg:
                    err = msg["error"]
                    raise WenlanUnavailable(
                        f"{method} failed: {err.get('message') or err}")
                return msg.get("result") or {}
            raise WenlanUnavailable(f"{method} timed out after {self.timeout}s")

    # -- tools ------------------------------------------------------------
    def list_tools(self) -> list[dict]:
        return (self._rpc("tools/list", {}) or {}).get("tools") or []

    def call(self, tool: str, arguments: dict | None = None) -> Any:
        """Invoke one Wenlan tool and return its payload.

        Results can contain several text parts (a human summary plus a JSON body),
        so parsing tries the joined text, then the last part, then returns the raw
        string rather than pretending to understand something it could not parse.
        """
        result = self._rpc("tools/call", {"name": tool, "arguments": arguments or {}})
        parts = result.get("content") or []
        texts = [p.get("text", "") for p in parts
                 if isinstance(p, dict) and p.get("type") == "text" and p.get("text")]
        if result.get("isError"):
            raise WenlanUnavailable(f"{tool} returned an error: {' '.join(texts)[:300]}")
        if not texts:
            return {"structured": result.get("structuredContent")}
        for candidate in ("\n".join(texts), texts[-1]):
            try:
                return json.loads(candidate)
            except (ValueError, TypeError):
                continue
        return "\n".join(texts)

    # -- conveniences (names verified from tools/list) --------------------
    def health(self) -> dict:
        return {"server": self.server_info, "tool_count": len(self.list_tools())}

    def recall(self, query: str, **kw) -> Any:
        return self.call("recall", {"query": query, **kw})

    def capture(self, content: str, **kw) -> Any:
        return self.call("capture", {"content": content, **kw})

    def write_page(self, title: str, content: str, **kw) -> Any:
        return self.call("write_page", {"title": title, "content": content, **kw})

    def list_pages(self, **kw) -> Any:
        return self.call("list_pages", kw)

    def brief(self, **kw) -> Any:
        return self.call("brief", kw)
