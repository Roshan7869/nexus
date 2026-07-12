"""P4 Execution plane — stable session contract for AgentRouter / Hermes / CodeNomad.

Contract (version 1):
  POST /v1/session/open   {task, project, budget, ide}
    → {session_id, tools[], harness_channels[], harness_tools[], model_hint, ...}
  POST /v1/session/{id}/report  {outcome, quality?, notes?}
    → {status, outcome, belief_delta, ...}
  GET  /v1/health
    → {version, git_sha, router_mode, db_ok, flags}

This module is the single implementation; HTTP + MCP + CLI clients wrap it.
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

NEXUS_ROOT = Path(__file__).resolve().parent.parent
API_VERSION = "1"
CONTRACT = "nexus.execution_plane.v1"


def _git_sha() -> str:
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=str(NEXUS_ROOT),
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            .decode()
            .strip()
        )
    except Exception:
        return "unknown"


def _sanitize_ide(ide: str | None) -> str:
    allowed = {
        "opencode", "claude-code", "unknown", "hermes", "codex",
        "mcp", "auto_invoke", "http",
    }
    raw = (ide or "http").strip().lower()
    aliases = {
        "claude": "claude-code",
        "claude_code": "claude-code",
        "agent-space": "http",
        "agentspace": "http",
        "codenomad": "opencode",
        "open-code": "opencode",
    }
    raw = aliases.get(raw, raw)
    return raw if raw in allowed else "unknown"


def _belief_snapshot(skill_names: list[str]) -> dict[str, dict[str, float]]:
    """Aggregated alpha/beta for resource-level Thompson beliefs (provider=nexus).

    Sums across task_type rows so deltas are visible regardless of which
    task_type the latest update hit.
    """
    out: dict[str, dict[str, float]] = {}
    if not skill_names:
        return out
    try:
        from server.db import db_conn
    except Exception:
        return out
    try:
        with db_conn() as conn:
            for name in skill_names[:12]:
                key = f"resource:{name}"
                row = conn.execute(
                    """
                    SELECT COALESCE(SUM(alpha),0), COALESCE(SUM(beta),0),
                           COALESCE(SUM(total_requests),0)
                    FROM model_beliefs
                    WHERE provider='nexus' AND model=?
                    """,
                    (key,),
                ).fetchone()
                if row and (row[0] or row[1] or row[2]):
                    out[name] = {
                        "alpha": float(row[0] or 0),
                        "beta": float(row[1] or 0),
                        "total_requests": float(row[2] or 0),
                    }
                else:
                    out[name] = {"alpha": 0.0, "beta": 0.0, "total_requests": 0.0}
    except Exception as e:
        logger.debug("belief snapshot failed: %s", e)
    return out


def _belief_delta(
    before: dict[str, dict[str, float]],
    after: dict[str, dict[str, float]],
) -> list[dict[str, Any]]:
    deltas = []
    keys = set(before) | set(after)
    for k in keys:
        b = before.get(k, {})
        a = after.get(k, {})
        if not a and not b:
            continue
        da = (a.get("alpha", 1.0) - b.get("alpha", 1.0))
        db = (a.get("beta", 1.0) - b.get("beta", 1.0))
        if abs(da) > 1e-9 or abs(db) > 1e-9:
            deltas.append({
                "skill": k,
                "alpha_before": b.get("alpha"),
                "alpha_after": a.get("alpha"),
                "beta_before": b.get("beta"),
                "beta_after": a.get("beta"),
                "delta_alpha": round(da, 4),
                "delta_beta": round(db, 4),
            })
    return deltas


def health_v1() -> dict[str, Any]:
    """GET /v1/health payload."""
    from server.router_cutover import get_router_status

    db_ok = True
    try:
        from server.db import db_conn
        with db_conn() as conn:
            conn.execute("SELECT 1").fetchone()
    except Exception:
        db_ok = False

    rstatus = get_router_status()
    return {
        "status": "ok" if db_ok else "degraded",
        "version": API_VERSION,
        "contract": CONTRACT,
        "git_sha": _git_sha(),
        "router_mode": rstatus.get("mode"),
        "router_flag": rstatus.get("use_unified_router"),
        "db_ok": db_ok,
        "flags": {
            "USE_UNIFIED_ROUTER": os.environ.get("USE_UNIFIED_ROUTER", rstatus.get("default_flag")),
            "NEXUS_CLUSTER_ROUTING": os.environ.get("NEXUS_CLUSTER_ROUTING"),
            "NEXUS_THOMPSON_ROUTING": os.environ.get("NEXUS_THOMPSON_ROUTING"),
            "NEXUS_REFLECTION": os.environ.get("NEXUS_REFLECTION"),
            "NEXUS_HARNESS_EAGER": os.environ.get("NEXUS_HARNESS_EAGER", "false"),
        },
        "endpoints": {
            "session_open": "POST /v1/session/open",
            "session_report": "POST /v1/session/{session_id}/report",
            "session_get": "GET /v1/session/{session_id}",
            "health": "GET /v1/health",
        },
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def open_session(
    task: str,
    project: str = "default",
    *,
    budget: int = 8,
    ide: str = "http",
    use_embedding: bool = True,
    ide_session_id: str | None = None,
) -> dict[str, Any]:
    """Open an execution session: route + load tools + harness plan."""
    task = (task or "").strip()
    if not task:
        raise ValueError("task is required")
    project = (project or "default").strip()
    ide = _sanitize_ide(ide)
    budget = max(1, min(int(budget or 8), 12))

    # 1) Production cutover route (pure session log outcome=routed)
    from server.router_cutover import route_for_task
    route = route_for_task(
        task, project, max_results=budget,
        open_session=True, ide=ide,
    )

    # 2) Ephemeral tool session (learning on report only)
    from server.tool_session_broker import get_tool_session_broker
    broker = get_tool_session_broker()
    # Prefer tools from route skills names
    session = broker.start_session(
        project=project,
        query=task,
        ide=ide,
        use_embedding=use_embedding,
        max_tools=budget,
        ide_session_id=ide_session_id,
    )

    # Prefer cutover ranking order when present
    skill_names = [
        s.get("name") for s in (route.get("skills") or [])
        if isinstance(s, dict) and s.get("name")
    ]
    if skill_names:
        session.tools_loaded = skill_names[:budget]
        try:
            broker._update_persisted_session(session)  # type: ignore[attr-defined]
            broker._active[session.session_id] = session
        except Exception:
            pass

    harness_tools = route.get("harness_tools") or session.harness_tools
    if route.get("harness_tools"):
        session.harness_tools = list(route["harness_tools"])[:16]

    payload = {
        "contract": CONTRACT,
        "version": API_VERSION,
        "session_id": session.session_id,
        "project": project,
        "task": task,
        "ide": ide,
        "status": "active",
        "tools": session.tools_loaded,
        "skills": route.get("skills") or [],
        "budget": budget,
        "model_hint": route.get("model") or session.model,
        "cluster": route.get("cluster") or session.cluster,
        "task_type": route.get("task_type") or session.task_type,
        "harness_channels": route.get("harness_channels") or [],
        "harness_tools": harness_tools,
        "toolsets": route.get("toolsets") or [],
        "harness_intent": route.get("harness_intent"),
        "harness_plan": route.get("harness_plan"),
        "research_artifacts": route.get("research_artifacts") or [],
        "context_block": session.context_block,
        "router_mode": route.get("router_mode"),
        "router_flag": route.get("router_flag"),
        "routing_log_id": route.get("session_log_id"),
        "mcp_proxy": broker.get_mcp_proxy_config(session.session_id),
        "instructions": (
            "Use only tools listed in this session. "
            "When finished call POST /v1/session/{session_id}/report "
            "with outcome in {correct,wrong,partial,skip}."
        ),
    }
    return payload


def report_session(
    session_id: str,
    outcome: str,
    *,
    notes: str = "",
    quality: float = 1.0,
    skill_name: str | None = None,
    ide_session_id: str | None = None,
) -> dict[str, Any]:
    """Close session with terminal outcome and return belief deltas."""
    from server.tool_session_broker import get_tool_session_broker
    from server.learning import record_outcome, TERMINAL_OUTCOMES

    broker = get_tool_session_broker()
    session = broker.get_session(session_id)
    if session is None:
        raise KeyError(f"unknown session: {session_id}")

    o = (outcome or "").strip().lower()
    if o in {"success", "passed", "done", "complete", "completed"}:
        o = "correct"
    if o in {"failed", "fail", "error"}:
        o = "wrong"
    if o in {"mixed"}:
        o = "partial"
    if o not in TERMINAL_OUTCOMES and o != "skip":
        # allow skip as terminal no-op for learning
        if o not in {"skip"}:
            raise ValueError(
                f"outcome must be terminal: correct|wrong|partial|skip (got {outcome})"
            )

    tools = list(session.tools_loaded or [])
    if skill_name and skill_name not in tools:
        tools = [skill_name] + tools

    before = _belief_snapshot(tools)

    # Primary skill learning (first tool / explicit).
    # skip closes session without belief update (not a quality signal).
    primary = skill_name or (tools[0] if tools else None)
    learning_results = []
    learnable = frozenset({"correct", "wrong", "partial"})
    if primary and o in learnable:
        # Gap 3: pass full tool bundle so co_triggered edges learn multi-skill sessions
        co = [t for t in tools if t and t != primary]
        learning_results.append(
            record_outcome(
                skill_name=primary,
                outcome=o,
                task_summary=session.query[:200],
                project=session.project,
                ide=session.ide,
                quality=float(quality or 1.0),
                co_skills=co,
            )
        )

    # Broker dump archives context; learning already applied on primary above.
    end = broker.end_session(
        session_id,
        outcome=o if o != "skip" else "partial",
        summary=notes or "",
        ide_session_id=ide_session_id,
        record_learning=False,
    )

    after = _belief_snapshot(tools)
    deltas = _belief_delta(before, after)

    return {
        "contract": CONTRACT,
        "version": API_VERSION,
        "session_id": session_id,
        "status": end.get("status", "dumped"),
        "outcome": o,
        "primary_skill": primary,
        "tools": tools,
        "learning": learning_results,
        "belief_delta": deltas,
        "belief_updated": len(deltas) > 0,
        "notes": notes,
        "quality": quality,
    }


def get_session(session_id: str) -> dict[str, Any]:
    from server.tool_session_broker import get_tool_session_broker

    broker = get_tool_session_broker()
    session = broker.get_session(session_id)
    if session is None:
        raise KeyError(f"unknown session: {session_id}")
    data = session.to_dict()
    data["contract"] = CONTRACT
    data["mcp_proxy"] = broker.get_mcp_proxy_config(session_id)
    return data
