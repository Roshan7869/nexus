"""Ephemeral MCP tool surface — expose only session-relevant tools to the IDE.

The IDE keeps one NEXUS MCP connection. Per task, ``task_start`` activates a
subset of tools; ``task_complete`` returns to baseline.
"""
from __future__ import annotations

BASELINE_TOOLS: frozenset[str] = frozenset({
    "task_start",
    "task_complete",
    "task_status",
    "nexus_health",
    "find_skills",
    "report_outcome",
})

# Always available on unified MCP (Phase 2)
CONTEXT_BASELINE_TOOLS: frozenset[str] = frozenset({
    "context_harness",
    "context_search",
    "context_store",
    "context_doctor",
    "harness_smart_research",
})

# Stable Shared Capability Service discovery & invocation facade (Phase 4)
CAPABILITY_BASELINE_TOOLS: frozenset[str] = frozenset({
    "capability_search",
    "capability_get",
    "skill_read",
    "capability_recommend",
    "capability_invoke",
    "capability_session_close",
})

HARNESS_PREFIX = "harness_"
CONTEXT_PREFIX = "context_"


def merge_tool_surface(
    session_tools: list[str],
    *,
    include_harness: bool = False,
    harness_tools: list[str] | None = None,
) -> list[str]:
    """Return ordered unique tool names visible for an active session."""
    seen: set[str] = set()
    ordered: list[str] = []

    def add(name: str) -> None:
        if name and name not in seen:
            seen.add(name)
            ordered.append(name)

    for name in sorted(BASELINE_TOOLS):
        add(name)

    for name in sorted(CONTEXT_BASELINE_TOOLS):
        add(name)

    for name in sorted(CAPABILITY_BASELINE_TOOLS):
        add(name)

    for name in session_tools:
        add(name)

    if include_harness and harness_tools:
        for name in harness_tools:
            add(name)

    return ordered


def filter_mcp_tool_names(
    all_tool_names: list[str],
    session_tools: list[str] | None,
    *,
    include_harness: bool = False,
    harness_tools: list[str] | None = None,
) -> list[str]:
    """Filter a full MCP tool registry to the ephemeral session surface."""
    if not session_tools:
        allowed = BASELINE_TOOLS | CONTEXT_BASELINE_TOOLS | CAPABILITY_BASELINE_TOOLS
        return sorted(n for n in all_tool_names if n in allowed)

    allowed = set(
        merge_tool_surface(session_tools, include_harness=include_harness, harness_tools=harness_tools or [])
    )
    if include_harness:
        allowed.update(n for n in all_tool_names if n.startswith(HARNESS_PREFIX))
        allowed.update(n for n in all_tool_names if n.startswith(CONTEXT_PREFIX))

    return [n for n in all_tool_names if n in allowed]