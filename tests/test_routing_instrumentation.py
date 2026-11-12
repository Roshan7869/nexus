"""Regression gate for routing_log instrumentation + traffic classification.

Context: an activity audit found 10 of 12 routing_log instrumentation columns
carried no information -- `action_type`, `intent`, `route_match`, `latency_ms`,
`cost` were 0.0% populated, and `tokens_used` / `injected_ctx_len` /
`repeat_count_1h` were DEFAULT 0 with `harness_tag` DEFAULT 'raw-api'. So
routing latency, cost, context injection and intent agreement were all
unmeasurable, and 26.4% of rows were machine-generated traffic inflating
quality aggregates.

These tests use an in-memory DB (see test_f5_affinity.py for the same
isolation rationale) and never touch db/nexus.db.
"""

import os
import sqlite3
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "server") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "server"))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from traffic_class import (  # noqa: E402
    EVAL, ORGANIC, SYNTHETIC, TEST,
    classify_traffic, is_synthetic,
)
SCHEMA = """
CREATE TABLE routing_log (
    id INTEGER PRIMARY KEY,
    task_summary TEXT, project TEXT, matched_tier INTEGER, matched_model TEXT,
    matched_skills TEXT, outcome TEXT, skill_name TEXT, ide TEXT,
    action_type TEXT, intent TEXT, route_match INTEGER, latency_ms REAL,
    cost REAL, tokens_used INTEGER, injected_ctx_len INTEGER, harness_tag TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
"""


class TestClassifyTraffic(unittest.TestCase):
    """P2: separate machine traffic from human-quality aggregates."""

    def test_genuine_work_is_organic(self):
        self.assertEqual(classify_traffic("fix the flaky login test"), ORGANIC)
        self.assertEqual(classify_traffic("add rate limiting", "nexus"), ORGANIC)

    def test_eval_ide_wins(self):
        self.assertEqual(classify_traffic("anything", "nexus", "eval"), EVAL)

    def test_test_projects(self):
        self.assertEqual(classify_traffic("x", "smoke"), TEST)
        self.assertEqual(classify_traffic("x", "STRESS"), TEST)  # case-insensitive

    def test_default_project_is_not_test(self):
        # 'default' carries 5,638 real routes -- must never be swept up.
        self.assertEqual(classify_traffic("real task", "default"), ORGANIC)

    def test_template_tasks_are_synthetic(self):
        self.assertEqual(classify_traffic("research: Research Context"), SYNTHETIC)
        self.assertEqual(classify_traffic("fix product image gallery"), SYNTHETIC)

    def test_precedence_ide_over_project_over_task(self):
        self.assertEqual(classify_traffic("x", "smoke", "eval"), EVAL)
        self.assertEqual(classify_traffic("deploy docker", "smoke"), TEST)

    def test_never_raises_on_junk(self):
        for args in ((None, None, None), ("", "", ""), ("x", None, None)):
            self.assertEqual(classify_traffic(*args), ORGANIC)

    def test_is_synthetic_helper(self):
        self.assertTrue(is_synthetic("fix the login bug"))
        self.assertFalse(is_synthetic("fix the real login bug"))


class TestLogDecisionInstrumentation(unittest.TestCase):
    """P0: the write site must actually persist the instrumentation columns."""

    def _fresh_db(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        return conn

    def test_write_site_populates_instrumentation(self):
        import router

        captured = {}

        class _Cur:
            rowcount = 1
            lastrowid = 1

            def execute(self, sql, params):
                captured["sql"] = sql
                captured["params"] = params
                return self

            def close(self):
                pass

        class _Conn:
            def __init__(self, *a, **k):
                self._closed = False

            def execute(self, *a, **k):
                return self

            def cursor(self):
                return _Cur()

            def commit(self):
                pass

            def close(self):
                self._closed = True

        real_connect = sqlite3.connect
        sqlite3.connect = lambda *a, **k: _Conn()
        try:
            router._log_decision(
                "audit landing page typography",
                {
                    "project": "nexus", "tier": 2, "model": "m",
                    "skills": [{"name": "qa", "_source": "intent"}],
                    "method": "v5", "cluster": "quality_security",
                    "latency_ms": 123.4,
                },
                outcome="correct", ide="opencode",
            )
        finally:
            sqlite3.connect = real_connect

        params = captured["params"]
        cols = [p.strip() for p in captured["sql"].split("(", 1)[1].split(")")[0].split(",")]
        self.assertEqual(len(cols), len(params), "column/placeholder count mismatch")
        row = dict(zip(cols, params))

        self.assertEqual(row["latency_ms"], 123.4)
        self.assertEqual(row["action_type"], "v5")
        self.assertEqual(row["intent"], "quality_security")
        self.assertEqual(row["route_match"], 1)
        self.assertEqual(row["harness_tag"], SYNTHETIC)
        self.assertEqual(row["skill_name"], "qa")

    def test_route_match_zero_for_non_intent_source(self):
        import router

        captured = {}

        class _Cur:
            lastrowid = 1

            def execute(self, sql, params):
                captured["params"] = params
                return self

        class _Conn:
            def execute(self, *a, **k):
                return self

            def cursor(self):
                return _Cur()

            def commit(self):
                pass

            def close(self):
                pass

        real = sqlite3.connect
        sqlite3.connect = lambda *a, **k: _Conn()
        try:
            router._log_decision(
                "real organic work", {
                    "project": "kaari", "tier": 1, "model": "m",
                    "skills": [{"name": "ship", "_source": "iso"}],
                    "method": "v5", "latency_ms": 9.9,
                },
                outcome="correct", ide="opencode",
            )
        finally:
            sqlite3.connect = real

        # params are positional in INSERT order; route_match is index 10,
        # harness_tag is index 15.
        self.assertEqual(captured["params"][10], 0)
        self.assertEqual(captured["params"][15], ORGANIC)


if __name__ == "__main__":
    unittest.main(verbosity=2)
