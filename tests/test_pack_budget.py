#!/usr/bin/env python3
"""S3 competition budget: slot logic, one-line notes, statement cap.

Stdlib only: python -m unittest tests.test_pack_budget
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

NEXUS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(NEXUS_ROOT))

from server.router_cutover import _pack_slots  # noqa: E402

DB_SCHEMA = """
CREATE TABLE user_learnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    note TEXT NOT NULL,
    scope TEXT DEFAULT '',
    project TEXT DEFAULT 'default',
    task_type TEXT DEFAULT 'general',
    created_at TEXT
);
CREATE TABLE resources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    resource_type TEXT NOT NULL,
    repo_name TEXT NOT NULL DEFAULT 'unknown',
    file_path TEXT NOT NULL,
    description TEXT DEFAULT '',
    body_text TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    tier INTEGER NOT NULL DEFAULT 2,
    token_count INTEGER DEFAULT 0,
    times_triggered INTEGER NOT NULL DEFAULT 0,
    times_correct INTEGER NOT NULL DEFAULT 0,
    times_wrong INTEGER NOT NULL DEFAULT 0,
    cluster TEXT,
    absorbed_at_commit TEXT,
    absorbed_at_date TEXT,
    invalidated_at TEXT
);
CREATE TABLE note_ledger (
    note_id INTEGER PRIMARY KEY,
    resource_name TEXT,
    claim_key TEXT,
    body_hash TEXT,
    valid_from TEXT NOT NULL,
    invalidated_at TEXT
);
CREATE TABLE resource_sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_id INTEGER NOT NULL,
    heading TEXT,
    content TEXT
);
CREATE TABLE hygiene_flags (
    resource_id INTEGER NOT NULL,
    flag TEXT NOT NULL,
    detail TEXT,
    found_at TEXT NOT NULL,
    resolved_at TEXT,
    PRIMARY KEY (resource_id, flag)
);
"""


class PackSlotsTests(unittest.TestCase):
    def test_default_pack_shrinks_to_three(self) -> None:
        self.assertEqual(_pack_slots({}, max_results=5), 3)
        self.assertEqual(_pack_slots({"cluster_decision": {
            "confidence": 0.9, "alternatives": []}}, max_results=5), 3)

    def test_explicit_caller_request_honored(self) -> None:
        self.assertEqual(_pack_slots({}, max_results=8), 8)
        self.assertEqual(_pack_slots({}, max_results=1), 1)
        self.assertEqual(_pack_slots({}, max_results=10), 10)

    def test_explicit_nonstandard_small_request(self) -> None:
        self.assertEqual(_pack_slots({}, max_results=2), 2)


class BrokerBlockTests(unittest.TestCase):
    """_build_context_block: one-line learning excerpts + 12-statement cap."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "t.db"
        con = sqlite3.connect(str(self.db))
        con.executescript(DB_SCHEMA)
        # one multi-line learning + one active resource with a stamp
        con.execute(
            "INSERT INTO user_learnings(note, project, created_at) "
            "VALUES(?, 'default', '2026-11-01')",
            ("First line of the lesson that should be kept at ~15 tokens.\n"
             "Second line that must NOT appear in the block.\n"
             "Third line also excluded.",))
        con.execute(
            "INSERT INTO user_learnings(note, project, created_at) "
            "VALUES(?, 'other-project', '2026-11-01')", ("should not appear",))
        con.execute(
            "INSERT INTO resources(name, resource_type, repo_name, file_path, "
            "body_text, absorbed_at_commit, absorbed_at_date) "
            "VALUES('alpha', 'skill', 'somerepo', 'skills/alpha/SKILL.md', "
            "'body text here long enough to be real', 'deadbeef00', '2026-11-17')")
        con.commit()
        con.close()

        # point the bridge at the temp DB, then import the broker lazily
        import server.context_harness.nexus_bridge as nb
        self._old_db_path = nb.DB_PATH
        nb.DB_PATH = self.db
        from server.tool_session_broker import ToolSessionBroker
        self.broker = ToolSessionBroker()

    def tearDown(self) -> None:
        import server.context_harness.nexus_bridge as nb
        nb.DB_PATH = self._old_db_path
        # Windows: a leaked sqlite handle (closed refs still map the file) can
        # lock the temp dir for a moment — collect and retry once.
        import gc
        gc.collect()
        try:
            self.tmp.cleanup()
        except PermissionError:
            gc.collect()
            import time as _t
            _t.sleep(0.2)
            try:
                self.tmp.cleanup()
            except PermissionError:
                pass  # OS prunes temp; the test verdicts stand

    def _block(self, route: dict | None = None, query: str = "alpha body text here") -> str:
        return self.broker._build_context_block(
            "default", query, route or {"cluster": "general", "task_type": "general",
                                        "model": "sonnet", "skills": []},
            ["tool-a", "tool-b"])

    def test_learning_is_one_line(self) -> None:
        block = self._block(query="kept lesson line excluded")
        learn = [l for l in block.splitlines() if l.startswith("- Learning")]
        self.assertTrue(learn, "no learning line found")
        self.assertIn("First line of the lesson", learn[0])
        self.assertNotIn("Second line that must NOT appear", learn[0])
        self.assertLessEqual(len(learn[0]), 150)
        self.assertIn("[noted 2026-11-01]", learn[0])

    def test_other_project_notes_excluded(self) -> None:
        block = self._block()
        self.assertNotIn("should not appear", block)

    def test_statement_cap_and_resource_stamp(self) -> None:
        block = self._block()
        content = [l for l in block.splitlines()
                   if l.startswith(("- Resource", "- Learning", "- Research"))]
        self.assertLessEqual(len(content), 12)
        res = [l for l in content if l.startswith("- Resource")]
        self.assertTrue(res)
        self.assertIn("somerepo @deadbeef · absorbed 2026-11-17", res[0])


if __name__ == "__main__":
    unittest.main()
