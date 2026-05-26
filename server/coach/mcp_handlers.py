"""MCP handler implementations for Coach tools.

Each handler is a standalone function that receives plain arguments
and returns a JSON string.  They delegate to ``CoachBridge`` for data
and gracefully handle missing Coach cache.
"""

from __future__ import annotations

import json
from typing import Any

from server.coach.bridge import get_bridge


def handle_coach_query(
    query: str = "",
    project: str = "default",
    include_scores: bool = True,
    include_antipatterns: bool = True,
    include_sessions: bool = False,
) -> str:
    """Aggregated dashboard: practice scores + anti-patterns + recent sessions."""
    bridge = get_bridge()
    result: dict[str, Any] = {
        "query": query,
        "project": project,
    }

    if include_scores:
        scores = bridge.get_practice_scores(project)
        result["practice_scores"] = [
            {
                "date": s.date,
                "prompt_quality": s.prompt_quality,
                "session_hygiene": s.session_hygiene,
                "code_review": s.code_review,
                "tool_mastery": s.tool_mastery,
                "context_management": s.context_management,
                "total_score": s.total_score,
            }
            for s in scores
        ]

    if include_antipatterns:
        patterns = bridge.get_antipatterns(project)
        result["antipatterns"] = [
            {
                "rule_id": p.rule_id,
                "rule_name": p.rule_name,
                "group": p.group_name,
                "severity": p.severity,
                "count": p.count,
                "total": p.total,
                "ratio": p.ratio,
                "description": p.description,
                "suggestion": p.suggestion,
            }
            for p in patterns
        ]

    if include_sessions:
        try:
            from server.coach.schema import get_connection

            conn = get_connection()
            rows = conn.execute(
                """SELECT session_id, harness, workspace_name, date,
                          request_count, end_reason
                   FROM coach_sessions
                   WHERE project = ?
                   ORDER BY last_activity DESC
                   LIMIT 20""",
                (project,),
            ).fetchall()
            result["recent_sessions"] = [
                {
                    "session_id": r["session_id"],
                    "harness": r["harness"],
                    "workspace": r["workspace_name"],
                    "date": r["date"],
                    "requests": r["request_count"],
                    "end_reason": r["end_reason"],
                }
                for r in rows
            ]
            conn.close()
        except Exception:
            result["recent_sessions"] = []

    return json.dumps(result, indent=2)


def handle_coach_antipatterns(
    rule_id: str = "",
    group: str = "",
    severity: str = "",
    since_date: str = "",
) -> str:
    """Filtered anti-pattern detections."""
    bridge = get_bridge()
    patterns = bridge.get_antipatterns(
        project="default",
        severity=severity if severity else None,
        since=since_date if since_date else None,
    )

    if rule_id:
        patterns = [p for p in patterns if p.rule_id == rule_id]
    if group:
        patterns = [p for p in patterns if p.group_name == group]

    result = [
        {
            "rule_id": p.rule_id,
            "rule_name": p.rule_name,
            "group": p.group_name,
            "severity": p.severity,
            "count": p.count,
            "total": p.total,
            "ratio": p.ratio,
            "description": p.description,
            "suggestion": p.suggestion,
            "details": p.details,
            "weekly_hist": p.weekly_hist,
        }
        for p in patterns
    ]

    return json.dumps(
        {
            "antipatterns": result,
            "total": len(result),
            "filter": {
                "rule_id": rule_id or None,
                "group": group or None,
                "severity": severity or None,
                "since_date": since_date or None,
            },
        },
        indent=2,
    )


def handle_coach_converse(query: str, context: str = "") -> str:
    """Conversational coaching via the Phase 6 coaching persona.

    Delegates to ``server.coach.conversation.coach_chat()`` which
    attempts the NEXUS LLM gateway (Ollama / NVIDIA NIM) and falls
    back to a structured template response.
    """
    from server.coach.conversation import coach_chat

    result = coach_chat(query, context_hint=context)
    return json.dumps(result, indent=2)


def handle_coach_skill_finder(pattern: str) -> str:
    """Match a prompt pattern against NEXUS skill catalog."""
    if not pattern or not pattern.strip():
        return json.dumps({"skills": [], "pattern": pattern})

    try:
        from server.router import find_matching_skills

        project = "default"
        skills = find_matching_skills(pattern, project)
        matched = []
        for s in skills[:5]:
            matched.append(
                {
                    "name": s.get("name", ""),
                    "description": s.get("description", "")[:120],
                    "tier": s.get("tier", 2),
                    "cluster": s.get("cluster", ""),
                }
            )
        return json.dumps(
            {"pattern": pattern, "skills": matched, "total_matches": len(matched)},
            indent=2,
        )
    except Exception as exc:
        return json.dumps(
            {"pattern": pattern, "skills": [], "error": str(exc)}, indent=2
        )


def handle_coach_analyze_sessions(
    harness: str = "",
    since_date: str = "",
    limit: int = 10,
) -> str:
    """Deep session analysis."""
    try:
        from server.coach.schema import get_connection

        conn = get_connection()
        clauses: list[str] = ["1=1"]
        params: list[Any] = []

        if harness:
            clauses.append("harness = ?")
            params.append(harness)
        if since_date:
            clauses.append("date >= ?")
            params.append(since_date)

        rows = conn.execute(
            f"""SELECT session_id, harness, workspace_name, date,
                       request_count, model_usage_json, first_activity,
                       last_activity, end_reason
                FROM coach_sessions
                WHERE {' AND '.join(clauses)}
                ORDER BY last_activity DESC
                LIMIT ?""",
            params + [limit],
        ).fetchall()

        sessions = [
            {
                "session_id": r["session_id"],
                "harness": r["harness"],
                "workspace": r["workspace_name"],
                "date": r["date"],
                "requests": r["request_count"],
                "model_usage": json.loads(r["model_usage_json"] or "{}"),
                "first_activity": r["first_activity"],
                "last_activity": r["last_activity"],
                "end_reason": r["end_reason"],
            }
            for r in rows
        ]

        # Compute aggregate stats
        total_requests = sum(s["requests"] for s in sessions)
        unique_harnesses = set(s["harness"] for s in sessions if s["harness"])
        end_reasons: dict[str, int] = {}
        for s in sessions:
            er = s["end_reason"] or "unknown"
            end_reasons[er] = end_reasons.get(er, 0) + 1

        conn.close()

        return json.dumps(
            {
                "sessions": sessions,
                "total_sessions": len(sessions),
                "total_requests": total_requests,
                "unique_harnesses": sorted(unique_harnesses),
                "end_reason_distribution": end_reasons,
            },
            indent=2,
        )
    except Exception as exc:
        return json.dumps({"sessions": [], "error": str(exc)}, indent=2)
