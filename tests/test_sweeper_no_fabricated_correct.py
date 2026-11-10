"""T0-2: the timeout sweeper must never write a terminal 'correct' label."""
import sqlite3

import pytest

from server import broker_runtime, learning


class _NoCloseConn:
    """Proxy so record_outcome/_get_conn cannot close the shared in-memory DB."""

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def close(self):
        pass


@pytest.fixture
def mem_db(monkeypatch):
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    raw.executescript(
        """
        CREATE TABLE resources (
            id INTEGER PRIMARY KEY, name TEXT UNIQUE, status TEXT DEFAULT 'active',
            times_triggered INTEGER DEFAULT 0, times_correct INTEGER DEFAULT 0,
            times_wrong INTEGER DEFAULT 0, updated_at TEXT
        );
        CREATE TABLE routing_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, task_summary TEXT, project TEXT,
            matched_tier INTEGER, matched_model TEXT, matched_skills TEXT,
            outcome TEXT, outcome_source TEXT, quality REAL, skill_name TEXT,
            tokens_used INTEGER DEFAULT 0, ide TEXT, created_at TEXT
                DEFAULT (datetime('now'))
        );
        INSERT INTO resources(name) VALUES ('skill-a');
        """
    )
    raw.execute(
        "INSERT INTO routing_log(task_summary, project, outcome, skill_name, ide, created_at)"
        " VALUES ('stale task', 'p1', NULL, 'skill-a', 'claude-code', '2000-01-01 00:00:00')"
    )
    raw.commit()
    proxy = _NoCloseConn(raw)
    monkeypatch.setattr(learning, "_get_conn", lambda: proxy)
    monkeypatch.setattr(broker_runtime, "_get_conn", lambda: proxy)
    yield raw
    raw.close()


def test_sweeper_writes_unverified_not_correct(mem_db):
    result = broker_runtime._close_routing_outcomes(
        _NoCloseConn(mem_db), project="p1", ide="claude-code", min_age_seconds=60
    )
    assert result.get("closed", 0) == 1

    res = mem_db.execute("SELECT times_correct, times_wrong, times_triggered FROM resources").fetchone()
    assert res["times_correct"] == 0
    assert res["times_wrong"] == 0
    assert res["times_triggered"] == 0

    row = mem_db.execute("SELECT outcome, outcome_source FROM routing_log").fetchone()
    # "unverified" is stored as the schema-valid telemetry label "routed".
    assert row["outcome"] == "routed"
    assert row["outcome_source"] == "timeout"
