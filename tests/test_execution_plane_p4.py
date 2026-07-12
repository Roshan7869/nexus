#!/usr/bin/env python3
"""P4 execution plane unit tests."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

NEXUS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(NEXUS_ROOT))
sys.path.insert(0, str(NEXUS_ROOT / "server"))


class TestHealthV1(unittest.TestCase):
    def test_health_shape(self):
        from server.execution_plane import health_v1

        h = health_v1()
        for key in ("version", "contract", "git_sha", "router_mode", "db_ok", "flags"):
            self.assertIn(key, h)
        self.assertEqual(h["contract"], "nexus.execution_plane.v1")


class TestOpenReportFlow(unittest.TestCase):
    def test_open_requires_task(self):
        from server.execution_plane import open_session

        with self.assertRaises(ValueError):
            open_session("")

    def test_report_unknown_session(self):
        from server.execution_plane import report_session

        with self.assertRaises(KeyError):
            report_session("does-not-exist-zzzz", "correct")

    def test_report_invalid_outcome(self):
        from server.execution_plane import report_session
        from server.tool_session_broker import ToolSession, get_tool_session_broker

        broker = get_tool_session_broker()
        # inject fake active session
        sid = "testsessionp4xxxx"
        sess = ToolSession(
            session_id=sid,
            project="test",
            query="unit test task",
            tools_loaded=["investigate"],
            ide="http",
        )
        broker._active[sid] = sess
        try:
            with self.assertRaises(ValueError):
                report_session(sid, "not-a-real-outcome")
        finally:
            broker._active.pop(sid, None)


class TestBeliefDelta(unittest.TestCase):
    def test_delta_math(self):
        from server.execution_plane import _belief_delta

        before = {"qa": {"alpha": 1.0, "beta": 1.0}}
        after = {"qa": {"alpha": 2.0, "beta": 1.0}}
        d = _belief_delta(before, after)
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["delta_alpha"], 1.0)


if __name__ == "__main__":
    unittest.main()
