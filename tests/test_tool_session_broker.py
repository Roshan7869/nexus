#!/usr/bin/env python3
"""Tests for ephemeral tool session broker (Phase 1)."""
import json
import sys
import unittest
from pathlib import Path

NEXUS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(NEXUS_ROOT))
sys.path.insert(0, str(NEXUS_ROOT / "server"))

from server.tool_session_broker import (
    ToolSessionBroker,
    ensure_tool_session_schema,
    get_tool_session_broker,
)
from server.ephemeral_mcp_proxy import BASELINE_TOOLS, merge_tool_surface, filter_mcp_tool_names
from server.db import db_conn, get_db_path


class TestEphemeralMcpProxy(unittest.TestCase):
    def test_baseline_always_visible(self):
        tools = merge_tool_surface(["ship", "qa"])
        self.assertIn("task_start", tools)
        self.assertIn("task_complete", tools)
        self.assertIn("ship", tools)

    def test_filter_without_session(self):
        all_tools = ["task_start", "find_skills", "ship", "harness_arxiv"]
        visible = filter_mcp_tool_names(all_tools, None)
        self.assertIn("task_start", visible)
        self.assertNotIn("ship", visible)


class TestToolSessionBroker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_tool_session_schema()
        with db_conn() as conn:
            cls.resource_count = conn.execute(
                "SELECT COUNT(*) FROM resources"
            ).fetchone()[0]
        if cls.resource_count == 0:
            raise unittest.SkipTest("nexus.db has no resources — restore DB first")

    def setUp(self):
        self.broker = ToolSessionBroker()

    def test_start_returns_limited_tools(self):
        session = self.broker.start_session(
            project="nexus",
            query="deploy docker to production",
            use_embedding=False,
            max_tools=8,
        )
        self.assertLessEqual(len(session.tools_loaded), 8)
        self.assertGreater(len(session.tools_loaded), 0)
        self.assertTrue(session.context_block)
        self.assertEqual(session.status, "active")

    def test_complete_dumps_session(self):
        session = self.broker.start_session(
            project="kaari",
            query="fix product image gallery",
            use_embedding=False,
        )
        sid = session.session_id
        result = self.broker.end_session(sid, outcome="correct", summary="tests pass")
        self.assertEqual(result["status"], "dumped")
        self.assertNotIn(sid, self.broker._active)

        with db_conn() as conn:
            row = conn.execute(
                "SELECT status, outcome FROM tool_sessions WHERE session_id = ?",
                (sid,),
            ).fetchone()
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["outcome"], "correct")

    def test_project_context_archived(self):
        session = self.broker.start_session(
            project="impeccable",
            query="audit landing page typography",
            use_embedding=False,
        )
        self.broker.end_session(session.session_id, outcome="correct", summary="done")
        with db_conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM project_contexts WHERE project = ?",
                ("impeccable",),
            ).fetchone()[0]
        self.assertGreaterEqual(count, 1)

    def test_singleton_broker(self):
        a = get_tool_session_broker()
        b = get_tool_session_broker()
        self.assertIs(a, b)


if __name__ == "__main__":
    unittest.main()