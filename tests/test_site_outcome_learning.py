"""Harness-site outcome learning: only terminal outcomes (correct/wrong) move belief counters.

partial, routed, unverified (normalized to routed) and pending must write routing_log
telemetry only. Uses the in-memory DB from tests/conftest.py.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from conftest import make_db, mock_rw_conn, _NoCloseConnection

SKILL = "site-result-skill"

EXTRA_SCHEMA = """
ALTER TABLE resources ADD COLUMN updated_at TEXT;
CREATE TABLE IF NOT EXISTS routing_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_summary TEXT, project TEXT, matched_tier INTEGER, matched_model TEXT,
    matched_skills TEXT, outcome TEXT, skill_name TEXT, tokens_used INTEGER,
    ide TEXT, outcome_source TEXT, quality REAL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS model_beliefs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT, model TEXT, task_type TEXT,
    alpha REAL DEFAULT 1.0, beta REAL DEFAULT 1.0, total_requests INTEGER DEFAULT 0,
    updated_at TEXT
);
"""


@pytest.fixture
def conn():
    c = make_db(extra_schema=EXTRA_SCHEMA)
    c.execute(
        "INSERT INTO resources (id, name, times_correct, times_wrong, times_triggered) "
        "VALUES (1, ?, 5, 0, 5)",
        (SKILL,),
    )
    c.commit()
    yield c
    c.close()


def _counters(conn):
    row = conn.execute(
        "SELECT times_correct, times_wrong, times_triggered FROM resources WHERE name = ?",
        (SKILL,),
    ).fetchone()
    return (row["times_correct"], row["times_wrong"], row["times_triggered"])


def _record(conn, outcome):
    from server.learning import record_outcome

    with mock_rw_conn(conn), \
         patch("server.learning._get_conn", return_value=_NoCloseConnection(conn)):
        return record_outcome(SKILL, outcome, task_summary="site result used in routed task",
                              project="test", ide="pytest")


@pytest.mark.parametrize("outcome", ["partial", "skip", "routed", "unverified", "pending", None])
def test_non_terminal_outcomes_leave_counters_unchanged(conn, outcome):
    before = _counters(conn)
    result = _record(conn, outcome)

    assert _counters(conn) == before
    assert result["learning_applied"] is False
    assert result["status"] == "logged_non_terminal"
    # telemetry row still written
    assert conn.execute("SELECT COUNT(*) FROM routing_log").fetchone()[0] == 1


def test_partial_writes_telemetry_with_partial_label(conn):
    _record(conn, "partial")
    row = conn.execute("SELECT outcome FROM routing_log").fetchone()
    assert row["outcome"] == "partial"


def test_partial_does_not_touch_thompson_beliefs(conn):
    _record(conn, "partial")
    assert conn.execute("SELECT COUNT(*) FROM model_beliefs").fetchone()[0] == 0


def test_correct_outcome_updates_counters(conn):
    result = _record(conn, "correct")
    assert result.get("learning_applied", True) is not False
    assert _counters(conn) == (6.0, 0, 6)


def test_wrong_outcome_updates_counters(conn):
    _record(conn, "wrong")
    assert _counters(conn) == (5, 1.0, 6)
