#!/usr/bin/env python3
"""Phase 5: RL loop closure tests."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

NEXUS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(NEXUS_ROOT))
sys.path.insert(0, str(NEXUS_ROOT / "server"))

from server.rl_feedback_loop import (
    handle_ide_event,
    get_feedback_coverage,
    distill_project_learnings,
    search_project_learnings,
    SUCCESS_EVENTS,
    FAILURE_EVENTS,
    OUTCOME_MAP,
)


class TestRLFeedbackConstants(unittest.TestCase):
    def test_event_sets(self):
        self.assertIn("test_passed", SUCCESS_EVENTS)
        self.assertIn("task_completed", SUCCESS_EVENTS)
        self.assertIn("test_failed", FAILURE_EVENTS)

    def test_outcome_map(self):
        self.assertEqual(OUTCOME_MAP["test_passed"], "correct")
        self.assertEqual(OUTCOME_MAP["test_failed"], "wrong")


class TestFeedbackCoverage(unittest.TestCase):
    def test_coverage_keys(self):
        cov = get_feedback_coverage()
        for key in (
            "routing_coverage_pct", "efc_coverage_pct",
            "routing_log_total", "tool_sessions_completed",
        ):
            self.assertIn(key, cov)


class TestHandleIDEEvent(unittest.TestCase):
    @patch("server.rl_feedback_loop._close_stale_routes")
    @patch("server.rl_feedback_loop._safe_record_outcome")
    @patch("server.rl_feedback_loop.get_tool_session_broker")
    def test_test_passed_auto_complete(self, mock_broker_fn, mock_record, mock_close):
        mock_broker = MagicMock()
        mock_broker_fn.return_value = mock_broker
        mock_broker.list_active_sessions.return_value = [{"session_id": "sess1"}]
        mock_session = MagicMock()
        mock_session.session_id = "sess1"
        mock_session.status = "active"
        mock_session.tools_loaded = ["ship", "qa"]
        mock_session.query = "deploy app"
        mock_session.project = "nexus"
        mock_session.ide = "claude-code"
        mock_broker.get_session.return_value = mock_session
        mock_broker.end_session.return_value = {"status": "dumped", "session_id": "sess1"}
        mock_close.return_value = {"closed": 2}

        result = handle_ide_event(
            "test_passed",
            project="nexus",
            metadata={"project": "nexus"},
        )
        self.assertTrue(result["handled"])
        self.assertEqual(result["status"], "success_closed")
        mock_broker.end_session.assert_called()
        self.assertEqual(mock_record.call_count, 2)

    @patch("server.rl_feedback_loop._reflect_and_suggest")
    @patch("server.rl_feedback_loop.get_tool_session_broker")
    def test_test_failed_reflection(self, mock_broker_fn, mock_reflect):
        mock_broker = MagicMock()
        mock_broker_fn.return_value = mock_broker
        mock_broker.get_session.return_value = None
        mock_reflect.return_value = {
            "alternative_skill": "qa",
            "diagnosis": "tests failed",
            "candidates": ["qa", "browse"],
        }

        result = handle_ide_event(
            "test_failed",
            project="kaari",
            metadata={"skill_name": "design-html", "query": "fix gallery", "error": "assertion"},
        )
        self.assertTrue(result["handled"])
        self.assertEqual(result["alternative_skill"], "qa")
        self.assertTrue(result["auto_retry_recommended"])
        mock_reflect.assert_called_once()


class TestDistillation(unittest.TestCase):
    def test_distill_returns_structure(self):
        result = distill_project_learnings("nexus", days=30)
        self.assertIn("status", result)
        self.assertEqual(result["project"], "nexus")

    def test_search_includes_sources(self):
        result = search_project_learnings(project="nexus", limit=5)
        self.assertIn("sources", result)
        self.assertIn("project_contexts", result["sources"])


class TestMCPToolsPhase5(unittest.TestCase):
    def test_feedback_coverage_callable(self):
        from server.rl_feedback_loop import get_feedback_coverage
        raw = json.dumps(get_feedback_coverage())
        data = json.loads(raw)
        self.assertIn("routing_coverage_pct", data)


if __name__ == "__main__":
    unittest.main()