#!/usr/bin/env python3
"""P0/P1: non-terminal learning guard + pure rank + session open."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

NEXUS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(NEXUS_ROOT))
sys.path.insert(0, str(NEXUS_ROOT / "server"))


class TestNonTerminalLearning(unittest.TestCase):
    def test_routed_does_not_apply_learning(self):
        from server.learning import record_outcome

        with patch("server.learning._get_conn") as mock_conn:
            conn = MagicMock()
            mock_conn.return_value = conn
            out = record_outcome(
                "some-skill", "routed",
                task_summary="unit test", project="test", ide="pytest",
            )
        self.assertEqual(out["status"], "logged_non_terminal")
        self.assertFalse(out["learning_applied"])
        # Must not run counter UPDATE for times_wrong
        executed = " ".join(
            str(c) for c in conn.execute.call_args_list
        )
        self.assertNotIn("times_wrong", executed)

    def test_pending_non_terminal(self):
        from server.learning import record_outcome

        with patch("server.learning._get_conn") as mock_conn:
            conn = MagicMock()
            mock_conn.return_value = conn
            out = record_outcome("x", "pending", ide="pytest")
        self.assertFalse(out["learning_applied"])

class TestIntentTelemetry(unittest.TestCase):
    def test_routed_does_not_call_record_outcome(self):
        from server.intent_classifier import log_intent_telemetry

        with patch("server.learning.record_outcome") as mock_ro:
            log_intent_telemetry("build react app", ["react-skill"], "routed")
        mock_ro.assert_not_called()

    def test_wrong_delegates_to_record_outcome(self):
        from server.intent_classifier import log_intent_telemetry

        with patch("server.learning.record_outcome") as mock_ro:
            log_intent_telemetry("fix bug", ["debug-skill", "alt-skill"], "wrong")
        mock_ro.assert_called_once()
        args, kwargs = mock_ro.call_args
        self.assertEqual(args[0], "debug-skill")
        self.assertEqual(args[1], "wrong")
        self.assertEqual(kwargs.get("co_skills"), ["alt-skill"])

    def test_routing_log_write_failure_is_fail_loud_not_swallowed(self):
        """G13: a swallowed routing_log write vanishes from both sides of the
        coverage ratio. The non-terminal 'routed' path must log NEXUS_WRITE_FAIL
        and must NOT raise."""
        import server.intent_classifier as ic

        with patch.object(ic.sqlite3, "connect", side_effect=OSError("db locked")):
            with self.assertLogs("server.intent_classifier", level="WARNING") as cm:
                # 'routed' is non-terminal → routing_log INSERT path (not record_outcome)
                ic.log_intent_telemetry("build react app", ["react-skill"], "routed")
        blob = "\n".join(cm.output)
        self.assertIn("NEXUS_WRITE_FAIL", blob)
        self.assertIn("op=routing_log", blob)


class TestPureRankFlag(unittest.TestCase):
    def test_route_task_accepts_write_feedback(self):
        import inspect
        from server.router import route_task

        sig = inspect.signature(route_task)
        self.assertIn("write_feedback", sig.parameters)


class TestSessionOpen(unittest.TestCase):
    def test_route_for_task_session_fields(self):
        from server.router_cutover import route_for_task

        fake = {
            "skills": [{"name": "qa"}],
            "method": "unified_router_v5",
            "tier": 2,
            "model": "sonnet",
        }
        with patch("server.router_cutover._shape_v5", return_value=dict(fake)):
            with patch(
                "server.router_cutover.log_session_open", return_value=42
            ) as mock_log:
                with patch.dict(os.environ, {"USE_UNIFIED_ROUTER": "2"}):
                    out = route_for_task(
                        "test", "default", 5, open_session=True, ide="pytest",
                    )
        mock_log.assert_called_once()
        self.assertEqual(out["session_log_id"], 42)
        self.assertEqual(out["session_outcome"], "routed")
        self.assertEqual(out["router_flag"], 2)

    def test_ab_compare_skips_session(self):
        from server.router_cutover import compare_routers

        with patch("server.router_cutover.route_for_task") as mock_route:
            mock_route.return_value = {
                "skills": [{"name": "a"}], "method": "m",
            }
            compare_routers("q", top_k=1)
        # All three flags called with open_session=False
        for call in mock_route.call_args_list:
            self.assertFalse(call.kwargs.get("open_session", True))


class TestG12RouteLifecycle(unittest.TestCase):
    """G12: timeout-gated auto-close + outcome_source audit marker."""

    def _conn(self):
        import sqlite3
        from conftest import make_db, _NoCloseConnection
        from server.db import ensure_routing_log_schema
        # get_feedback_coverage also reads outcome_log + tool_sessions; provide
        # minimal shapes so the coverage assertions exercise the real query.
        conn = make_db(extra_schema="""
            CREATE TABLE IF NOT EXISTS outcome_log (id INTEGER PRIMARY KEY,
                efc_score REAL);
            CREATE TABLE IF NOT EXISTS tool_sessions (id INTEGER PRIMARY KEY,
                status TEXT, outcome TEXT);
        """)
        ensure_routing_log_schema(conn)  # relax CHECK + add outcome_source/quality
        conn.execute(
            "INSERT INTO resources (id, name, description, body_text, resource_type,"
            " cluster, status, tier, token_count, times_correct, times_wrong,"
            " times_triggered) VALUES (99,'g12-skill','d','b','skill','general-dev',"
            "'active',3,10,0,0,0)"
        )
        conn.commit()
        return conn, _NoCloseConnection(conn)

    def test_explicit_outcome_stamps_source_explicit(self):
        from server.learning import record_outcome
        raw, noc = self._conn()
        with patch("server.learning._get_conn", return_value=noc):
            record_outcome("g12-skill", "correct", project="test", ide="pytest",
                           routed_id=None)
        row = raw.execute(
            "SELECT outcome, outcome_source, quality FROM routing_log"
            " WHERE skill_name='g12-skill' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(row["outcome"], "correct")
        self.assertEqual(row["outcome_source"], "explicit")   # default
        self.assertEqual(row["quality"], 1.0)
        raw.close()

    def test_timeout_gate_leaves_fresh_row_open(self):
        """A just-created routed row must NOT be auto-closed (min_age gate)."""
        from server.broker_runtime import _close_routing_outcomes
        raw, noc = self._conn()
        raw.execute(
            "INSERT INTO routing_log(task_summary,project,matched_tier,matched_model,"
            "matched_skills,outcome,skill_name,ide,created_at) VALUES"
            "('t','test',0,'m','[]','routed','g12-skill','pytest',datetime('now'))"
        )
        raw.commit()
        with patch("server.learning._get_conn", return_value=noc):
            res = _close_routing_outcomes(raw, project="test", min_age_seconds=60)
        self.assertEqual(res.get("closed", 0), 0)          # fresh → untouched
        still = raw.execute(
            "SELECT outcome FROM routing_log WHERE skill_name='g12-skill'"
        ).fetchone()
        self.assertEqual(still["outcome"], "routed")       # still open
        raw.close()

    def test_timeout_close_stamps_source_timeout(self):
        """Past the gate, the row auto-closes as correct/timeout, quality 0.3."""
        from server.broker_runtime import _close_routing_outcomes
        raw, noc = self._conn()
        raw.execute(
            "INSERT INTO routing_log(task_summary,project,matched_tier,matched_model,"
            "matched_skills,outcome,skill_name,ide,created_at) VALUES"
            "('t','test',0,'m','[]','routed','g12-skill','pytest',datetime('now'))"
        )
        raw.commit()
        with patch("server.learning._get_conn", return_value=noc):
            res = _close_routing_outcomes(raw, project="test", min_age_seconds=0)
        self.assertEqual(res["closed"], 1)
        self.assertEqual(res["min_age_seconds"], 0)
        row = raw.execute(
            "SELECT outcome, outcome_source, quality FROM routing_log"
            " WHERE skill_name='g12-skill'"
        ).fetchone()
        self.assertEqual(row["outcome"], "correct")
        self.assertEqual(row["outcome_source"], "timeout")  # derived, not explicit
        self.assertEqual(row["quality"], 0.3)
        raw.close()

    def test_coverage_exposes_explicit_vs_derived_split(self):
        from server.rl_feedback_loop import get_feedback_coverage
        raw, _ = self._conn()
        for src, oc in [("explicit", "correct"), ("timeout", "correct"),
                        ("explicit", "wrong")]:
            raw.execute(
                "INSERT INTO routing_log(task_summary,project,matched_tier,"
                "matched_model,matched_skills,outcome,skill_name,ide,"
                "outcome_source,quality) VALUES('t','test',0,'m','[]',?,'g12-skill',"
                "'pytest',?,1.0)", (oc, src)
            )
        raw.commit()
        cov = get_feedback_coverage(conn=raw)
        self.assertEqual(cov["by_outcome_source"]["explicit"], 2)
        self.assertEqual(cov["by_outcome_source"]["timeout"], 1)
        self.assertEqual(cov["explicit_with_outcome"], 2)
        self.assertEqual(cov["derived_with_outcome"], 1)
        # Headline formula unchanged: 3 terminal rows / 3 total = 100%
        self.assertEqual(cov["routing_coverage_pct"], 100.0)
        raw.close()


if __name__ == "__main__":
    unittest.main()
