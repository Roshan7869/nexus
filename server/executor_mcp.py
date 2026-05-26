"""MCP bridge for the NEXUS Plan Executor — exposes create/resume/checkpoint as tools.

Usage:
    # In the IDE, call:
    from server.executor_mcp import create_plan, get_next_phase, complete_phase, resume_plan

    # Or via NEXUS MCP:
    tool_call("nexus_create_plan", {"goal": "...", "phases": [...]})
    tool_call("nexus_resume_plan", {"plan_id": 42})
"""

import json
import logging
from typing import Dict, List, Optional, Any

from server.nexus_plan_executor import PlanExecutor

logger = logging.getLogger(__name__)

_executor = None


def _get_executor() -> PlanExecutor:
    global _executor
    if _executor is None:
        _executor = PlanExecutor()
    return _executor


def nexus_create_plan(goal: str, project: str = "default",
                      phases: List[Dict] = None,
                      plan_type: str = "adaptive") -> Dict:
    """Create a plan and return plan_id + first unit of work.
    
    This is the ENTRY POINT for any graphify-based task tracking.
    The plan is stored in NEXUS DB and survives context resets.

    Args:
        goal: High-level goal description
        project: Project name
        phases: List of phase dicts with id, type, name, inputs, outputs, guardrails
        plan_type: adaptive | manual | autopilot

    Returns:
        Dict with plan_id, next_phase info, and checkpoint token
    """
    ex = _get_executor()
    plan_id = ex.create_plan(goal, project, phases, plan_type)
    ex.update_plan_status(plan_id, "active")

    # Create task_run for broker compatibility
    task_id = ex.create_task_run(goal, project, plan_id)
    ex.update_task_run(task_id, plan_json={
        "plan_id": plan_id,
        "phases": phases,
        "goal": goal,
    })

    next_phase = ex.get_next_phase(plan_id)
    checkpoint = ex.checkpoint(plan_id)

    return {
        "status": "created",
        "plan_id": plan_id,
        "task_id": task_id,
        "next_phase": next_phase,
        "checkpoint": checkpoint,
        "resume_token": checkpoint.get("resume_token"),
        "message": f"Plan {plan_id} created with {len(phases or [])} phases. "
                   f"OOC/OOM-safe: checkpoint stored at /tmp/nexus-plan-checkpoint-{plan_id}.json",
    }


def nexus_get_next_phase(plan_id: int) -> Dict:
    """Get the next unit of work. Returns None if plan complete.
    
    Each call returns ONE phase. The IDE executes it, then calls
    complete_phase. This keeps context small — no full plan in memory.
    """
    ex = _get_executor()
    next_phase = ex.get_next_phase(plan_id)
    if not next_phase:
        plan = ex.get_plan(plan_id)
        return {
            "status": "completed" if plan and plan["status"] == "completed" else "blocked",
            "plan_id": plan_id,
            "message": "All phases complete" if plan and plan["status"] == "completed" else "No ready phases (dependencies not met)",
        }
    return {
        "status": "ready",
        "plan_id": plan_id,
        "phase": next_phase["phase"],
        "phase_id": next_phase["phase_id"],
        "progress": next_phase["progress"],
        "plan_goal": next_phase["plan_goal"],
    }


def nexus_complete_phase(plan_id: int, phase_id: str,
                         outcome: str = "correct",
                         tokens_used: int = 0,
                         skills_used: List[str] = None,
                         artifacts: List[str] = None,
                         notes: str = "") -> Dict:
    """Mark a phase complete and advance the plan.
    
    Call this after each phase. Returns the NEXT phase so the IDE
    can immediately continue without re-querying.
    """
    ex = _get_executor()
    ex.complete_phase(plan_id, phase_id, outcome, tokens_used, skills_used, notes)
    for a in (artifacts or []):
        ex.add_artifact(plan_id, a)

    plan = ex.get_plan(plan_id)
    is_done = plan and plan["status"] == "completed"

    if is_done:
        return {
            "status": "plan_complete",
            "plan_id": plan_id,
            "message": "All phases completed successfully",
        }

    next_phase = ex.get_next_phase(plan_id)
    checkpoint = ex.checkpoint(plan_id)
    return {
        "status": "phase_complete",
        "plan_id": plan_id,
        "completed_phase": phase_id,
        "outcome": outcome,
        "next_phase": next_phase,
        "checkpoint": checkpoint,
    }


def nexus_fail_phase(plan_id: int, phase_id: str,
                     error: str = "", retry: bool = True) -> Dict:
    """Mark a phase as failed, optionally queue retry."""
    ex = _get_executor()
    ex.fail_phase(plan_id, phase_id, error)
    if retry:
        ex.retry_phase(plan_id, phase_id)
        next_phase = ex.get_next_phase(plan_id)
        return {
            "status": "queued_for_retry",
            "plan_id": plan_id,
            "failed_phase": phase_id,
            "next_phase": next_phase,
            "message": f"Phase {phase_id} queued for retry",
        }
    return {
        "status": "failed",
        "plan_id": plan_id,
        "failed_phase": phase_id,
    }


def nexus_resume_plan(plan_id: int = None,
                      checkpoint_path: str = None) -> Dict:
    """Resume a plan after OOC/OOM or session restart.
    
    This is the RECOVERY ENTRY POINT. Call this when:
    - Context was reset (OOC)
    - Memory was exhausted (OOM)
    - Session was interrupted
    - Need to pick up where left off

    The plan state is loaded from NEXUS DB, NOT from context.
    Zero context required to resume.
    """
    ex = _get_executor()
    result = ex.resume_plan(plan_id, checkpoint_path)

    if result is None:
        return {
            "status": "error",
            "message": f"Plan {plan_id} not found",
        }

    if result.get("status") == "completed":
        return result

    # Create fresh checkpoint
    cp = ex.checkpoint(result["plan_id"])
    result["checkpoint"] = cp
    return result


def nexus_plan_status(plan_id: int) -> Dict:
    """Get current plan status without advancing."""
    ex = _get_executor()
    plan = ex.get_plan(plan_id)
    if not plan:
        return {"status": "error", "message": f"Plan {plan_id} not found"}
    completed = json.loads(plan["phases_completed"] or "[]")
    remaining = json.loads(plan["phases_remaining"] or "[]")
    phases = json.loads(plan["phases_json"] or "[]")
    return {
        "plan_id": plan_id,
        "goal": plan["goal"],
        "status": plan["status"],
        "progress": {
            "completed": completed,
            "remaining": remaining,
            "total": len(phases),
            "pct": round(len(completed) / max(len(phases), 1) * 100, 1),
        },
        "artifacts": json.loads(plan["artifacts"] or "[]"),
    }


def nexus_begin_subtask(plan_id: int, phase_id: str, subtask_name: str,
                        resource_name: str = "") -> Dict:
    """Begin tracking a subtask within a phase."""
    ex = _get_executor()
    trace_id = ex.begin_subtask(plan_id, phase_id, subtask_name, resource_name)
    return {
        "status": "tracking",
        "plan_id": plan_id,
        "phase_id": phase_id,
        "subtask": subtask_name,
        "trace_id": trace_id,
    }


def nexus_complete_subtask(plan_id: int, phase_id: str, subtask_name: str,
                           outcome: str = "correct", notes: str = "") -> Dict:
    """Complete a subtask with outcome."""
    ex = _get_executor()
    ex.complete_subtask(plan_id, phase_id, subtask_name, outcome, notes)
    return {"status": "completed", "subtask": subtask_name, "outcome": outcome}


def nexus_list_plans(status: str = None, limit: int = 10) -> Dict:
    """List all plans, optionally filtered by status."""
    ex = _get_executor()
    plans = ex.list_plans(status, limit)
    stats = ex.get_stats()
    return {"plans": plans, "stats": stats}


def nexus_get_executor_stats() -> Dict:
    """Get plan executor statistics."""
    ex = _get_executor()
    return ex.get_stats()


def get_mcp_tool_definitions() -> List[Dict]:
    """Return MCP tool definitions for auto-registration."""
    return [
        {
            "name": "nexus_create_plan",
            "description": "Create a persistent plan with OOC/OOM-safe checkpoint. "
                           "Plan stored in NEXUS DB, returns plan_id for resume.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "description": "Plan goal"},
                    "project": {"type": "string", "default": "default"},
                    "phases": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "List of phase dicts with id, type, name, inputs, outputs",
                    },
                },
                "required": ["goal"],
            },
        },
        {
            "name": "nexus_resume_plan",
            "description": "Resume a plan after OOC/OOM/session-restart. "
                           "Loads from DB, returns next phase to execute. "
                           "Zero context required.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "integer", "description": "Plan ID to resume"},
                    "checkpoint_path": {
                        "type": "string",
                        "description": "Path to checkpoint file (alternative to plan_id)",
                    },
                },
            },
        },
        {
            "name": "nexus_get_next_phase",
            "description": "Get the next phase to execute. Returns one unit of work.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "integer"},
                },
                "required": ["plan_id"],
            },
        },
        {
            "name": "nexus_complete_phase",
            "description": "Mark current phase complete, advance to next. Returns next phase.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "integer"},
                    "phase_id": {"type": "string"},
                    "outcome": {"type": "string", "enum": ["correct", "wrong", "partial"]},
                    "tokens_used": {"type": "integer"},
                    "artifacts": {"type": "array", "items": {"type": "string"}},
                    "notes": {"type": "string"},
                },
                "required": ["plan_id", "phase_id"],
            },
        },
        {
            "name": "nexus_fail_phase",
            "description": "Mark a phase as failed, optionally retry.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "integer"},
                    "phase_id": {"type": "string"},
                    "error": {"type": "string"},
                    "retry": {"type": "boolean", "default": True},
                },
                "required": ["plan_id", "phase_id"],
            },
        },
        {
            "name": "nexus_plan_status",
            "description": "Get plan progress without advancing.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "integer"},
                },
                "required": ["plan_id"],
            },
        },
        {
            "name": "nexus_begin_subtask",
            "description": "Start tracking a subtask within a phase.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "integer"},
                    "phase_id": {"type": "string"},
                    "subtask_name": {"type": "string"},
                    "resource_name": {"type": "string"},
                },
                "required": ["plan_id", "phase_id", "subtask_name"],
            },
        },
        {
            "name": "nexus_complete_subtask",
            "description": "Mark a subtask complete.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "integer"},
                    "phase_id": {"type": "string"},
                    "subtask_name": {"type": "string"},
                    "outcome": {"type": "string", "enum": ["correct", "wrong"]},
                },
                "required": ["plan_id", "phase_id", "subtask_name"],
            },
        },
        {
            "name": "nexus_list_plans",
            "description": "List all plans filtered by status.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["planning", "active", "paused", "completed", "failed"],
                    },
                    "limit": {"type": "integer", "default": 10},
                },
            },
        },
    ]
