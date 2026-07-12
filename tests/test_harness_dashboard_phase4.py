#!/usr/bin/env python3
"""Phase 4: Harness Engine dashboard + IDE metrics."""
import json
import sys
import unittest
from pathlib import Path

NEXUS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(NEXUS_ROOT))
sys.path.insert(0, str(NEXUS_ROOT / "server"))

from server.ide_manager import IDEManager, launch_multi
from server.harness_metrics import (
    compute_global_metrics,
    compute_project_metrics,
    build_harness_dashboard,
    session_metrics_from_counters,
)
from server.tool_session_broker import ToolSessionBroker


class TestHarnessMetrics(unittest.TestCase):
    def test_global_metrics_keys(self):
        m = compute_global_metrics()
        for key in ("ftsr", "mrc", "tlt", "cdr"):
            self.assertIn(key, m)
            self.assertIsInstance(m[key], (int, float))
        self.assertGreaterEqual(m["mrc"], 1.0)

    def test_project_metrics(self):
        m = compute_project_metrics("nexus")
        self.assertEqual(m["project"], "nexus")

    def test_session_counters(self):
        m = session_metrics_from_counters(
            tools_routed=3, tools_succeeded=2, tools_failed=1, tools_loaded=4,
        )
        self.assertEqual(m["ftsr"], round(100.0 * 2 / 3, 2))
        self.assertGreaterEqual(m["mrc"], 1.0)
        self.assertGreater(m["tlt"], 0)


class TestIDEManagerPhase4(unittest.TestCase):
    def setUp(self):
        self.mgr = IDEManager()

    def test_harness_report_has_four_metrics(self):
        s = self.mgr.register("claude-code", 999, "tmux-1", project="kaari")
        self.mgr.link_tool_session(s.session_id, "tool-abc", tools_loaded=5, routing_time_ms=50)
        self.mgr.record_outcome(s.session_id, "correct", tokens=100, duration_ms=200)
        report = self.mgr.get_harness_report(s.session_id)
        for key in ("ftsr", "mrc", "tlt", "cdr"):
            self.assertIn(key, report, f"missing {key}")
        self.assertEqual(report["project"], "kaari")
        self.assertEqual(report["tool_session_id"], "tool-abc")

    def test_dashboard_include_metrics(self):
        self.mgr.register("opencode", 1001, project="nexus")
        self.mgr.register("codex", 1002, project="impeccable")
        dash = self.mgr.get_dashboard(include_metrics=True)
        self.assertEqual(dash["total_sessions"], 2)
        self.assertIn("global_metrics", dash)
        self.assertIn("by_project", dash)
        self.assertEqual(dash["by_project"]["nexus"]["session_count"], 1)
        self.assertEqual(dash["by_project"]["impeccable"]["session_count"], 1)

    def test_launch_multi_offline(self):
        """launch_multi is best-effort without tmux — returns entries either way."""
        results = launch_multi(["claude-code", "opencode"], project="nexus", register=True)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["ide_type"], "claude-code")
        self.assertEqual(results[1]["ide_type"], "opencode")


class TestHarnessDashboard(unittest.TestCase):
    def test_build_dashboard(self):
        dash = build_harness_dashboard()
        self.assertEqual(dash["status"], "ok")
        self.assertIn("global", dash)
        self.assertIn("summary", dash)
        for key in ("ftsr", "mrc", "tlt", "cdr"):
            self.assertIn(key, dash["summary"])

    def test_mcp_harness_dashboard_tool(self):
        from server.harness_metrics import build_harness_dashboard
        raw = json.dumps(build_harness_dashboard("nexus"))
        data = json.loads(raw)
        self.assertIn("scoped", data)


class TestToolSessionIDELink(unittest.TestCase):
    def test_ide_link_on_start_complete(self):
        from server import ide_manager as ide_mod
        ide_mod._MANAGER = IDEManager()
        mgr = ide_mod.get_manager()
        ide = mgr.register("claude-code", 42, project="nexus")
        broker = ToolSessionBroker()
        session = broker.start_session(
            project="nexus",
            query="deploy docker",
            ide_session_id=ide.session_id,
        )
        linked = mgr.get(ide.session_id)
        self.assertEqual(linked.tool_session_id, session.session_id)
        broker.end_session(
            session.session_id,
            outcome="correct",
            summary="done",
            ide_session_id=ide.session_id,
        )
        linked = mgr.get(ide.session_id)
        self.assertEqual(linked.tools_routed, 1)
        self.assertEqual(linked.tools_succeeded, 1)


if __name__ == "__main__":
    unittest.main()