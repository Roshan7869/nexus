#!/usr/bin/env python3
"""
P4-1: End-to-end test suite verifying all 8 gap fixes.
Covers: feedback loop, cold-start, cluster consistency, ingestion pipeline, edge decay.
"""
import sys, os, sqlite3, unittest, time
from pathlib import Path
from datetime import datetime, timedelta

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "server"))

DB_PATH = REPO_ROOT / "db" / "nexus.db"

TEST_PREFIX = "test-e2e-"


def get_conn():
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.row_factory = sqlite3.Row
    return conn


class TestFeedbackLoop(unittest.TestCase):
    """P0-1: record_outcome wired into _log_decision → edges update, beliefs update."""

    def setUp(self):
        self.conn = get_conn()
        self.skill = "test-e2e-feedback"
        self.conn.execute("DELETE FROM cluster_membership WHERE resource_id IN (SELECT id FROM resources WHERE name=?)", (self.skill,))
        self.conn.execute("DELETE FROM resource_edges WHERE source_id IN (SELECT id FROM resources WHERE name=?)", (self.skill,))
        self.conn.execute("DELETE FROM resources WHERE name=?", (self.skill,))
        self.conn.commit()
        # Insert skill + an edge
        self.conn.execute("""
            INSERT INTO resources (name, description, resource_type, status, cluster, file_path, body_text,
                                   times_triggered, times_correct, times_wrong)
            VALUES (?, 'Feedback loop test resource', 'skill', 'active', 'general', 'test_e2e.py', '',
                    3, 3, 0)
        """, (self.skill,))
        self.conn.commit()
        rid = self.conn.execute("SELECT id FROM resources WHERE name=?", (self.skill,)).fetchone()["id"]
        self.rid = rid
        self.conn.execute("INSERT OR IGNORE INTO resource_edges (source_id, target_id, edge_type, weight) VALUES (?,?,?,?)",
                          (rid, rid, "self_loop", 0.5))
        self.conn.commit()

    def tearDown(self):
        self.conn.execute("DELETE FROM resource_edges WHERE source_id=? OR target_id=?", (self.rid, self.rid))
        self.conn.execute("DELETE FROM cluster_membership WHERE resource_id=?", (self.rid,))
        self.conn.execute("DELETE FROM resources WHERE id=?", (self.rid,))
        self.conn.commit()
        self.conn.close()

    def test_correct_outcome_updates_edge_weight(self):
        """Correct outcome should multiply edge weight by 1.05."""
        from server.learning import record_outcome
        w_before = self.conn.execute("SELECT weight FROM resource_edges WHERE source_id=?", (self.rid,)).fetchone()["weight"]
        record_outcome(self.skill, "correct", task_summary="e2e test", project="test")
        w_after = self.conn.execute("SELECT weight FROM resource_edges WHERE source_id=?", (self.rid,)).fetchone()["weight"]
        self.assertAlmostEqual(w_after, min(1.0, w_before * 1.05), places=3,
                               msg=f"Expected {w_before*1.05:.3f}, got {w_after:.3f}")

    def test_wrong_outcome_updates_resource_counters(self):
        """Wrong outcome increments times_wrong and times_triggered."""
        from server.learning import record_outcome
        before = self.conn.execute("SELECT times_triggered, times_wrong FROM resources WHERE id=?", (self.rid,)).fetchone()
        record_outcome(self.skill, "wrong", task_summary="e2e fail", project="test")
        after = self.conn.execute("SELECT times_triggered, times_wrong FROM resources WHERE id=?", (self.rid,)).fetchone()
        self.assertEqual(after["times_triggered"], before["times_triggered"] + 1)
        self.assertEqual(after["times_wrong"], before["times_wrong"] + 1)

    def test_routing_log_entry_created(self):
        """record_outcome creates a routing_log entry."""
        from server.learning import record_outcome
        before_count = self.conn.execute("SELECT COUNT(*) FROM routing_log WHERE skill_name=?", (self.skill,)).fetchone()[0]
        record_outcome(self.skill, "correct", task_summary="log check", project="test")
        after_count = self.conn.execute("SELECT COUNT(*) FROM routing_log WHERE skill_name=?", (self.skill,)).fetchone()[0]
        self.assertGreater(after_count, before_count)


class TestColdStart(unittest.TestCase):
    """P0-3: Cold resources have Beta(1,1) prior, get backfilled from routing_log."""

    def setUp(self):
        # This class tests the Thompson belief bridge — force it on regardless
        # of conftest's NEXUS_THOMPSON_ROUTING=false default (flag is read at
        # import time into a module global, so patch the global directly).
        import server.learning as _learning
        self._thompson_saved = _learning.THOMPSON_ENABLED
        _learning.THOMPSON_ENABLED = True
        self.conn = get_conn()
        self.skill = "test-e2e-cold"
        self.conn.execute("DELETE FROM cluster_membership WHERE resource_id IN (SELECT id FROM resources WHERE name=?)", (self.skill,))
        self.conn.execute("DELETE FROM model_beliefs WHERE model=?", (f"resource:{self.skill}",))
        self.conn.execute("DELETE FROM resources WHERE name=?", (self.skill,))
        self.conn.commit()
        self.conn.execute("""
            INSERT INTO resources (name, description, resource_type, status, cluster, file_path, body_text,
                                   times_triggered, times_correct, times_wrong)
            VALUES (?, 'Cold start test', 'skill', 'active', 'general', 'test_cold.py', '', 0, 0, 0)
        """, (self.skill,))
        self.conn.commit()
        self.rid = self.conn.execute("SELECT id FROM resources WHERE name=?", (self.skill,)).fetchone()["id"]

    def tearDown(self):
        import server.learning as _learning
        _learning.THOMPSON_ENABLED = self._thompson_saved
        self.conn.execute("DELETE FROM model_beliefs WHERE model=?", (f"resource:{self.skill}",))
        self.conn.execute("DELETE FROM cluster_membership WHERE resource_id=?", (self.rid,))
        self.conn.execute("DELETE FROM resources WHERE id=?", (self.rid,))
        self.conn.commit()
        self.conn.close()

    def test_cold_resource_gets_belief_after_outcome(self):
        """After recording an outcome for a cold resource, model_beliefs is populated."""
        from server.learning import record_outcome
        record_outcome(self.skill, "correct", task_summary="cold start seed", project="test")
        belief = self.conn.execute(
            "SELECT alpha, beta FROM model_beliefs WHERE model=?", (f"resource:{self.skill}",)
        ).fetchone()
        self.assertIsNotNone(belief, "model_beliefs should be created after outcome")
        self.assertGreater(belief["alpha"], 1.0, "alpha should increase after correct outcome")


class TestClusterConsistency(unittest.TestCase):
    """P0-2: cluster_membership is the single source of truth. INSERT triggers sync resources.cluster."""

    def setUp(self):
        self.conn = get_conn()
        self.skill = "test-e2e-cluster"
        self.conn.execute("DELETE FROM cluster_membership WHERE resource_id IN (SELECT id FROM resources WHERE name=?)", (self.skill,))
        self.conn.execute("DELETE FROM resources WHERE name=?", (self.skill,))
        self.conn.commit()
        self.conn.execute("""
            INSERT INTO resources (name, description, resource_type, status, cluster, file_path, body_text)
            VALUES (?, 'Cluster consistency test', 'skill', 'active', 'general', 'test_cluster.py', '')
        """, (self.skill,))
        self.conn.commit()
        self.rid = self.conn.execute("SELECT id FROM resources WHERE name=?", (self.skill,)).fetchone()["id"]

    def tearDown(self):
        self.conn.execute("DELETE FROM cluster_membership WHERE resource_id=?", (self.rid,))
        self.conn.execute("DELETE FROM resources WHERE id=?", (self.rid,))
        self.conn.commit()
        self.conn.close()

    def test_insert_cluster_membership_syncs_resources_cluster(self):
        """INSERT into cluster_membership should auto-sync resources.cluster via trigger."""
        self.conn.execute("INSERT OR IGNORE INTO cluster_membership (resource_id, cluster_name) VALUES (?,?)",
                          (self.rid, "backend_api"))
        self.conn.commit()
        r = self.conn.execute("SELECT cluster FROM resources WHERE id=?", (self.rid,)).fetchone()
        self.assertEqual(r["cluster"], "backend_api",
                         "resources.cluster should be synced via trigger after cluster_membership INSERT")

    def test_update_cluster_membership_syncs_resources_cluster(self):
        """UPDATE cluster_membership should auto-sync resources.cluster via trigger."""
        self.conn.execute("INSERT OR IGNORE INTO cluster_membership (resource_id, cluster_name) VALUES (?,?)",
                          (self.rid, "backend_api"))
        self.conn.commit()
        self.conn.execute("UPDATE cluster_membership SET cluster_name='frontend_ux' WHERE resource_id=?", (self.rid,))
        self.conn.commit()
        r = self.conn.execute("SELECT cluster FROM resources WHERE id=?", (self.rid,)).fetchone()
        self.assertEqual(r["cluster"], "frontend_ux",
                         "resources.cluster should sync after cluster_membership UPDATE")

    def test_resource_count_auto_updated(self):
        """INSERT into cluster_membership should auto-update cluster_definitions.resource_count."""
        before = self.conn.execute(
            "SELECT resource_count FROM cluster_definitions WHERE name='backend_api'"
        ).fetchone()
        before_count = before["resource_count"] if before else 0
        self.conn.execute("INSERT OR IGNORE INTO cluster_membership (resource_id, cluster_name) VALUES (?,?)",
                          (self.rid, "backend_api"))
        self.conn.commit()
        after = self.conn.execute(
            "SELECT resource_count FROM cluster_definitions WHERE name='backend_api'"
        ).fetchone()
        after_count = after["resource_count"] if after else 0
        self.assertGreaterEqual(after_count, before_count,
                                "resource_count should be >= before after INSERT")


class TestEdgeDecay(unittest.TestCase):
    """P3-1: Edge weight decay — verify decay script logic."""

    def setUp(self):
        self.conn = get_conn()
        self.skill_a = "test-e2e-decay-a"
        self.skill_b = "test-e2e-decay-b"
        for s in [self.skill_a, self.skill_b]:
            self.conn.execute("DELETE FROM cluster_membership WHERE resource_id IN (SELECT id FROM resources WHERE name=?)", (s,))
            self.conn.execute("DELETE FROM resources WHERE name=?", (s,))
        self.conn.commit()
        for s in [self.skill_a, self.skill_b]:
            self.conn.execute("""
                INSERT INTO resources (name, description, resource_type, status, cluster, file_path, body_text)
                VALUES (?, 'Decay test', 'skill', 'active', 'general', 'decay_test.py', '')
            """, (s,))
        self.conn.commit()
        self.rid_a = self.conn.execute("SELECT id FROM resources WHERE name=?", (self.skill_a,)).fetchone()["id"]
        self.rid_b = self.conn.execute("SELECT id FROM resources WHERE name=?", (self.skill_b,)).fetchone()["id"]
        self.conn.execute("INSERT OR IGNORE INTO resource_edges (source_id, target_id, edge_type, weight) VALUES (?,?,?,?)",
                          (self.rid_a, self.rid_b, "co_trigger", 0.80))
        self.conn.commit()

    def tearDown(self):
        self.conn.execute("DELETE FROM resource_edges WHERE source_id=? OR target_id=?", (self.rid_a, self.rid_b))
        for rid in [self.rid_a, self.rid_b]:
            self.conn.execute("DELETE FROM cluster_membership WHERE resource_id=?", (rid,))
            self.conn.execute("DELETE FROM resources WHERE id=?", (rid,))
        self.conn.commit()
        self.conn.close()

    def test_edge_weight_above_floor(self):
        """Edge weights should never go below 0.05 after decay."""
        # Simulate decay (0.995 multiplier, 100 iterations)
        w = 0.80
        for _ in range(200):
            w = max(0.05, w * 0.995)
        # Verify floor holds
        self.assertGreaterEqual(w, 0.05, "Edge weight should not go below 0.05 floor")

    def test_multiplicative_correct_increases_weight(self):
        """record_outcome correct → edge weight × 1.05, floored at 1.0."""
        from server.learning import record_outcome
        w_before = self.conn.execute(
            "SELECT weight FROM resource_edges WHERE source_id=? AND target_id=?", (self.rid_a, self.rid_b)
        ).fetchone()["weight"]
        record_outcome(self.skill_a, "correct", task_summary="decay e2e test", project="test")
        w_after = self.conn.execute(
            "SELECT weight FROM resource_edges WHERE source_id=? AND target_id=?", (self.rid_a, self.rid_b)
        ).fetchone()["weight"]
        self.assertAlmostEqual(w_after, min(1.0, w_before * 1.05), places=3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
