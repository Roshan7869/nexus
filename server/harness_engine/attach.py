"""Attach harness plan (+ optional research artifacts) to a routing result."""
from __future__ import annotations

import logging
from typing import Any

from server.harness_engine.plan import plan_harness
from server.harness_engine.orchestrator import run_research_orchestrator

logger = logging.getLogger(__name__)


def attach_harness_to_result(
    result: dict[str, Any],
    task: str,
    *,
    execute_research: bool = False,
) -> dict[str, Any]:
    """Mutate/return route result with harness_plan, toolsets, tools, artifacts.

    Never adds paper titles into result['skills'].
    """
    plan = plan_harness(task, execute_research=execute_research)
    result["harness_plan"] = plan.to_dict()
    result["toolsets"] = plan.toolsets
    result["harness_tools"] = plan.tools
    result["harness_channels"] = plan.channels
    result["harness_intent"] = plan.intent

    # Strip any legacy skill rows that were paper titles from harness_research
    skills = result.get("skills") or []
    cleaned = [
        s for s in skills
        if not (isinstance(s, dict) and s.get("_source") == "harness_research")
    ]
    if len(cleaned) != len(skills):
        result["skills"] = cleaned
        result.setdefault("pipeline", [])
        if isinstance(result["pipeline"], list):
            result["pipeline"] = [
                p for p in result["pipeline"] if p != "harness_research"
            ] + ["harness_artifacts_detached"]

    if plan.execute_research or (execute_research and plan.intent == "research"):
        try:
            research = run_research_orchestrator(task, max_results=3, max_artifacts=12)
            result["research_artifacts"] = research.get("artifacts") or []
            result["research_meta"] = {
                "channels": research.get("channels"),
                "latency_ms": research.get("latency_ms"),
                "n_artifacts": research.get("n_artifacts"),
                "error": research.get("error"),
                "source": research.get("source"),
            }
            pipe = result.get("pipeline")
            if isinstance(pipe, list) and "harness_research_orchestrator" not in pipe:
                pipe.append("harness_research_orchestrator")
        except Exception as e:
            logger.debug("research orchestrator failed: %s", e)
            result["research_artifacts"] = []
            result["research_meta"] = {"error": str(e)}
    else:
        result.setdefault("research_artifacts", [])

    return result
