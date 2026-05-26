"""CoachDataFeeder — Populates coach_* tables from NEXUS's own operational data.

The Coach subsystem was designed for a VS Code extension cache that doesn't
exist in this environment. This module bridges the gap by feeding NEXUS's own
routing_log, plans, and execution_log into the coach schema.

Call `feed_all()` periodically (cron) or on-demand.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from server.db import get_connection

logger = logging.getLogger(__name__)


def _today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_coach_tables(conn: Any) -> None:
    """Ensures coach_* tables exist if bridge hasn't created them yet.

    Uses same schema as bridge.py to avoid column conflicts.
    If tables exist (created by bridge), this is a no-op.
    """
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS coach_practice_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project TEXT NOT NULL DEFAULT 'default',
            date TEXT NOT NULL,
            prompt_quality REAL NOT NULL DEFAULT 0.0,
            session_hygiene REAL NOT NULL DEFAULT 0.0,
            code_review REAL NOT NULL DEFAULT 0.0,
            tool_mastery REAL NOT NULL DEFAULT 0.0,
            context_management REAL NOT NULL DEFAULT 0.0,
            total_score REAL NOT NULL DEFAULT 0.0,
            source TEXT NOT NULL DEFAULT 'routing_log',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(project, date)
        );
        CREATE TABLE IF NOT EXISTS coach_antipatterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project TEXT NOT NULL DEFAULT 'default',
            rule_id TEXT NOT NULL,
            rule_name TEXT NOT NULL DEFAULT '',
            group_name TEXT NOT NULL DEFAULT '',
            severity TEXT NOT NULL DEFAULT 'low' CHECK(severity IN ('high', 'medium', 'low')),
            count INTEGER NOT NULL DEFAULT 0,
            total INTEGER NOT NULL DEFAULT 0,
            ratio REAL NOT NULL DEFAULT 0.0,
            description TEXT NOT NULL DEFAULT '',
            suggestion TEXT NOT NULL DEFAULT '',
            details_json TEXT DEFAULT '[]',
            weekly_hist_json TEXT DEFAULT '[]',
            last_detected TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(project, rule_id)
        );
        CREATE TABLE IF NOT EXISTS coach_daily_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project TEXT NOT NULL DEFAULT 'default',
            date TEXT NOT NULL,
            request_count INTEGER NOT NULL DEFAULT 0,
            session_count INTEGER NOT NULL DEFAULT 0,
            total_loc INTEGER NOT NULL DEFAULT 0,
            unique_models TEXT DEFAULT '[]',
            unique_tools TEXT DEFAULT '[]',
            total_tokens_input INTEGER NOT NULL DEFAULT 0,
            total_tokens_output INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(project, date)
        );
        CREATE TABLE IF NOT EXISTS coach_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project TEXT NOT NULL DEFAULT 'default',
            session_id TEXT NOT NULL,
            harness TEXT NOT NULL DEFAULT '',
            workspace_id TEXT NOT NULL DEFAULT '',
            workspace_name TEXT NOT NULL DEFAULT '',
            date TEXT,
            request_count INTEGER NOT NULL DEFAULT 0,
            model_usage_json TEXT DEFAULT '{}',
            first_activity INTEGER,
            last_activity INTEGER,
            end_reason TEXT DEFAULT 'unknown',
            source TEXT NOT NULL DEFAULT 'routing_log',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(session_id)
        );
    """)


def feed_practice_scores(project: str = "default") -> int:
    """Compute practice scores from routing_log and upsert into coach_practice_scores.

    Uses routing_log as the primary data source instead of VS Code cache.
    Returns number of score rows inserted.
    """
    conn = get_connection()
    ensure_coach_tables(conn)

    today = _today_iso()

    # Aggregate routing_log by day
    rows = conn.execute("""
        SELECT
            date(created_at) as day,
            COUNT(*) as total_requests,
            SUM(CASE WHEN outcome IN ('correct','routed') THEN 1 ELSE 0 END) as correct_count,
            SUM(CASE WHEN outcome IN ('wrong','partial') THEN 1 ELSE 0 END) as wrong_count,
            SUM(CASE WHEN outcome = 'pending' THEN 1 ELSE 0 END) as pending_count,
            COALESCE(SUM(tokens_used), 0) as total_tokens,
            COUNT(DISTINCT skill_name) as unique_skills,
            COUNT(DISTINCT matched_model) as unique_models
        FROM routing_log
        WHERE project = ?
        GROUP BY date(created_at)
        ORDER BY day DESC
        LIMIT 30
    """, (project,)).fetchall()

    inserted = 0
    for row in rows:
        day = row["day"]
        total = row["total_requests"]
        correct = row["correct_count"] or 0
        wrong = row["wrong_count"] or 0
        uniq_skills = row["unique_skills"] or 0
        tokens = row["total_tokens"] or 0

        if total == 0:
            continue

        # Map routing_log stats → coach practice dimensions
        prompt_quality = round(min(100, (correct / total) * 100), 1)
        session_hygiene = round(min(100, 100 - (row["pending_count"] or 0) / total * 50), 1)
        tool_mastery = round(min(100, uniq_skills * 10 + 30), 1)
        code_review = round(min(100, 40 + (wrong / total) * 60), 1)
        context_management = round(min(100, 50 + (tokens / max(total, 1)) / 1000), 1)

        total_score = round(
            (prompt_quality + session_hygiene + code_review + tool_mastery + context_management) / 5, 1
        )

        conn.execute("""
            INSERT OR REPLACE INTO coach_practice_scores
                (project, date, prompt_quality, session_hygiene, code_review,
                 tool_mastery, context_management, total_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            project, day,
            prompt_quality, session_hygiene, code_review,
            tool_mastery, context_management, total_score
        ))
        inserted += 1

    conn.commit()
    conn.close()
    return inserted


def feed_antipatterns(project: str = "default") -> int:
    """Detect anti-patterns from routing_log and upsert into coach_antipatterns.

    Detects:
      - lazy-prompting: high ratio of pending/empty outcomes
      - high-cancel-rate: many partial outcomes
      - low-tool-variety: too few unique skills used
      - no-code-review: /audit_plan or review tools never called
    """
    conn = get_connection()
    ensure_coach_tables(conn)

    today = _today_iso()

    # Total requests for ratio calculations
    total_row = conn.execute("""
        SELECT COUNT(*) as cnt FROM routing_log WHERE project = ?
    """, (project,)).fetchone()
    total = total_row["cnt"] if total_row else 0
    if total == 0:
        conn.close()
        return 0

    # 1. Lazy prompting: high pending ratio
    pending_row = conn.execute("""
        SELECT COUNT(*) as cnt FROM routing_log
        WHERE project = ? AND outcome = 'pending'
    """, (project,)).fetchone()
    pending = pending_row["cnt"] if pending_row else 0
    pending_ratio = round(pending / total, 3)

    # 2. Partial outcomes (tools mid-failure or cancellation)
    partial_row = conn.execute("""
        SELECT COUNT(*) as cnt FROM routing_log
        WHERE project = ? AND outcome = 'partial'
    """, (project,)).fetchone()
    partial = partial_row["cnt"] if partial_row else 0
    partial_ratio = round(partial / total, 3)

    # 2b. Wrong rate (tools returning incorrect results)
    wrong_row = conn.execute("""
        SELECT COUNT(*) as cnt FROM routing_log
        WHERE project = ? AND outcome = 'wrong'
    """, (project,)).fetchone()
    wrong = wrong_row["cnt"] if wrong_row else 0
    correct_row = conn.execute("""
        SELECT COUNT(*) as cnt FROM routing_log
        WHERE project = ? AND outcome = 'correct'
    """, (project,)).fetchone()
    correct = correct_row["cnt"] if correct_row else 0
    correct_ratio = round(correct / total, 3) if total > 0 else 1.0

    # 3. Low tool variety
    skill_row = conn.execute("""
        SELECT COUNT(DISTINCT skill_name) as cnt FROM routing_log
        WHERE project = ? AND skill_name IS NOT NULL AND skill_name != ''
    """, (project,)).fetchone()
    unique_skills = skill_row["cnt"] if skill_row else 0

    # 4. Review/audit tools used (fix SQL precedence bug — was missing parens)
    review_row = conn.execute("""
        SELECT COUNT(*) as cnt FROM routing_log
        WHERE project = ? AND (skill_name LIKE '%review%' OR skill_name LIKE '%audit%')
    """, (project,)).fetchone()
    review_count = review_row["cnt"] if review_row else 0

    now = _now_iso()
    inserted = 0

    patterns = []

    if pending_ratio > 0.3:
        patterns.append({
            "rule_id": "lazy-prompting",
            "rule_name": "Pending-heavy routing (potential lazy prompting)",
            "group_name": "prompt-quality",
            "severity": "high" if pending_ratio > 0.5 else "medium",
            "count": pending,
            "total": total,
            "ratio": pending_ratio,
            "description": f"{pending}/{total} routing calls are in 'pending' state — tools may not be completing.",
            "suggestion": "Check if auto_route invocations are completing. Add error handling in tool execution paths.",
        })

    if partial_ratio > 0.05:
        patterns.append({
            "rule_id": "high-partial-rate",
            "rule_name": "High Partial Outcome Rate",
            "group_name": "session-hygiene",
            "severity": "high" if partial_ratio > 0.15 else "medium",
            "count": partial,
            "total": total,
            "ratio": partial_ratio,
            "description": f"{partial} routing calls ended with 'partial' ({partial_ratio*100:.1f}%) — tools may be failing mid-execution.",
            "suggestion": "Review tool selection and error handling in auto_invoke.py. Many tools may be timing out or throwing unhandled exceptions.",
        })

    if correct_ratio < 0.6:
        patterns.append({
            "rule_id": "high-error-rate",
            "rule_name": "High Routing Error Rate",
            "group_name": "session-hygiene",
            "severity": "high" if correct_ratio < 0.4 else "medium",
            "count": total - correct,
            "total": total,
            "ratio": round(1.0 - correct_ratio, 3),
            "description": f"Only {correct}/{total} routing calls ({correct_ratio*100:.1f}%) returned successful results.",
            "suggestion": "Check NEXUS routing quality. Tools may be mismatched to tasks or reflection_loop may need stricter result validation.",
        })

    if unique_skills <= 2 and total >= 5:
        patterns.append({
            "rule_id": "low-tool-variety",
            "rule_name": "Low Tool Variety",
            "group_name": "tool-mastery",
            "severity": "medium",
            "count": unique_skills,
            "total": total,
            "ratio": round(unique_skills / total, 3),
            "description": f"Only {unique_skills} unique skills used across {total} routing calls.",
            "suggestion": "Explore more NEXUS skills via find_skills(). Each tool is a distinct capability.",
        })

    if review_count == 0 and total >= 10:
        patterns.append({
            "rule_id": "no-code-review",
            "rule_name": "No Code Review Pattern",
            "group_name": "code-review",
            "severity": "low",
            "count": 0,
            "total": total,
            "ratio": 0.0,
            "description": "No review/audit tools used. Plans may skip verification.",
            "suggestion": "Run audit_plan after each design_plan to catch issues early.",
        })

    for p in patterns:
        conn.execute("""
            INSERT OR REPLACE INTO coach_antipatterns
                (project, rule_id, rule_name, group_name, severity,
                 count, total, ratio, description, suggestion,
                 details_json, weekly_hist_json, last_detected)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            project, p["rule_id"], p["rule_name"], p["group_name"], p["severity"],
            p["count"], p["total"], p["ratio"],
            p["description"], p["suggestion"],
            json.dumps([{"detected_at": now, "count": p["count"]}]),
            json.dumps([]),
            now,
        ))
        inserted += 1

    conn.commit()
    conn.close()
    return inserted


def feed_daily_activity(project: str = "default") -> int:
    """Aggregate routing_log into coach_daily_activity table."""
    conn = get_connection()
    ensure_coach_tables(conn)

    rows = conn.execute("""
        SELECT
            date(created_at) as day,
            COUNT(*) as total_requests,
            SUM(CASE WHEN outcome IN ('correct','routed') THEN 1 ELSE 0 END) as correct_count,
            SUM(CASE WHEN outcome IN ('wrong','partial') THEN 1 ELSE 0 END) as wrong_count,
            SUM(CASE WHEN outcome = 'pending' THEN 1 ELSE 0 END) as pending_count,
            COALESCE(SUM(tokens_used), 0) as total_tokens,
            COUNT(DISTINCT skill_name) as unique_skills,
            COUNT(DISTINCT matched_model) as unique_models
        FROM routing_log
        WHERE project = ?
        GROUP BY date(created_at)
        ORDER BY day
    """, (project,)).fetchall()

    inserted = 0
    for row in rows:
        conn.execute("""
            INSERT OR REPLACE INTO coach_daily_activity
                (project, date, request_count, session_count, unique_models, unique_tools,
                 total_tokens_input, total_tokens_output)
            VALUES (?, ?, ?, 1, ?, ?,
                    ?, 0)
        """, (
            project,
            row["day"],
            row["total_requests"] or 0,
            json.dumps([row["unique_models"]]),  # unique_models as JSON array
            json.dumps([row["unique_skills"]]),  # unique_tools as JSON array
            row["total_tokens"] or 0,
        ))
        inserted += 1

    conn.commit()
    conn.close()
    return inserted


def feed_all(project: str = "default") -> dict[str, int]:
    """Run all feeder functions and return counts."""
    return {
        "practice_scores": feed_practice_scores(project),
        "antipatterns": feed_antipatterns(project),
        "daily_activity": feed_daily_activity(project),
    }
