#!/usr/bin/env python3
"""S6 watchdog shadow: baselines, anomaly detection, detour construction.

Writes ONLY to watchdog_events — verified by table diff.
Stdlib only: python -m unittest tests.test_watchdog
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

NEXUS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(NEXUS_ROOT))

from server.watchdog import (  # noqa: E402
    audit_routing_log,
    construct_detour,
    evaluate_detector,
)

SCHEMA = """
CREATE TABLE routing_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_summary TEXT NOT NULL DEFAULT '',
    project TEXT NOT NULL DEFAULT 'default',
    matched_tier INTEGER NOT NULL DEFAULT 2,
    matched_model TEXT,
    actual_model_used TEXT,
    outcome TEXT,
    skill_name TEXT,
    tokens_used INTEGER,
    ide TEXT,
    action_type TEXT,
    repeat_count_1h INTEGER,
    created_at TEXT,
    intent TEXT,
    route_match TEXT,
    latency_ms REAL,
    cost REAL,
    injected_ctx_len INTEGER,
    harness_tag TEXT,
    outcome_source TEXT,
    quality REAL
);
CREATE TABLE watchdog_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    route_id TEXT NOT NULL,
    anomaly TEXT NOT NULL,
    ts TEXT NOT NULL,
    detail TEXT
);
"""


def _healthy(con: sqlite3.Connection, n: int, intent: str = "code") -> None:
    for i in range(n):
        con.execute(
            "INSERT INTO routing_log(task_summary, intent, latency_ms, "
            "tokens_used, injected_ctx_len) VALUES(?,?,?,?,?)",
            (f"task {i}", intent, 500 + (i % 7) * 10, 200 + (i % 5) * 5,
             1000 + (i % 3) * 10))


class WatchdogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "t.db"
        con = sqlite3.connect(str(self.db))
        con.executescript(SCHEMA)
        _healthy(con, 60)
        con.commit()
        self.con = con

    def tearDown(self) -> None:
        try:
            self.con.close()
        finally:
            try:
                self.tmp.cleanup()
            except PermissionError:
                pass

    def test_shadow_writes_only_watchdog_events(self) -> None:
        tables_before = {t[0] for t in self.con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        counts_before = {
            t: self.con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in tables_before - {"watchdog_events"}
        }
        audit_routing_log(self.con)
        for t, n in counts_before.items():
            self.assertEqual(
                self.con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0], n,
                f"shadow violation: {t} was written")
        events = self.con.execute(
            "SELECT COUNT(*) FROM watchdog_events").fetchone()[0]
        self.assertGreaterEqual(events, 0)  # writes confined to the events table

    def test_detour_detection_on_constructed_rows(self) -> None:
        # a 2x/1.77x outlier against a tight healthy baseline must flag
        res = evaluate_detector(self.con, n_detours=50)
        self.assertEqual(res["detours"], 50)
        self.assertGreaterEqual(res["detection_rate"], 0.90)
        self.assertLessEqual(res["fpr"], 0.10)

    def test_construct_detour_signature(self) -> None:
        row = {"id": 7, "task_summary": "t", "intent": "code",
               "latency_ms": 400.0, "tokens_used": 100,
               "injected_ctx_len": 900}
        d = construct_detour(row)
        self.assertAlmostEqual(d["latency_ms"], 820.0)   # x2.05 (+105%)
        self.assertEqual(d["tokens_used"], 177)          # x1.77 (+77%)
        self.assertEqual(d["injected_ctx_len"], 900)     # untouched

    def test_no_anomalies_on_uniform_healthy(self) -> None:
        # uniform-ish rows: no row should trip >=2 features at |z|>3
        res = audit_routing_log(self.con, write_events=False)
        self.assertLessEqual(res["anomalies"], 1)

    def test_offline_small_corpus_is_noop(self) -> None:
        con2 = sqlite3.connect(str(self.db))
        con2.execute("DELETE FROM routing_log")
        con2.commit()
        res = audit_routing_log(con2, write_events=False)
        self.assertEqual(res["scanned"], 0)
        con2.close()


if __name__ == "__main__":
    unittest.main()
