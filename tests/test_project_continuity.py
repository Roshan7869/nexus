"""Project continuity module — cross-IDE attach/resume/notes (unified-MCP U1).

TDD: written RED first; the module is the smallest deep interface that makes
IDE switching lossless. The DB (not process memory) is the substrate — stdio
MCP means one process per IDE.
"""
import os
import sqlite3
from pathlib import Path

import pytest

os.environ.setdefault("NEXUS_THOMPSON_ROUTING", "false")


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Isolated DB via NEXUS_DB_PATH (lazy resolution in db.py — no reload needed).

    routing_log comes from its owner (server.db.ensure_routing_log_schema);
    model_beliefs is provisioned here as test DDL (its owner, learning.py,
    self-heals columns in production).
    """
    db = tmp_path / "continuity.db"
    monkeypatch.delenv("NEXUS_BROKER_DB_PATH", raising=False)
    monkeypatch.setenv("NEXUS_DB_PATH", str(db))
    # db.get_db_path caches its first resolution for the process; tests that
    # point it elsewhere must reset the cache explicitly.
    from server.db import get_db_path
    if hasattr(get_db_path, "_cached"):
        del get_db_path._cached

    import sqlite3
    conn = sqlite3.connect(str(db))
    # db.py's ensure_routing_log_schema is a copy-migration that assumes the
    # table exists; a fresh test DB provisions the columns resume() reads.
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
    conn.close()

    import server.project_continuity as pc
    pc.ensure_schema()
    yield pc, db, tmp_path
    # restore the process-wide cache for downstream tests in the same run
    if hasattr(get_db_path, "_cached"):
        del get_db_path._cached


class TestSchema:
    def test_tables_created(self, tmp_db):
        pc, db, _ = tmp_db
        conn = sqlite3.connect(db)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        assert {"projects", "project_notes"} <= tables

    def test_schema_idempotent(self, tmp_db):
        pc, *_ = tmp_db
        pc.ensure_schema()  # second call must not raise
        pc.ensure_schema()


class TestAttach:
    def test_attach_creates_and_updates(self, tmp_db):
        pc, db, _ = tmp_db
        r1 = pc.attach("nexus", git_root="/tmp/fake-nexus", ide="opencode")
        assert r1["project"] == "nexus" and r1["created"] is True
        r2 = pc.attach("nexus", git_root="/tmp/fake-nexus", ide="claude-code")
        assert r2["created"] is False  # upsert, not duplicate
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT last_ide, git_root FROM projects WHERE name='nexus'").fetchone()
        conn.close()
        assert row == ("claude-code", "/tmp/fake-nexus")

    def test_attach_returns_resume_hint(self, tmp_db):
        pc, *_ = tmp_db
        r = pc.attach("kaari", git_root="/tmp/kaari")
        assert "resume" in r and r["resume"]["project"] == "kaari"


class TestNote:
    def test_note_persists_to_db_and_file(self, tmp_db):
        pc, db, tmp_path = tmp_db
        root = tmp_path / "proj"
        root.mkdir()
        pc.attach("proj", git_root=str(root))
        pc.note("proj", "auth module uses JWT middleware", ide="opencode")
        conn = sqlite3.connect(db)
        rows = conn.execute("SELECT text FROM project_notes WHERE project='proj'").fetchall()
        conn.close()
        assert rows and "JWT" in rows[0][0]
        mem = root / ".nexus" / "memory.md"
        assert mem.exists() and "JWT" in mem.read_text()

    def test_note_without_attach_still_persists_to_db(self, tmp_db):
        pc, db, _ = tmp_db
        pc.note("ghost", "note with no attach")
        conn = sqlite3.connect(db)
        n = conn.execute("SELECT COUNT(*) FROM project_notes WHERE project='ghost'").fetchone()[0]
        conn.close()
        assert n == 1


class TestResume:
    def test_resume_empty_project(self, tmp_db):
        pc, *_ = tmp_db
        r = pc.resume("fresh-project")
        assert r["project"] == "fresh-project"
        assert r["recent_routes"] == [] and r["notes"] == []

    def test_resume_returns_notes_and_agents_md(self, tmp_db):
        pc, db, tmp_path = tmp_db
        root = tmp_path / "p2"
        root.mkdir()
        (root / "AGENTS.md").write_text("# P2\nBuild the trading dashboard.")
        pc.attach("p2", git_root=str(root))
        pc.note("p2", "prefer htmx over react here")
        r = pc.resume("p2")
        assert any("htmx" in n["text"] for n in r["notes"])
        assert r["agents_md"]["exists"] is True
        assert "trading dashboard" in r["agents_md"]["excerpt"]

    def test_resume_includes_recent_routes(self, tmp_db):
        pc, db, _ = tmp_db
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO routing_log (task_summary, project, matched_tier, matched_model, "
            "matched_skills, outcome, ide) VALUES ('fix auth bug', 'nexus', 2, 'sonnet', '[]', "
            "'routed', 'opencode')")
        conn.commit()
        conn.close()
        r = pc.resume("nexus")
        assert any("fix auth bug" in route["task_summary"] for route in r["recent_routes"])

    def test_resume_excludes_other_projects(self, tmp_db):
        pc, db, _ = tmp_db
        pc.note("a", "note for A only")
        pc.note("b", "note for B only")
        r = pc.resume("a")
        assert all("for A" in n["text"] for n in r["notes"])
