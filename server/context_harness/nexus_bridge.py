"""NEXUS DB bridge — query resources, routing_log, outcome_log, learnings."""
from server.db import get_db_path as _resolve_db_path
import sqlite3
from pathlib import Path
from typing import Optional

DB_PATH = _resolve_db_path()


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def search_resources(query: str, max_results: int = 10) -> list[dict]:
    """Search active resources — OKF sections + catalog fields."""
    conn = get_conn()
    try:
        words = [w for w in query.lower().split() if len(w) > 2]
        if not words:
            words = [w for w in query.lower().split() if w]

        like_clauses = []
        params: list = []
        for w in words:
            pattern = f"%{w}%"
            like_clauses.append(
                "(LOWER(r.name) LIKE ? OR LOWER(r.description) LIKE ? "
                "OR LOWER(r.body_text) LIKE ? OR LOWER(rs.heading) LIKE ? "
                "OR LOWER(rs.content) LIKE ?)"
            )
            params.extend([pattern] * 5)
        where = " OR ".join(like_clauses) if like_clauses else "1=1"

        rows = conn.execute(
            f"""
            SELECT r.id, r.name, r.resource_type, r.description, r.cluster,
                   r.token_count, r.times_triggered, r.times_correct,
                   r.times_wrong, r.status, r.file_path,
                   r.repo_name, r.absorbed_at_commit, r.absorbed_at_date,
                   COUNT(rs.id) AS section_count
            FROM resources r
            LEFT JOIN resource_sections rs ON rs.resource_id = r.id
            WHERE r.status='active' AND r.invalidated_at IS NULL
              AND NOT EXISTS (SELECT 1 FROM hygiene_flags hf
                              WHERE hf.resource_id = r.id
                                AND hf.resolved_at IS NULL)
              AND ({where})
            GROUP BY r.id
            ORDER BY section_count DESC, r.times_correct DESC, r.times_triggered DESC
            LIMIT ?
            """,
            params + [max_results],
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def search_learnings(query: str, limit: int = 10) -> list[dict]:
    """Search learning notes stored via nexus_add_learning_note."""
    conn = get_conn()
    try:
        words = [w for w in query.lower().split() if len(w) > 2]
        clauses = []
        params = []
        for w in words:
            clauses.append("(LOWER(note) LIKE ? OR LOWER(scope) LIKE ?)")
            params.extend([f"%{w}%", f"%{w}%"])
        where = " AND ".join(clauses) if clauses else "1=1"
        rows = conn.execute(
            f"""SELECT id, note, scope, project, task_type, created_at
                FROM user_learnings u
                WHERE {where}
                  AND NOT EXISTS (SELECT 1 FROM note_ledger nl
                                  WHERE nl.note_id = u.id
                                    AND nl.invalidated_at IS NOT NULL)
                ORDER BY id DESC
                LIMIT ?""",
            params + [limit],
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def store_learning(
    key: str,
    value: str,
    tags: list[str] | None = None,
    *,
    project: str = "default",
    task_type: str = "general",
) -> int:
    """Store a context entry as a learning note (fallback when gcode unavailable)."""
    conn = get_conn()
    try:
        tag_str = ",".join(tags) if tags else ""
        note = f"[context-harness:{project}:{task_type}:{key}]\n{value}\n---\ntags: {tag_str}"
        conn.execute(
            """INSERT INTO user_learnings (note, scope, project, task_type, bundles_json, tools_json, created_by)
               VALUES (?, 'context-harness', ?, ?, ?, '[\"context-harness-engine\"]', 'engine')""",
            (note, project, task_type, tag_str),
        )
        conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    finally:
        conn.close()
