"""Phase 5 — RL loop closure: auto task_complete, reflection retry, distillation.

Closes the feedback loop without manual ``report_outcome``:
  - IDE events ``task_completed`` / ``test_passed`` → auto ``task_complete`` + beliefs
  - ``test_failed`` → reflection + alternative skill suggestion
  - Weekly-style project distillation → ``search_learnings(project=...)``
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections import Counter
from typing import Any

from server.db import db_conn, get_connection

logger = logging.getLogger(__name__)

SUCCESS_EVENTS = frozenset({
    "task_completed", "test_passed", "build_passed",
    "code_accepted", "edit_accepted", "lint_passed",
})
FAILURE_EVENTS = frozenset({
    "test_failed", "build_failed", "task_abandoned",
    "code_rejected", "edit_rejected", "lint_failed", "error",
})

OUTCOME_MAP = {
    "task_completed": "correct",
    "test_passed": "correct",
    "build_passed": "correct",
    "code_accepted": "correct",
    "edit_accepted": "correct",
    "lint_passed": "correct",
    "test_failed": "wrong",
    "build_failed": "wrong",
    "task_abandoned": "wrong",
    "code_rejected": "wrong",
    "edit_rejected": "partial",
    "lint_failed": "partial",
    "error": "wrong",
    "timeout": "partial",
}


def get_feedback_coverage(conn=None) -> dict[str, Any]:
    """Outcome coverage stats for routing_log + tool_sessions."""
    owns = conn is None
    if owns:
        conn = get_connection()
    try:
        total_routes = conn.execute("SELECT COUNT(*) FROM routing_log").fetchone()[0]
        with_outcome = conn.execute(
            "SELECT COUNT(*) FROM routing_log WHERE outcome IN ('correct','wrong','partial')"
        ).fetchone()[0]
        null_outcome = conn.execute(
            "SELECT COUNT(*) FROM routing_log WHERE outcome IS NULL OR outcome IN ('routed','pending')"
        ).fetchone()[0]
        efc_rows = conn.execute(
            "SELECT COUNT(*) FROM outcome_log WHERE efc_score IS NOT NULL"
        ).fetchone()[0]
        total_outcome_log = conn.execute("SELECT COUNT(*) FROM outcome_log").fetchone()[0]
        ts_done = conn.execute(
            "SELECT COUNT(*) FROM tool_sessions WHERE status IN ('completed','abandoned')"
        ).fetchone()[0]
        ts_correct = conn.execute(
            "SELECT COUNT(*) FROM tool_sessions WHERE outcome = 'correct'"
        ).fetchone()[0]
        return {
            "routing_log_total": total_routes,
            "routing_log_with_outcome": with_outcome,
            "routing_log_pending": null_outcome,
            "routing_coverage_pct": round(100.0 * with_outcome / max(total_routes, 1), 2),
            "outcome_log_efc_populated": efc_rows,
            "outcome_log_total": total_outcome_log,
            "efc_coverage_pct": round(100.0 * efc_rows / max(total_outcome_log, 1), 2),
            "tool_sessions_completed": ts_done,
            "tool_sessions_correct": ts_correct,
            "session_success_pct": round(100.0 * ts_correct / max(ts_done, 1), 2),
        }
    finally:
        if owns:
            conn.close()


def _persist_ide_event(
    event_type: str,
    *,
    ide: str,
    model: str,
    provider: str,
    task_type: str,
    reward: float,
    metadata: dict,
) -> int | None:
    """Gap 4: always write to resource-brain ide_events (not only LLM gateway)."""
    try:
        with db_conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO ide_events
                  (event_type, ide, model, provider, task_type, reward, metadata)
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    event_type,
                    ide or "unknown",
                    model or "unknown",
                    provider or "unknown",
                    task_type or "general",
                    float(reward or 0.0),
                    json.dumps(metadata or {}),
                ),
            )
            return int(cur.lastrowid) if cur.lastrowid else None
    except Exception as exc:
        logger.warning("ide_events insert failed: %s", exc)
        return None


def ide_event_stats(conn=None) -> dict[str, Any]:
    """Gap 4 ops metrics for IDE feedback loop health."""
    owns = conn is None
    if owns:
        conn = get_connection()
    try:
        total = conn.execute("SELECT COUNT(*) FROM ide_events").fetchone()[0]
        # Exclude obvious unit/fixture noise (model=test-model, ide=test, empty metadata)
        real = conn.execute(
            """
            SELECT COUNT(*) FROM ide_events
            WHERE COALESCE(model,'') NOT IN ('test', 'test-model', 'unknown')
              AND COALESCE(ide,'') NOT IN ('test', '')
              AND created_at >= datetime('now', '-30 days')
            """
        ).fetchone()[0]
        last_7d = conn.execute(
            "SELECT COUNT(*) FROM ide_events WHERE created_at >= datetime('now', '-7 days')"
        ).fetchone()[0]
        by_type = {
            r[0]: r[1]
            for r in conn.execute(
                "SELECT event_type, COUNT(*) FROM ide_events GROUP BY 1 ORDER BY 2 DESC"
            ).fetchall()
        }
        # Gate: endpoint path works if we have any real events in 30d OR total grew past fixtures
        # Soft target: ≥5 non-fixture events in 30d (bootstrap may use synthetic smoke)
        return {
            "ide_events_total": total,
            "ide_events_real_30d": real,
            "ide_events_7d": last_7d,
            "by_type": by_type,
            "ide_event_loop_gate_pass": real >= 5 or last_7d >= 3,
        }
    finally:
        if owns:
            conn.close()


def handle_ide_event(
    event_type: str,
    *,
    ide: str = "unknown",
    project: str = "default",
    task_type: str = "general",
    metadata: dict | None = None,
    model: str = "unknown",
    provider: str = "unknown",
    reward: float | None = None,
) -> dict[str, Any]:
    """Process an IDE feedback event — auto-close sessions and update beliefs."""
    metadata = metadata or {}
    project = (metadata.get("project") or project or "default").strip()
    event_type = (event_type or "").strip()

    # Default reward from event class
    if reward is None:
        if event_type in SUCCESS_EVENTS:
            reward = 1.0
        elif event_type in FAILURE_EVENTS:
            reward = -0.5
        else:
            reward = 0.0

    result: dict[str, Any] = {
        "event_type": event_type,
        "project": project,
        "ide": ide,
        "handled": False,
    }

    event_id = _persist_ide_event(
        event_type,
        ide=ide,
        model=model,
        provider=provider,
        task_type=task_type,
        reward=float(reward),
        metadata={**metadata, "project": project},
    )
    result["ide_event_id"] = event_id

    if event_type in SUCCESS_EVENTS:
        result.update(_handle_success_event(
            event_type, project=project, ide=ide, metadata=metadata,
        ))
        result["handled"] = True
    elif event_type in FAILURE_EVENTS:
        result.update(_handle_failure_event(
            event_type, project=project, ide=ide, metadata=metadata,
        ))
        result["handled"] = True
    else:
        result["status"] = "ignored"
        result["reason"] = "event_type not in success/failure sets"

    # Thompson / record_outcome bridge (always attempt — not gated on env flag)
    outcome = OUTCOME_MAP.get(event_type)
    if outcome:
        _record_beliefs_from_event(
            event_type, outcome, project, ide, metadata, model, provider, task_type,
        )
        result["outcome_recorded"] = outcome

    result["coverage"] = get_feedback_coverage()
    result["ide_stats"] = ide_event_stats()
    return result


def get_tool_session_broker():
    from server.tool_session_broker import get_tool_session_broker as _get
    return _get()


def _handle_success_event(
    event_type: str,
    *,
    project: str,
    ide: str,
    metadata: dict,
) -> dict[str, Any]:
    broker = get_tool_session_broker()
    completed: list[dict] = []
    tool_session_id = metadata.get("tool_session_id") or metadata.get("session_id")

    if tool_session_id:
        sessions = [broker.get_session(tool_session_id)]
        sessions = [s for s in sessions if s and s.status == "active"]
    else:
        active = broker.list_active_sessions(project=project)
        sessions = []
        for row in active:
            try:
                s = broker.get_session(row["session_id"])
                if s and s.status == "active" and (not ide or ide == "unknown" or s.ide == ide):
                    sessions.append(s)
            except Exception:
                pass

    summary = metadata.get("summary") or f"auto:{event_type}"
    for session in sessions[:3]:
        end = broker.end_session(
            session.session_id,
            outcome="correct",
            summary=summary,
            ide_session_id=metadata.get("ide_session_id"),
        )
        for tool_name in session.tools_loaded:
            _safe_record_outcome(
                tool_name, "correct",
                task_summary=session.query,
                project=session.project,
                ide=session.ide or ide,
            )
        completed.append(end)

    closed_routes = _close_stale_routes(project=project, ide=ide)

    return {
        "status": "success_closed",
        "sessions_completed": len(completed),
        "session_ids": [c.get("session_id") for c in completed],
        "routes_auto_closed": closed_routes.get("closed", 0),
    }


def _handle_failure_event(
    event_type: str,
    *,
    project: str,
    ide: str,
    metadata: dict,
) -> dict[str, Any]:
    broker = get_tool_session_broker()
    failed_skill = metadata.get("skill_name") or metadata.get("failed_tool") or ""
    query = metadata.get("query") or metadata.get("task_summary") or ""
    error = metadata.get("error") or event_type

    tool_session_id = metadata.get("tool_session_id") or metadata.get("session_id")
    session = broker.get_session(tool_session_id) if tool_session_id else None
    if session and not failed_skill and session.tools_loaded:
        failed_skill = session.tools_loaded[0]
    if session and not query:
        query = session.query

    reflection_result = _reflect_and_suggest(
        failed_skill=failed_skill,
        query=query,
        error=error,
        project=project,
        exclude={failed_skill} if failed_skill else None,
    )

    if session and session.status == "active":
        broker.end_session(
            session.session_id,
            outcome="wrong" if event_type == "test_failed" else "partial",
            summary=f"auto:{event_type}: {error[:200]}",
            ide_session_id=metadata.get("ide_session_id"),
        )
        if failed_skill:
            _safe_record_outcome(
                failed_skill, "wrong",
                task_summary=query,
                project=project,
                ide=ide,
            )

    return {
        "status": "failure_handled",
        "failed_skill": failed_skill,
        "reflection": reflection_result,
        "alternative_skill": reflection_result.get("alternative_skill"),
        "auto_retry_recommended": bool(reflection_result.get("alternative_skill")),
    }


def _reflect_and_suggest(
    *,
    failed_skill: str,
    query: str,
    error: str,
    project: str,
    exclude: set[str] | None = None,
) -> dict[str, Any]:
    exclude = exclude or set()
    candidates: list[dict] = []

    try:
        from server.router import route_task
        route = route_task(query or "retry after failure", project, ide="claude-code")
        for skill in route.get("skills") or []:
            if isinstance(skill, dict):
                name = skill.get("name") or skill.get("skill_name")
                if name and name not in exclude:
                    candidates.append({"name": name, **skill})
            elif isinstance(skill, str) and skill not in exclude:
                candidates.append({"name": skill})
    except Exception as exc:
        logger.debug("route_task for reflection failed: %s", exc)

    if not candidates and failed_skill:
        try:
            from server.learning import _find_alternatives
            conn = get_connection()
            try:
                row = conn.execute(
                    "SELECT cluster FROM resources WHERE name=?", (failed_skill,)
                ).fetchone()
                cluster = row["cluster"] if row else "general"
                alts = _find_alternatives(conn, failed_skill, cluster, query)
                candidates = [a for a in alts if a.get("name") not in exclude]
            finally:
                conn.close()
        except Exception as exc:
            logger.debug("find_alternatives failed: %s", exc)

    alt_name = candidates[0]["name"] if candidates else None

    try:
        from server.reflection_loop import reflect_and_retry
        retry = reflect_and_retry(
            failed_tool=failed_skill or "unknown",
            error=error,
            query=query,
            candidates=candidates,
        )
        if retry.alternative_used:
            alt_name = retry.alternative_used
        return {
            "diagnosis": retry.diagnosis,
            "alternative_skill": alt_name,
            "attempts": retry.attempts,
            "success": retry.success,
            "candidates": [c.get("name") for c in candidates[:5]],
        }
    except Exception as exc:
        logger.debug("reflect_and_retry failed: %s", exc)
        return {
            "diagnosis": str(error)[:300],
            "alternative_skill": alt_name,
            "attempts": 0,
            "success": False,
            "candidates": [c.get("name") for c in candidates[:5]],
            "error": str(exc),
        }


def _close_stale_routes(*, project: str, ide: str) -> dict[str, Any]:
    try:
        from server.broker_runtime import _close_routing_outcomes
        conn = get_connection()
        try:
            return _close_routing_outcomes(conn, project=project, ide=ide, max_rows=50)
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("close routing outcomes failed: %s", exc)
        return {"closed": 0, "error": str(exc)}


def _safe_record_outcome(
    skill_name: str,
    outcome: str,
    *,
    task_summary: str,
    project: str,
    ide: str,
) -> None:
    if not skill_name:
        return
    try:
        from server.learning import record_outcome
        record_outcome(
            skill_name=skill_name,
            outcome=outcome,
            task_summary=task_summary[:500],
            project=project,
            ide=ide,
        )
    except Exception as exc:
        logger.debug("record_outcome skipped for %s: %s", skill_name, exc)


def _record_beliefs_from_event(
    event_type: str,
    outcome: str,
    project: str,
    ide: str,
    metadata: dict,
    model: str,
    provider: str,
    task_type: str,
) -> None:
    skill = metadata.get("skill_name") or ""
    if skill:
        _safe_record_outcome(skill, outcome, task_summary=metadata.get("query", event_type), project=project, ide=ide)

    try:
        from server.event_bridge import get_bridge
        bridge = get_bridge()
        bridge.outcome_to_event(outcome, {
            "provider": provider,
            "model": model,
            "task_type": task_type,
            "project": project,
            **metadata,
        }, ide=ide)
    except Exception as exc:
        logger.debug("event_bridge skipped: %s", exc)


def distill_project_learnings(
    project: str,
    *,
    days: int = 7,
    max_patterns: int = 12,
) -> dict[str, Any]:
    """Distill ``project_contexts`` + recent sessions into searchable learnings."""
    project = (project or "default").strip()
    patterns: Counter[str] = Counter()
    snippets: list[dict[str, Any]] = []

    with db_conn() as conn:
        ctx_rows = conn.execute(
            """
            SELECT title, content, context_type, tags_json, created_at
            FROM project_contexts
            WHERE project = ?
              AND created_at >= datetime('now', ?)
            ORDER BY created_at DESC
            LIMIT 80
            """,
            (project, f"-{days} days"),
        ).fetchall()

        for row in ctx_rows:
            text = f"{row['title']}\n{row['content']}"
            for line in text.splitlines():
                line = line.strip()
                if len(line) < 24 or line.startswith("#"):
                    continue
                if line.startswith("- Learning:") or line.startswith("- Resource"):
                    key = line[:120]
                    patterns[key] += 1
                    snippets.append({
                        "source": row["context_type"],
                        "line": line[:400],
                        "created_at": row["created_at"],
                    })

        session_rows = conn.execute(
            """
            SELECT query, cluster, outcome, summary, tools_json
            FROM tool_sessions
            WHERE project = ? AND status IN ('completed', 'abandoned')
              AND ended_at >= datetime('now', ?)
            ORDER BY ended_at DESC
            LIMIT 40
            """,
            (project, f"-{days} days"),
        ).fetchall()

        for row in session_rows:
            tools = json.loads(row["tools_json"] or "[]")
            pattern = (
                f"[{row['outcome'] or 'done'}] {row['query'][:80]} "
                f"→ tools: {', '.join(tools[:4])} (cluster={row['cluster']})"
            )
            patterns[pattern] += 1
            snippets.append({"source": "tool_session", "line": pattern, "created_at": ""})

    top = patterns.most_common(max_patterns)
    if not top:
        return {"status": "no_patterns", "project": project, "distilled": 0}

    distilled_notes: list[str] = []
    for pattern, count in top:
        note = f"[distilled:{project}] ({count}×) {pattern}"
        distilled_notes.append(note)

    stored = 0
    try:
        from server.broker_runtime import add_user_learning
        for note in distilled_notes:
            add_user_learning(
                note,
                scope="distilled",
                task_type="general",
                project=project,
                bundles=[],
                tools=[],
                created_by="rl-distiller",
            )
            stored += 1
    except Exception as exc:
        logger.warning("distill store failed: %s", exc)
        return {
            "status": "error",
            "project": project,
            "patterns_found": len(top),
            "error": str(exc),
            "preview": distilled_notes[:3],
        }

    return {
        "status": "distilled",
        "project": project,
        "distilled": stored,
        "patterns": [{"pattern": p, "count": c} for p, c in top],
        "preview": distilled_notes[:5],
    }


def search_project_learnings(
    query: str = "",
    *,
    project: str = "default",
    limit: int = 20,
) -> dict[str, Any]:
    """Search user learnings + project_contexts + distilled notes."""
    from server.broker_runtime import search_user_learnings

    base = search_user_learnings(query=query, project=project, limit=limit)
    results = list(base.get("results") or [])

    with db_conn() as conn:
        sql = """
            SELECT id, title, content, context_type, tags_json, created_at
            FROM project_contexts
            WHERE project = ?
        """
        params: list[Any] = [project]
        if query:
            sql += " AND (title LIKE ? OR content LIKE ?)"
            params.extend([f"%{query}%", f"%{query}%"])
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        for row in conn.execute(sql, params).fetchall():
            results.append({
                "id": f"ctx-{row['id']}",
                "scope": row["context_type"],
                "task_type": "general",
                "project": project,
                "note": f"{row['title']}\n{(row['content'] or '')[:600]}",
                "bundles": json.loads(row["tags_json"] or "[]"),
                "tools": [],
                "created_by": "project_contexts",
                "created_at": row["created_at"],
            })

    # Prefer distilled notes first
    results.sort(
        key=lambda r: (
            0 if r.get("scope") == "distilled" else 1,
            r.get("created_at") or "",
        ),
        reverse=True,
    )
    return {
        "results": results[:limit],
        "method": base.get("method", "keyword"),
        "total": len(results[:limit]),
        "sources": ["user_learnings", "project_contexts", "distilled"],
    }