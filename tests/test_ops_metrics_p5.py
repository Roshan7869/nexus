#!/usr/bin/env python3
"""P5 ops metrics unit tests."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

NEXUS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(NEXUS_ROOT))
sys.path.insert(0, str(NEXUS_ROOT / "server"))


class TestOpsReport(unittest.TestCase):
    def test_build_ops_report_shape(self):
        from server.ops_metrics import build_ops_report

        report = build_ops_report(include_ab=False)
        for key in (
            "phase", "overall_pass", "gates", "routing_coverage",
            "catalog", "router", "harness", "learning_integrity",
        ):
            self.assertIn(key, report)
        self.assertEqual(report["phase"], "P5")
        # Required gates present
        for g in (
            "terminal_close_rate", "router_mode_match",
            "zero_token_active", "cluster_divergence", "db_ok",
            "invoke_cold_rate", "process_topology", "edge_learning",
            "ide_event_loop", "okf_residual", "chunk_resource_link",
        ):
            self.assertIn(g, report["gates"])
            self.assertIn("pass", report["gates"][g])
        self.assertIn("process_status", report)
        self.assertIn("edge_learning", report)
        self.assertIn("ide_events", report)
        self.assertIn("chunk_resource_link", report)

    def test_routing_coverage_fields(self):
        from server.ops_metrics import routing_coverage

        cov = routing_coverage()
        self.assertIn("terminal_close_rate_pct", cov)
        self.assertIn("terminal_close_gate_pass", cov)
        self.assertGreaterEqual(cov["total_routes"], 0)

    def test_ab_quick_mocked(self):
        from server.ops_metrics import run_ab_eval

        fake = {
            "queries_run": 5,
            "top1_agreement": 1.0,
            "top1_target": 0.95,
            "gate_pass": True,
            "top1_agreement_pct": 100.0,
        }
        with patch("server.router_cutover.run_ab_suite", return_value=dict(fake)):
            out = run_ab_eval(quick=True)
        self.assertEqual(out["mode"], "quick")
        self.assertTrue(out["gate_pass"])
        self.assertIn("elapsed_ms", out)


if __name__ == "__main__":
    unittest.main()
