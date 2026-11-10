"""S2-a: production routing never grades its own output; the written row says 'routed'."""
import sqlite3
import sys
from pathlib import Path

import pytest

_SERVER = str(Path(__file__).resolve().parent.parent / "server")
if _SERVER not in sys.path:
    sys.path.insert(0, _SERVER)  # router.py has bare `from traffic_class import ...`

from server import router  # noqa: E402


@pytest.fixture
def routing_db(tmp_path, monkeypatch):
    path = tmp_path / "nexus.db"
    conn = sqlite3.connect(str(path))
    conn.execute("""CREATE TABLE routing_log(
        id INTEGER PRIMARY KEY, task_summary TEXT, project TEXT, matched_tier INTEGER,
        matched_model TEXT, matched_skills TEXT, outcome TEXT, skill_name TEXT, ide TEXT,
        action_type TEXT, intent TEXT, route_match INTEGER, latency_ms REAL, cost REAL,
        tokens_used INTEGER, injected_ctx_len INTEGER, harness_tag TEXT)""")
    conn.commit()
    conn.close()
    monkeypatch.setattr(router, "DB_PATH", path)
    monkeypatch.setattr(router, "THOMPSON_ROUTING", False)
    monkeypatch.setattr(router, "CLUSTER_ROUTING", False)
    return path


def test_written_row_is_routed_even_with_ground_truth(routing_db, monkeypatch):
    task = "fix the flaky login test"
    # A ground-truth entry that a self-grader would turn into 'correct'.
    monkeypatch.setattr(router, "_load_eval_dataset", lambda: {task: ["tdd-workflow"]})
    result = {"skills": [{"name": "tdd-workflow", "_source": "intent"}], "project": "p"}
    router._log_decision(task, result, ide="http")
    row = sqlite3.connect(str(routing_db)).execute(
        "SELECT outcome FROM routing_log").fetchall()
    assert row == [("routed",)]
