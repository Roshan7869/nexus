"""S1-d: open_session must leave ended_at NULL on the persisted row.

Runs against a temporary database (never the live db/nexus.db): the path is
redirected by patching server.db.get_db_path, which get_connection reads at
call time.
"""
from unittest.mock import patch

import pytest

import server.db as dbmod


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    path = tmp_path / "nexus_test.db"
    monkeypatch.setattr(dbmod, "get_db_path", lambda: path)
    from server.tool_session_broker import ensure_tool_session_schema
    conn = dbmod.get_connection()
    try:
        ensure_tool_session_schema(conn)
        conn.commit()
    finally:
        dbmod.close_connection(conn)
    return path


def test_open_session_leaves_ended_at_null(tmp_db):
    from server import execution_plane as ep
    route = {"skills": [{"name": "alpha_skill"}, {"name": "beta_skill"}]}
    with patch("server.router_cutover.route_for_task", return_value=route):
        out = ep.open_session("s1-d open check task", project="s1d-test",
                              use_embedding=False)
    sid = out["session_id"]
    with dbmod.db_conn() as conn:
        row = conn.execute(
            "SELECT status, ended_at FROM tool_sessions WHERE session_id = ?", (sid,)
        ).fetchone()
    assert row is not None
    assert row["ended_at"] is None
