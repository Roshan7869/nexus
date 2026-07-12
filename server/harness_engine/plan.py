"""Build a harness plan for a task (channels + toolsets, no network)."""
from __future__ import annotations

from server.harness_engine.models import HarnessPlan
from server.harness_engine.toolsets import (
    detect_intent,
    plan_toolsets,
    tools_for_toolsets,
)


def plan_harness(task: str, *, execute_research: bool = False) -> HarnessPlan:
    intent = detect_intent(task)
    toolsets = plan_toolsets(task)
    channels: list[str] = []
    reason_parts: list[str] = [f"intent={intent}"]

    if intent == "research":
        channels = ["research"]
        reason_parts.append("load research toolset; artifacts via orchestrator")
    elif intent == "browse":
        channels = ["browser"]
        reason_parts.append("load browser toolset / browser-harness adapter")
    elif intent == "memory":
        channels = ["memory"]
    else:
        channels = []

    # Memory channel available lightly whenever memory toolset present
    if "memory" in toolsets and "memory" not in channels:
        channels.append("memory")

    return HarnessPlan(
        intent=intent,
        channels=channels,
        toolsets=toolsets,
        tools=tools_for_toolsets(toolsets),
        execute_research=bool(execute_research and intent == "research"),
        reason="; ".join(reason_parts),
    )
