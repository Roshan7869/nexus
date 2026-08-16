"""Project continuity — cross-IDE attach / resume / notes (unified-MCP L1+L2).

The design rule from docs/UNIFIED_MCP_DESIGN.md: nothing that must survive an
IDE switch may live in MCP process memory (stdio MCP = one process per IDE).
The DB is the substrate; AGENTS.md + .nexus/memory.md at the project root are
the portable interchange every IDE can read.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.environ.get("NEXUS_DB_PATH", str(REPO_ROOT / "db" / "nexus.db")))

AGENTS_EXCERPT_CHARS = 400


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_schema(conn: sqlite3.Connection | None = None) -> None:
    """Create continuity tables (idempotent). Safe next to the live schema."""
    own = conn is None
    conn = conn or _get_conn()
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
        # routing_log may not exist in a fresh continuity-only DB; create the
        # production shape (subset of columns resume() reads) so queries work.
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS routing_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_summary TEXT NOT NULL,
                project TEXT NOT NULL DEFAULT 'default',
                matched_tier INTEGER NOT NULL DEFAULT 2,
                matched_model TEXT NOT NULL DEFAULT 'unknown',
                matched_skills TEXT NOT NULL DEFAULT '[]',
                actual_model_used TEXT,
                outcome TEXT,
                skill_name TEXT,
                tokens_used INTEGER DEFAULT 0,
                ide TEXT DEFAULT 'unknown',
                action_type TEXT,
                repeat_count_1h INTEGER DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_routing_log_project
                ON routing_log(project, created_at DESC);
        """)
        # model_beliefs is queried by resume(); create the production shape so a
        # continuity-only DB works standalone.
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS model_beliefs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL DEFAULT 'nexus',
                model TEXT NOT NULL,
                task_type TEXT NOT NULL DEFAULT 'general',
                alpha REAL NOT NULL DEFAULT 1.0,
                beta REAL NOT NULL DEFAULT 1.0,
                total_requests INTEGER NOT NULL DEFAULT 0,
                total_reward REAL DEFAULT 0.0,
                UNIQUE(provider, model, task_type)
            );
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
    conn = _get_conn()
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
    conn = _get_conn()
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
    conn = _get_conn()
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
