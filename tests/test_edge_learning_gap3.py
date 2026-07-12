#!/usr/bin/env python3
"""Gap 3: co-trigger edge learning + graph ranking boost."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

NEXUS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(NEXUS_ROOT))
sys.path.insert(0, str(NEXUS_ROOT / "server"))


def _mem_db() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(
        """
        CREATE TABLE resources (
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE,
            status TEXT DEFAULT 'active',
            times_triggered INTEGER DEFAULT 0,
            times_correct REAL DEFAULT 0,
            times_wrong REAL DEFAULT 0
        );
        CREATE TABLE resource_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            edge_type TEXT NOT NULL DEFAULT 'related_to',
            weight REAL NOT NULL DEFAULT 1.0,
            co_trigger_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(source_id, target_id, edge_type)
        );
        CREATE TABLE routing_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            skill_name TEXT,
            matched_skills TEXT,
            outcome TEXT
        );
        INSERT INTO resources(id, name) VALUES
            (1, 'skill-a'), (2, 'skill-b'), (3, 'skill-c');
        """
    )
    return con


class TestLearnCoTriggers(unittest.TestCase):
    def test_creates_pairs_and_counts(self):
        from server.learning import learn_co_triggers, edge_learning_stats

        con = _mem_db()
        r1 = learn_co_triggers(con, ["skill-a", "skill-b", "skill-c"], "correct")
        self.assertEqual(r1["upserted"], 3)  # 3 pairs
        r2 = learn_co_triggers(con, ["skill-a", "skill-b"], "correct")
        self.assertEqual(r2["upserted"], 1)
        row = con.execute(
            "SELECT weight, co_trigger_count FROM resource_edges "
            "WHERE edge_type='co_triggered' AND source_id=1 AND target_id=2"
        ).fetchone()
        self.assertEqual(row["co_trigger_count"], 2)
        self.assertGreater(row["weight"], 0.55)
        stats = edge_learning_stats(con)
        self.assertEqual(stats["co_triggered"], 3)
        self.assertEqual(stats["co_triggered_with_count"], 3)
        # Live gate needs ≥10 with_count (or ≥50 co edges); unit fixture is small.
        self.assertFalse(stats["edge_learning_gate_pass"])

    def test_single_skill_noop(self):
        from server.learning import learn_co_triggers

        con = _mem_db()
        r = learn_co_triggers(con, ["skill-a"], "correct")
        self.assertEqual(r["pairs"], 0)

class TestRecordOutcomeCoSkills(unittest.TestCase):
    def test_co_skills_param_accepted(self):
        from server.learning import record_outcome
        import inspect

        sig = inspect.signature(record_outcome)
        self.assertIn("co_skills", sig.parameters)


class TestGraphBoost(unittest.TestCase):
    def test_boost_increases_score(self):
        from server.router import _apply_edge_graph_boost

        con = _mem_db()
        con.execute(
            "INSERT INTO resource_edges"
            "(source_id, target_id, edge_type, weight, co_trigger_count) "
            "VALUES (1, 2, 'co_triggered', 0.9, 5)"
        )
        matches = [
            {"name": "skill-a", "score": 0.5, "resource_id": 1, "category": "REVIEW"},
            {"name": "skill-b", "score": 0.4, "resource_id": 2, "category": "REVIEW"},
            {"name": "skill-c", "score": 0.4, "resource_id": 3, "category": "REVIEW"},
        ]
        out = _apply_edge_graph_boost(con, matches)
        a = next(m for m in out if m["name"] == "skill-a")
        c = next(m for m in out if m["name"] == "skill-c")
        self.assertGreater(a["score"], 0.5)
        self.assertIn("graph_boost", a)
        self.assertEqual(c["score"], 0.4)  # no edges


if __name__ == "__main__":
    unittest.main()
