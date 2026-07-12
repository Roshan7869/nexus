"""Harness Engine metrics — FTSR, MRC, TLT, CDR.

Phase 4 dashboard primitives. Computes global and per-project efficiency
from ``routing_log``, ``tool_sessions``, and ``resources``.

Metric definitions (proxies where live schema lacks timing columns):

- **FTSR** — First-Try Success Rate (%): routes with ``outcome='correct'``
  and ``repeat_count_1h <= 1`` over all routes with explicit outcomes.
- **MRC** — Mean Reroute Count: average ``repeat_count_1h`` on closed routes;
  falls back to avg tool count per ``tool_sessions`` row.
- **TLT** — Tool Load Tax (%): share of session time spent in tool surface
  load (proxy: avg tools loaded / 8-tool budget × 100 from tool_sessions).
- **CDR** — Cold Discovery Rate (%): active resources used in last 7 days.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from server.db import db_conn, get_connection

logger = logging.getLogger(__name__)

MAX_SESSION_TOOLS = 8


def _safe_pct(num: float, den: float) -> float:
    if den <= 0:
        return 0.0
    return round(100.0 * num / den, 2)


def _avg_tools_from_json(tools_json: str) -> float:
    try:
        tools = json.loads(tools_json or "[]")
        return float(len(tools)) if isinstance(tools, list) else 0.0
    except Exception:
        return 0.0


def compute_global_metrics(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Compute FTSR/MRC/TLT/CDR across the full NEXUS DB."""
    owns = conn is None
    if owns:
        conn = get_connection()
    try:
        return _compute(conn, project=None)
    finally:
        if owns:
            conn.close()


def compute_project_metrics(project: str, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Compute harness metrics scoped to one project."""
    owns = conn is None
    if owns:
        conn = get_connection()
    try:
        return _compute(conn, project=(project or "default").strip())
    finally:
        if owns:
            conn.close()


def _compute(conn: sqlite3.Connection, project: str | None) -> dict[str, Any]:
    proj_filter = ""
    params: tuple = ()
    if project:
        proj_filter = " AND project = ?"
        params = (project,)

    # FTSR from routing_log
    ftsr_row = conn.execute(
        f"""
        SELECT
            SUM(CASE WHEN outcome = 'correct' AND COALESCE(repeat_count_1h, 0) <= 1 THEN 1 ELSE 0 END) AS first_correct,
            SUM(CASE WHEN outcome IN ('correct', 'wrong', 'partial') THEN 1 ELSE 0 END) AS closed
        FROM routing_log
        WHERE 1=1{proj_filter}
        """,
        params,
    ).fetchone()
    first_correct = int(ftsr_row["first_correct"] or 0)
    closed_routes = int(ftsr_row["closed"] or 0)

    # Session-level FTSR fallback from tool_sessions
    ts_row = conn.execute(
        f"""
        SELECT
            SUM(CASE WHEN outcome = 'correct' THEN 1 ELSE 0 END) AS ok,
            SUM(CASE WHEN status IN ('completed', 'abandoned') THEN 1 ELSE 0 END) AS done
        FROM tool_sessions
        WHERE 1=1{proj_filter}
        """,
        params,
    ).fetchone()
    ts_ok = int(ts_row["ok"] or 0)
    ts_done = int(ts_row["done"] or 0)

    if closed_routes > 0:
        ftsr = _safe_pct(first_correct, closed_routes)
        ftsr_source = "routing_log"
    elif ts_done > 0:
        ftsr = _safe_pct(ts_ok, ts_done)
        ftsr_source = "tool_sessions"
    else:
        ftsr = 0.0
        ftsr_source = "none"

    # MRC — mean reroute / tool load count
    mrc_row = conn.execute(
        f"""
        SELECT AVG(COALESCE(repeat_count_1h, 0)) AS mrc
        FROM routing_log
        WHERE outcome IN ('correct', 'wrong', 'partial'){proj_filter}
        """,
        params,
    ).fetchone()
    mrc = float(mrc_row["mrc"] or 0.0)
    mrc_source = "routing_log.repeat_count_1h"

    if mrc <= 0:
        tools_rows = conn.execute(
            f"SELECT tools_json FROM tool_sessions WHERE 1=1{proj_filter}",
            params,
        ).fetchall()
        if tools_rows:
            avg_tools = sum(_avg_tools_from_json(r["tools_json"]) for r in tools_rows) / len(tools_rows)
            mrc = round(max(1.0, avg_tools), 3)
            mrc_source = "tool_sessions.tools_json"

    mrc = round(max(mrc, 1.0), 3)

    # TLT — tool load tax (% of 8-tool budget)
    tools_rows = conn.execute(
        f"SELECT tools_json FROM tool_sessions WHERE 1=1{proj_filter}",
        params,
    ).fetchall()
    if tools_rows:
        avg_tools = sum(_avg_tools_from_json(r["tools_json"]) for r in tools_rows) / len(tools_rows)
        tlt = round(min(100.0, avg_tools / MAX_SESSION_TOOLS * 100.0), 2)
        tlt_source = "tool_sessions.tools_json"
    else:
        # fallback: matched_skills array length in routing_log
        skill_rows = conn.execute(
            f"SELECT matched_skills FROM routing_log WHERE matched_skills IS NOT NULL{proj_filter} LIMIT 500",
            params,
        ).fetchall()
        lengths = []
        for r in skill_rows:
            try:
                skills = json.loads(r["matched_skills"] or "[]")
                if isinstance(skills, list):
                    lengths.append(len(skills))
            except Exception:
                pass
        if lengths:
            tlt = round(min(100.0, (sum(lengths) / len(lengths)) / MAX_SESSION_TOOLS * 100.0), 2)
            tlt_source = "routing_log.matched_skills"
        else:
            tlt = 0.0
            tlt_source = "none"

    # CDR — cold discovery rate (resources active in last 7 days)
    cdr_row = conn.execute(
        """
        SELECT
            SUM(CASE WHEN last_used_at IS NOT NULL
                      AND last_used_at >= datetime('now', '-7 days') THEN 1 ELSE 0 END) AS warm,
            COUNT(*) AS total
        FROM resources
        WHERE status = 'active'
        """
    ).fetchone()
    warm = int(cdr_row["warm"] or 0)
    total_active = int(cdr_row["total"] or 0)
    cdr = _safe_pct(warm, total_active)

    # Efficiency summary
    outcome_row = conn.execute(
        f"""
        SELECT
            SUM(CASE WHEN outcome = 'correct' THEN 1 ELSE 0 END) AS correct,
            SUM(CASE WHEN outcome = 'wrong' THEN 1 ELSE 0 END) AS wrong,
            COUNT(*) AS total
        FROM routing_log
        WHERE outcome IN ('correct', 'wrong', 'partial'){proj_filter}
        """,
        params,
    ).fetchone()
    correct = int(outcome_row["correct"] or 0)
    wrong = int(outcome_row["wrong"] or 0)
    routed_total = int(outcome_row["total"] or 0)
    harness_efficiency = round(correct / max(correct + wrong, 1), 4)

    active_sessions = int(conn.execute(
        f"SELECT COUNT(*) FROM tool_sessions WHERE status = 'active'{proj_filter}",
        params,
    ).fetchone()[0])

    return {
        "project": project or "all",
        "ftsr": ftsr,
        "mrc": mrc,
        "tlt": tlt,
        "cdr": cdr,
        "harness_efficiency": harness_efficiency,
        "routing_accuracy_pct": _safe_pct(correct, routed_total),
        "active_tool_sessions": active_sessions,
        "closed_routes": closed_routes,
        "sources": {
            "ftsr": ftsr_source,
            "mrc": mrc_source,
            "tlt": tlt_source,
            "cdr": "resources.last_used_at",
        },
    }


def build_harness_dashboard(project: str | None = None) -> dict[str, Any]:
    """Full dashboard payload for ``/nexus/harness/dashboard``."""
    from server.ide_manager import get_manager

    ide_mgr = get_manager()
    global_metrics = compute_global_metrics()

    projects: dict[str, Any] = {}
    try:
        with db_conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT project FROM tool_sessions "
                "UNION SELECT DISTINCT project FROM routing_log"
            ).fetchall()
            for row in rows:
                p = row["project"] or "default"
                projects[p] = compute_project_metrics(p, conn=conn)
    except Exception as exc:
        logger.debug("per-project metrics failed: %s", exc)

    if project:
        scoped = projects.get(project) or compute_project_metrics(project)
    else:
        scoped = global_metrics

    ide_dashboard = ide_mgr.get_dashboard(include_metrics=True)

    return {
        "status": "ok",
        "global": global_metrics,
        "scoped": scoped,
        "projects": projects,
        "ide_sessions": ide_dashboard,
        "summary": {
            "ftsr": global_metrics["ftsr"],
            "mrc": global_metrics["mrc"],
            "tlt": global_metrics["tlt"],
            "cdr": global_metrics["cdr"],
            "active_ide_sessions": ide_dashboard.get("total_sessions", 0),
            "active_tool_sessions": global_metrics.get("active_tool_sessions", 0),
        },
    }


def session_metrics_from_counters(
    *,
    tools_routed: int,
    tools_succeeded: int,
    tools_failed: int,
    routing_time_ms: int = 0,
    total_time_ms: int = 0,
    tools_loaded: int = 0,
) -> dict[str, float]:
    """Derive per-IDE-session FTSR/MRC/TLT/CDR from live counters."""
    resolved = max(tools_succeeded + tools_failed, 0)
    ftsr = round(100.0 * tools_succeeded / max(tools_routed, 1), 2)
    mrc = round(max(1.0, tools_routed / max(resolved, 1)), 3)
    if total_time_ms > 0 and routing_time_ms > 0:
        tlt = round(min(100.0, routing_time_ms / total_time_ms * 100.0), 2)
    else:
        loaded = tools_loaded or tools_routed
        tlt = round(min(100.0, loaded / MAX_SESSION_TOOLS * 100.0), 2)
    return {"ftsr": ftsr, "mrc": mrc, "tlt": tlt, "cdr": 0.0}