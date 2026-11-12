#!/usr/bin/env python3
"""
Test suite for graph edge traversal and self-reflection adjustments.
"""
import sys
import os
import random
import sqlite3
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "server") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "server"))

from server.db import get_db_path as _resolve_db_path
import server.router as _router
import server.learning as _learning
from server.router import find_matching_skills
from server.learning import record_outcome


class TestEdgesAndReflection(unittest.TestCase):
    
    def setUp(self):
        # The router explores with random.random(); seed it so ranking assertions are repeatable.
        random.seed(0)
        self.db_path = _resolve_db_path()
        _router.DB_PATH = self.db_path
        _learning.DB_PATH = self.db_path

        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        
        # Clean up test rows (order matters: edges/memberships before resources)
        self.conn.execute("DELETE FROM resource_edges WHERE source_id IN (SELECT id FROM resources WHERE name LIKE 'test-edge-%')")
        self.conn.execute("DELETE FROM cluster_membership WHERE resource_id IN (SELECT id FROM resources WHERE name LIKE 'test-edge-%')")
        self.conn.execute("DELETE FROM resources WHERE name LIKE 'test-edge-%'")
        self.conn.execute("DELETE FROM reflection_failures WHERE resource_name LIKE 'test-edge-%'")
        self.conn.execute("DELETE FROM reflection_fixes WHERE resource_id IN (SELECT id FROM resources WHERE name LIKE 'test-edge-%')")
        self.conn.commit()
        
        # Insert test resources
        self.conn.execute("""
            INSERT INTO resources (name, description, resource_type, status, cluster, times_triggered, times_wrong, times_correct, file_path, body_text)
            VALUES ('test-edge-source', 'Source tool designed to trigger adjacent edges', 'skill', 'active', 'backend_api', 5, 0, 5, 'test_source.py', '')
        """)
        self.conn.execute("""
            INSERT INTO resources (name, description, resource_type, status, cluster, times_triggered, times_wrong, times_correct, file_path, body_text)
            VALUES ('test-edge-target', 'Target adjacent skill dependency', 'skill', 'active', 'backend_api', 2, 0, 2, 'test_target.py', '')
        """)
        self.conn.execute("""
            INSERT INTO resources (name, description, resource_type, status, cluster, times_triggered, times_wrong, times_correct, file_path, body_text)
            VALUES ('test-edge-failing', 'Failing tool designed to get penalized', 'skill', 'active', 'backend_api', 5, 0, 0, 'test_failing.py', '')
        """)
        self.conn.commit()
        
        # Get IDs
        self.source_id = self.conn.execute("SELECT id FROM resources WHERE name='test-edge-source'").fetchone()["id"]
        self.target_id = self.conn.execute("SELECT id FROM resources WHERE name='test-edge-target'").fetchone()["id"]
        self.failing_id = self.conn.execute("SELECT id FROM resources WHERE name='test-edge-failing'").fetchone()["id"]
        
        # Add graph edge connection
        self.conn.execute("""
            INSERT INTO resource_edges (source_id, target_id, edge_type, weight)
            VALUES (?, ?, 'dependency', 1.0)
        """, (self.source_id, self.target_id))
        self.conn.commit()
        
    def tearDown(self):
        self.conn.execute("DELETE FROM resource_edges WHERE source_id IN (SELECT id FROM resources WHERE name LIKE 'test-edge-%')")
        self.conn.execute("DELETE FROM cluster_membership WHERE resource_id IN (SELECT id FROM resources WHERE name LIKE 'test-edge-%')")
        self.conn.execute("DELETE FROM resources WHERE name LIKE 'test-edge-%'")
        self.conn.execute("DELETE FROM reflection_failures WHERE resource_name LIKE 'test-edge-%'")
        self.conn.execute("DELETE FROM reflection_fixes WHERE resource_id IN (SELECT id FROM resources WHERE name LIKE 'test-edge-%')")
        self.conn.commit()
        self.conn.close()
        
    def test_adjacent_edges_retrieved(self):
        print("\n--- Testing Graph Edges Retrieval ---")
        skills = find_matching_skills(
            task="adjacent edges source trigger query",
            project="test_proj",
            filtered_ids=[self.source_id, self.target_id]
        )
        self.assertGreater(len(skills), 0)
        
        # The source should match and contain target edge metadata
        source_match = next((s for s in skills if s["name"] == "test-edge-source"), None)
        self.assertIsNotNone(source_match)
        self.assertIn("edges", source_match)
        self.assertEqual(len(source_match["edges"]), 1)
        self.assertEqual(source_match["edges"][0]["target_name"], "test-edge-target")
        self.assertEqual(source_match["edges"][0]["edge_type"], "dependency")

    def test_reflection_penalization_and_boost(self):
        print("\n--- Testing Self-Reflection Router Adjustments ---")
        
        # Create reflection failure for failing tool
        self.conn.execute("""
            INSERT INTO reflection_failures (task, resource_name, resource_id, expected, actual, root_cause)
            VALUES ('test reflection task', 'test-edge-failing', ?, 'success', 'fail', 'missing_param')
        """, (self.failing_id,))
        # Suggest alternative fix
        self.conn.execute("""
            INSERT INTO reflection_fixes (resource_id, failure_summary, fix_description, times_applied, times_successful)
            VALUES (?, 'Failed with missing_param', 'Use test-edge-target instead', 1, 0)
        """, (self.failing_id,))
        self.conn.commit()
        
        # Run route lookup
        skills = find_matching_skills(
            task="test reflection task with failing tool",
            project="test_proj",
            filtered_ids=[self.failing_id, self.target_id]
        )
        
        failing_match = next((s for s in skills if s["name"] == "test-edge-failing"), None)
        target_match = next((s for s in skills if s["name"] == "test-edge-target"), None)
        
        # Check that the failing tool got penalized (or falls below the target match)
        if failing_match and target_match:
            self.assertGreater(target_match["score"], failing_match["score"], 
                              "Failing tool should score lower due to reflection failure penalty!")
            
    def test_fractional_updates_record_outcome(self):
        print("\n--- Partial outcome is telemetry-only (no counter change) ---")
        q = "SELECT times_correct, times_wrong, times_triggered FROM resources WHERE name='test-edge-source'"
        before = self.conn.execute(q).fetchone()
        record_outcome(
            skill_name="test-edge-source",
            outcome="partial",
            task_summary="partial execution task",
            project="test_proj"
        )
        after = self.conn.execute(q).fetchone()
        self.assertEqual(tuple(after), tuple(before))

    def test_multiplicative_edge_updates(self):
        print("\n--- Testing Multiplicative Edge Weight Updates ---")
        # Set weight to 0.5 first so we can verify the multiplier works and is not capped at 1.0
        self.conn.execute("UPDATE resource_edges SET weight = 0.5 WHERE source_id = ? AND target_id = ?", (self.source_id, self.target_id))
        self.conn.commit()
        
        record_outcome(
            skill_name="test-edge-source",
            outcome="correct",
            task_summary="successful execution task",
            project="test_proj"
        )
        
        res = self.conn.execute("SELECT weight FROM resource_edges WHERE source_id = ? AND target_id = ?", (self.source_id, self.target_id)).fetchone()
        # 0.5 * 1.05 = 0.525
        self.assertAlmostEqual(res["weight"], 0.525, places=3)
        
        # Record wrong outcome
        record_outcome(
            skill_name="test-edge-source",
            outcome="wrong",
            task_summary="failed execution task",
            project="test_proj"
        )
        
        res2 = self.conn.execute("SELECT weight FROM resource_edges WHERE source_id = ? AND target_id = ?", (self.source_id, self.target_id)).fetchone()
        # 0.525 * 0.85 = 0.44625
        self.assertAlmostEqual(res2["weight"], 0.44625, places=3)


if __name__ == "__main__":
    unittest.main()
