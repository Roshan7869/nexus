"""Project continuity — cross-IDE attach / resume / notes (unified-MCP L1+L2).

The design rule from docs/UNIFIED_MCP_DESIGN.md: nothing that must survive an
IDE switch may live in MCP process memory (stdio MCP = one process per IDE).
The DB is the substrate; AGENTS.md + .nexus/memory.md at the project root are
the portable interchange every IDE can read.

Connections come from server.db (lazy path resolution — the single source of
truth). This module owns only its own tables: projects + project_notes.
routing_log and model_beliefs belong to db.py / learning.py; resume() requires
them to exist (they do on any provisioned NEXUS DB; tests provision them).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from server.db import get_connection

REPO_ROOT = Path(__file__).resolve().parents[1]

AGENTS_EXCERPT_CHARS = 400


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_schema(conn: sqlite3.Connection | None = None) -> None:
    """Create continuity tables (idempotent). Safe next to the live schema."""
    own = conn is None
    conn = conn or get_connection()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS projects (
                name TEXT PRIMARY KEY,
                git_root TEXT,
                last_ide TEXT,
                last_active_at TEXT,
                note TEXT
            );
            CREATE TABLE IF NOT EXISTS project_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL,
                text TEXT NOT NULL,
                ide TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_project_notes_project
                ON project_notes(project, created_at DESC);
        """)
        conn.commit()
    finally:
        if own:
            conn.close()


def _memory_path(git_root: str | None) -> Path | None:
    if not git_root:
        return None
    return Path(git_root) / ".nexus" / "memory.md"


def _read_agents_md(git_root: str | None) -> dict:
    if not git_root:
        return {"exists": False, "path": None, "excerpt": ""}
    path = Path(git_root) / "AGENTS.md"
    if not path.is_file():
        return {"exists": False, "path": str(path), "excerpt": ""}
    return {
        "exists": True,
        "path": str(path),
        "excerpt": path.read_text(errors="replace")[:AGENTS_EXCERPT_CHARS],
    }


def attach(project: str, git_root: str | None = None, ide: str | None = None) -> dict:
    """Register/refresh a project. Call once per IDE session; returns a resume hint."""
    ensure_schema()
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT name FROM projects WHERE name=?", (project,)).fetchone()
        conn.execute(
            """INSERT INTO projects (name, git_root, last_ide, last_active_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET
                   git_root=COALESCE(excluded.git_root, git_root),
                   last_ide=excluded.last_ide,
                   last_active_at=excluded.last_active_at""",
            (project, git_root, ide, _now()),
        )
        conn.commit()
        return {
            "project": project,
            "created": existing is None,
            "ide": ide,
            "resume": resume(project),
        }
    finally:
        conn.close()


def note(project: str, text: str, ide: str | None = None) -> dict:
    """Persist a durable project note: DB row + append to .nexus/memory.md."""
    ensure_schema()
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO project_notes (project, text, ide) VALUES (?, ?, ?)",
            (project, text, ide),
        )
        root = conn.execute(
            "SELECT git_root FROM projects WHERE name=?", (project,)).fetchone()
        conn.commit()
    finally:
        conn.close()

    written = False
    mem = _memory_path(root["git_root"] if root else None)
    if mem is not None:
        try:
            mem.parent.mkdir(parents=True, exist_ok=True)
            with mem.open("a", encoding="utf-8") as f:
                f.write(f"- [{_now()}]{' (' + ide + ')' if ide else ''} {text}\n")
            written = True
        except OSError:
            pass  # DB row is the source of truth; file is best-effort
    return {"project": project, "saved": True, "memory_file": written}


def resume(project: str, limit: int = 10) -> dict:
    """The cross-IDE 'where was I': recent routes, notes, AGENTS.md, beliefs."""
    ensure_schema()
    conn = get_connection()
    try:
        meta = conn.execute(
            "SELECT git_root, last_ide, last_active_at FROM projects WHERE name=?",
            (project,)).fetchone()
        routes = conn.execute(
            """SELECT task_summary, outcome, ide, created_at FROM routing_log
               WHERE project=? ORDER BY created_at DESC, id DESC LIMIT ?""",
            (project, limit)).fetchall()
        notes = conn.execute(
            """SELECT text, ide, created_at FROM project_notes
               WHERE project=? ORDER BY created_at DESC, id DESC LIMIT ?""",
            (project, limit)).fetchall()
        beliefs = conn.execute(
            """SELECT model, task_type, ROUND(alpha,2) AS alpha, ROUND(beta,2) AS beta,
                      total_requests
               FROM model_beliefs ORDER BY total_requests DESC LIMIT 5""").fetchall()
    finally:
        conn.close()

    git_root = meta["git_root"] if meta else None
    repo_map = ""
    if git_root and Path(git_root).is_dir():
        try:
            from server.repo_map import build as build_repo_map
            # Small budget here: resume is a digest; full map via the MCP tool.
            repo_map = build_repo_map(git_root, budget_tokens=400)["map"]
        except Exception:
            pass  # resume must never fail on map failure
    return {
        "project": project,
        "last_ide": meta["last_ide"] if meta else None,
        "last_active_at": meta["last_active_at"] if meta else None,
        "recent_routes": [dict(r) for r in routes],
        "notes": [dict(n) for n in notes],
        "agents_md": _read_agents_md(git_root),
        "repo_map_excerpt": repo_map,
        "top_beliefs": [dict(b) for b in beliefs],  # global until U2 per-project keys
    }
