"""Default tool bindings for the agentic harness engine.

Every binding is lazy and failure-tolerant: if a NEXUS surface (or one of its
heavy dependencies) is unavailable, the tool still exists and returns an
`Observation(ok=False, error=...)` instead of raising. That keeps the engine's
loop running and keeps failures visible in `EngineResult.steps` rather than
turning them into silence or, worse, an implied success.

Stdlib only at import time.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from server.local_harness.engine import Observation

logger = logging.getLogger(__name__)

ToolFn = Callable[[dict], Observation]


def _ok(content: str, artifacts: tuple[dict, ...] = ()) -> Observation:
    return Observation(ok=True, content=content, artifacts=artifacts)


def _fail(msg: str) -> Observation:
    return Observation(ok=False, error=msg)


def _artifact_from(item: dict) -> dict:
    return {
        "title": str(item.get("title") or "")[:300],
        "url": str(item.get("url") or ""),
        "snippet": str(item.get("snippet") or "")[:600],
        "channel": str(item.get("channel") or ""),
        "kind": str(item.get("kind") or "document"),
    }


def research_tool(arguments: dict) -> Observation:
    """Fan research channels out through the harness_engine orchestrator."""
    query = str(arguments.get("query") or "").strip()
    if not query:
        return _fail("missing_argument: query")
    try:
        limit = int(arguments.get("max_results") or 5)
    except (TypeError, ValueError):
        limit = 5

    try:
        from server.harness_engine.orchestrator import run_research_orchestrator
    except Exception as exc:
        return _fail(f"research_unavailable: {type(exc).__name__}: {exc}")

    try:
        data: Any = run_research_orchestrator(query, max_results=limit)
    except Exception as exc:
        return _fail(f"research_failed: {type(exc).__name__}: {exc}")

    if not isinstance(data, dict):
        return _fail("research_bad_payload")
    if data.get("error"):
        return _fail(f"research_error: {data['error']}")

    arts = tuple(_artifact_from(a) for a in (data.get("artifacts") or []) if isinstance(a, dict))
    if not arts:
        # An empty result is not an error, but it must not look like success either.
        return Observation(ok=False, content="", error="research_empty",
                           artifacts=())
    return _ok(f"{len(arts)} artifacts for {query!r}", arts)


def browser_status_tool(_arguments: dict) -> Observation:
    """Report whether a local browser harness adapter is available."""
    try:
        from server.harness_engine.orchestrator import browser_harness_status
    except Exception as exc:
        return _fail(f"browser_unavailable: {type(exc).__name__}: {exc}")
    try:
        status = browser_harness_status()
    except Exception as exc:
        return _fail(f"browser_status_failed: {type(exc).__name__}: {exc}")
    if not isinstance(status, dict):
        return _fail("browser_bad_payload")
    available = bool(status.get("available"))
    return Observation(ok=available,
                       content=str(status.get("note") or ""),
                       error=None if available else "browser_not_installed")


def echo_tool(arguments: dict) -> Observation:
    """Trivial deterministic tool; useful for smoke tests and wiring checks."""
    return _ok(str(arguments.get("text") or ""))


DEFAULT_TOOLS: dict[str, ToolFn] = {
    "research": research_tool,
    "browser_status": browser_status_tool,
    "echo": echo_tool,
}


def build_default_tools(**overrides: ToolFn) -> dict[str, ToolFn]:
    """Return the default registry, optionally overridden per tool name."""
    tools: dict[str, ToolFn] = dict(DEFAULT_TOOLS)
    tools.update(overrides)
    return tools
