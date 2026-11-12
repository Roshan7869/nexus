#!/usr/bin/env python3
"""RCA-F5 regression test — cluster affinity must DIFFERENTIATE, not flatline.

Locks the fix in server/learning.py (Beta posterior mean on the write path) and
proves the live read path (get_cluster_affinity_scores) is independent of the
write column. See docs/rca/F5-affinity-flatline.md §7.
"""
import sys
import sqlite3
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "server") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "server"))

from server.learning import _update_cluster_affinity, get_cluster_affinity_scores


def _mk_conn():
    """Minimal in-memory schema — only the tables the two functions touch."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE cluster_definitions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT, trigger_keywords TEXT
        );
        CREATE TABLE resources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT, cluster TEXT, status TEXT
        );
        CREATE TABLE routing_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            skill_name TEXT, outcome TEXT
        );
        CREATE TABLE cluster_affinity_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cluster_name TEXT, trigger_query TEXT,
            old_affinity REAL, new_affinity REAL, reason TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    return conn


class TestF5AffinityDifferentiation(unittest.TestCase):

    def setUp(self):
        self.conn = _mk_conn()
        for cname, skill in (("clusterA", "skill-a"), ("clusterB", "skill-b")):
            self.conn.execute(
                "INSERT INTO cluster_definitions(name, trigger_keywords) VALUES(?, '')",
                (cname,))
            self.conn.execute(
                "INSERT INTO resources(name, cluster, status) VALUES(?, ?, 'active')",
                (skill, cname))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _feed(self, skill, cluster, n_correct, n_wrong):
        for _ in range(n_correct):
            self.conn.execute(
                "INSERT INTO routing_log(skill_name, outcome) VALUES(?, 'correct')", (skill,))
        for _ in range(n_wrong):
            self.conn.execute(
                "INSERT INTO routing_log(skill_name, outcome) VALUES(?, 'wrong')", (skill,))
        self.conn.commit()
        # One affinity write; it recomputes stats over the whole routing_log for the skill.
        _update_cluster_affinity(self.conn, cluster, skill, "correct")

    def _latest(self, cluster):
        return self.conn.execute(
            "SELECT new_affinity FROM cluster_affinity_history "
            "WHERE cluster_name=? ORDER BY id DESC LIMIT 1", (cluster,)
        ).fetchone()["new_affinity"]

    def test_clusters_differentiate_and_none_pinned(self):
        # RCA-F5 §7 gate 1: A 18/20 correct, B 5/20 correct.
        self._feed("skill-a", "clusterA", 18, 2)
        self._feed("skill-b", "clusterB", 5, 15)
        a, b = self._latest("clusterA"), self._latest("clusterB")
        self.assertGreater(a - b, 0.2, f"clusters must differ by > 0.2 (a={a}, b={b})")
        self.assertLess(a, 1.0, f"no cluster may pin at 1.0 (a={a})")
        self.assertLess(b, 1.0, f"no cluster may pin at 1.0 (b={b})")

    def test_all_correct_does_not_saturate(self):
        # The original bug: 20/20 correct ratcheted to exactly 1.0. Beta mean must stay < 1.0.
        self._feed("skill-a", "clusterA", 20, 0)
        a = self._latest("clusterA")
        self.assertLess(a, 1.0, f"20/20 correct must NOT pin at 1.0 (got {a})")
        self.assertGreater(a, 0.9, f"20/20 correct should still be high (got {a})")

    def test_read_path_independent_of_write_column(self):
        # RCA-F5 §7 gate 2: live read path recomputes correct/total from routing_log,
        # unaffected by the (dead) new_affinity write column.
        self._feed("skill-a", "clusterA", 18, 2)
        self._feed("skill-b", "clusterB", 5, 15)
        scores = get_cluster_affinity_scores(self.conn)
        self.assertAlmostEqual(scores["clusterA"], 18 / 20, places=3)
        self.assertAlmostEqual(scores["clusterB"], 5 / 20, places=3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
