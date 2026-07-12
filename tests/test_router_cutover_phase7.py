#!/usr/bin/env python3
"""Phase 7: Router v5 cutover tests."""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

NEXUS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(NEXUS_ROOT))
sys.path.insert(0, str(NEXUS_ROOT / "server"))

from server.router_cutover import (
    get_router_flag,
    get_router_status,
    route_for_task,
    compare_routers,
    run_ab_suite,
    TOP1_AGREEMENT_TARGET,
    DEFAULT_ROUTER_FLAG,
)


class TestRouterFlag(unittest.TestCase):
    def test_default_is_v5(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("USE_UNIFIED_ROUTER", None)
            self.assertEqual(get_router_flag(), DEFAULT_ROUTER_FLAG)
            self.assertEqual(DEFAULT_ROUTER_FLAG, "2")

    def test_env_override(self):
        with patch.dict(os.environ, {"USE_UNIFIED_ROUTER": "0"}):
            self.assertEqual(get_router_flag(), "0")


class TestRouterStatus(unittest.TestCase):
    def test_status_shape(self):
        status = get_router_status()
        for key in ("use_unified_router", "mode", "path", "production_sot"):
            self.assertIn(key, status)
        self.assertTrue(status["production_sot"])


class TestRouteDispatch(unittest.TestCase):
    @patch("server.router_cutover._shape_v5")
    def test_dispatches_v5_by_default(self, mock_v5):
        mock_v5.return_value = {"skills": [{"name": "test-skill"}], "method": "unified_router_v5"}
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("USE_UNIFIED_ROUTER", None)
            out = route_for_task("fix bug", "default", 5)
        mock_v5.assert_called_once()
        self.assertEqual(out["method"], "unified_router_v5")

    @patch("server.router_cutover._shape_v4")
    def test_dispatches_v4_when_flag_0(self, mock_v4):
        mock_v4.return_value = {"skills": [], "method": "route_task_v4"}
        with patch.dict(os.environ, {"USE_UNIFIED_ROUTER": "0"}):
            route_for_task("fix bug")
        mock_v4.assert_called_once()


class TestCompareRouters(unittest.TestCase):
    @patch("server.router_cutover.route_for_task")
    def test_compare_top1_match(self, mock_route):
        def side_effect(task, project, top_k, flag=None, **kwargs):
            names = {"0": "skill-a", "1": "skill-b", "2": "skill-a"}
            return {"skills": [{"name": names[flag]}], "method": f"m{flag}"}
        mock_route.side_effect = side_effect
        result = compare_routers("test query", top_k=3)
        self.assertTrue(result["top1_match"])
        self.assertEqual(result["v4_names"][0], "skill-a")
        self.assertEqual(result["v5_names"][0], "skill-a")


class TestABSuite(unittest.TestCase):
    @patch("server.router_cutover.compare_routers")
    def test_suite_gate_calculation(self, mock_compare):
        mock_compare.side_effect = [
            {"top1_match": True, "overlap_at_k": 3, "latencies_ms": {"v4": 10, "v5": 12}},
            {"top1_match": False, "overlap_at_k": 1, "latencies_ms": {"v4": 10, "v5": 12}},
        ]
        result = run_ab_suite(queries=["q1", "q2"], top_k=5)
        self.assertEqual(result["queries_run"], 2)
        self.assertEqual(result["top1_hits"], 1)
        self.assertEqual(result["top1_agreement"], 0.5)
        self.assertFalse(result["gate_pass"])
        self.assertEqual(result["top1_target"], TOP1_AGREEMENT_TARGET)


class TestMCPJSON(unittest.TestCase):
    def test_status_serializable(self):
        text = json.dumps(get_router_status())
        parsed = json.loads(text)
        self.assertEqual(parsed["mode"], "unified_v5")


if __name__ == "__main__":
    unittest.main()