#!/usr/bin/env python3
import sys
import os
import sqlite3
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "server"))

from server.router import find_matching_skills
from server.learning import record_outcome

DB_PATH = REPO_ROOT / "db" / "nexus.db"

class TestEdgesAndReflection(unittest.TestCase):
    
    def setUp(self):
        self.conn = sqlite3.connect(str(DB_PATH))
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
        print("\n--- Testing Fractional Reward Updates ---")
        record_outcome(
            skill_name="test-edge-source",
            outcome="partial",
            task_summary="partial execution task",
            project="test_proj"
        )
        # Check database directly
        res = self.conn.execute("SELECT times_correct, times_wrong, times_triggered FROM resources WHERE name='test-edge-source'").fetchone()
        # Initial: correct=5, wrong=0, triggered=5
        # After partial: correct=5.5, wrong=0.5, triggered=6
        self.assertEqual(res["times_correct"], 5.5)
        self.assertEqual(res["times_wrong"], 0.5)
        self.assertEqual(res["times_triggered"], 6)

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
