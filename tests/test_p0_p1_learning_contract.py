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


if __name__ == "__main__":
    unittest.main()
