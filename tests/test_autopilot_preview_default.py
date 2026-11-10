"""T0-3: create_task_run defaults to preview only; no execution without opt-in."""
import inspect
import re
import sqlite3
from pathlib import Path

import conftest
import pytest

from server import broker_runtime

PLANS_DDL = """
CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal TEXT NOT NULL,
    project TEXT NOT NULL DEFAULT 'default',
    phases_json TEXT NOT NULL DEFAULT '[]',
    current_phase INTEGER NOT NULL DEFAULT 0,
    phases_completed TEXT NOT NULL DEFAULT '[]',
    phases_remaining TEXT NOT NULL DEFAULT '[]',
    artifacts TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'planning',
    plan_type TEXT NOT NULL DEFAULT 'adaptive',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    path = tmp_path / "broker.db"

    def _conn():
        c = sqlite3.connect(str(path), timeout=30)
        c.row_factory = sqlite3.Row
        return c

    def _init():
        c = _conn()
        c.executescript(conftest.SCHEMA_SQL + PLANS_DDL)
        c.close()
        broker_runtime.ensure_runtime_schema()

    monkeypatch.setattr(broker_runtime, "_get_conn", _conn)
    monkeypatch.setattr(broker_runtime, "initialize_runtime", _init)
    monkeypatch.setattr(broker_runtime, "route_for_task", lambda *a, **k: {
        "cluster": "general", "task_type": "general", "candidates": [], "skills": []})
    return path


def _sentinel(*a, **k):
    raise AssertionError("execute_task_run must not run without explicit opt-in")


def test_default_is_preview_only(temp_db, monkeypatch):
    monkeypatch.setattr(broker_runtime, "execute_task_run", _sentinel)
    result = broker_runtime.create_task_run("summarize the readme")
    assert result["status"] == "previewed"


def test_default_value_is_false():
    default = inspect.signature(broker_runtime.create_task_run).parameters["auto_execute"].default
    assert default is False


def test_mcp_and_unified_defaults_false():
    root = Path(broker_runtime.__file__).parent
    assert re.search(r"auto_execute: bool = False\) -> str:", (root / "nexus-mcp-enhanced.py").read_text())
    assert re.search(r"auto_execute: bool = False\n", (root / "unified.py").read_text())
