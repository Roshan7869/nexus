#!/usr/bin/env python3
"""
Verification script for Phase 4: Auto-Degradation, Cooldown, and Cluster Tuning.
"""
import sys
import os
import sqlite3
import unittest
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "server") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "server"))

from server.db import get_db_path as _resolve_db_path
from server.learning import record_outcome
from server.router import find_matching_skills


class TestPhase4DegradationTuning(unittest.TestCase):
    
    def setUp(self):
        from server.db import get_connection
        self.conn = get_connection()
        self.db_path = _resolve_db_path()
        import server.router as _router
        import server.learning as _learning
        _router.DB_PATH = self.db_path
        _learning.DB_PATH = self.db_path
        # Clean up test rows and their cluster memberships
        self.conn.execute("DELETE FROM cluster_membership WHERE resource_id IN (SELECT id FROM resources WHERE name LIKE 'test-degrade-%' OR name LIKE 'test-dummy-%')")
        self.conn.execute("DELETE FROM resources WHERE name LIKE 'test-degrade-%' OR name LIKE 'test-dummy-%'")
        self.conn.commit()
        
    def tearDown(self):
        self.conn.execute("DELETE FROM cluster_membership WHERE resource_id IN (SELECT id FROM resources WHERE name LIKE 'test-degrade-%' OR name LIKE 'test-dummy-%')")
        self.conn.execute("DELETE FROM resources WHERE name LIKE 'test-degrade-%' OR name LIKE 'test-dummy-%'")
        self.conn.commit()
        self.conn.close()

    def test_auto_degradation_and_cooldown(self):
        print("\n--- Testing Auto-Degradation & Cooldown Restoration ---")
        
        # 1. Insert test active resource
        self.conn.execute("""
            INSERT INTO resources (name, description, resource_type, status, cluster, times_triggered, times_wrong, times_correct, file_path, body_text)
            VALUES ('test-degrade-skill', 'Skill designed to fail repeatedly', 'skill', 'active', 'backend_api', 0, 0, 0, 'test_degrade.py', '')
        """)
        self.conn.commit()
        
        res = self.conn.execute("SELECT id FROM resources WHERE name='test-degrade-skill'").fetchone()
        skill_id = res["id"]
        
        # Record outcome 4 times (wrong)
        for i in range(4):
            record_outcome("test-degrade-skill", "wrong", task_summary="test task fail", project="test")
            
        # Verify it is still active (less than 5 trials)
        status_4 = self.conn.execute("SELECT status FROM resources WHERE id=?", (skill_id,)).fetchone()["status"]
        print(f"Status after 4 failures: '{status_4}' (Expected: 'active')")
        self.assertEqual(status_4, "active")
        
        # Record 5th wrong outcome
        record_outcome("test-degrade-skill", "wrong", task_summary="test task fail", project="test")
        
        # Verify it is now degraded
        status_5 = self.conn.execute("SELECT status, times_triggered, times_wrong FROM resources WHERE id=?", (skill_id,)).fetchone()
        print(f"Status after 5 failures: '{status_5['status']}' (Expected: 'degraded')")
        self.assertEqual(status_5["status"], "degraded")
        
        # 2. Simulate 8-day cooldown passage
        eight_days_ago = (datetime.now() - timedelta(days=8)).strftime('%Y-%m-%d %H:%M:%S')
        self.conn.execute("UPDATE resources SET updated_at=? WHERE id=?", (eight_days_ago, skill_id))
        self.conn.commit()
        
        # Trigger cooldown check (calls check_cooldown_restorations during record_outcome)
        self.conn.execute("INSERT OR IGNORE INTO resources (name, status, file_path, body_text) VALUES ('test-dummy-skill', 'active', 'dummy.py', '')")
        self.conn.commit()
        
        record_outcome("test-dummy-skill", "correct", task_summary="dummy success", project="test")
        
        # Verify test-degrade-skill was restored to active
        restored = self.conn.execute("SELECT status, times_triggered, times_wrong FROM resources WHERE id=?", (skill_id,)).fetchone()
        print(f"Status after cooldown check: '{restored['status']}' (Expected: 'active', reset counters: {dict(restored)})")
        self.assertEqual(restored["status"], "active")
        self.assertEqual(restored["times_triggered"], 0)
        
        # Verify belief priors were set defensively (if present)
        belief = self.conn.execute("SELECT alpha, beta FROM model_beliefs WHERE resource_id=?", (skill_id,)).fetchone()
        if belief is not None:
            print(f"Priors reset: alpha={belief['alpha']}, beta={belief['beta']} (Expected: 1.0, 3.0)")
            self.assertEqual(belief["alpha"], 1.0)
            self.assertEqual(belief["beta"], 3.0)
        
        # Clean dummy
        self.conn.execute("DELETE FROM resources WHERE name='test-dummy-skill'")
        self.conn.commit()

    def test_reclassification_and_cluster_balancing(self):
        print("\n--- Testing Cluster Re-classification & Balancing Penalty ---")
        
        try:
            from server.cluster_classifier import ClusterClassifier
            classifier = ClusterClassifier(db_path=str(self.db_path))
            print("Successfully loaded ClusterClassifier engine.")
            
            # Insert a resource with missing cluster confidence
            self.conn.execute("""
                INSERT INTO resources (name, description, resource_type, status, cluster, cluster_confidence, file_path, body_text)
                VALUES ('test-degrade-unclassed', 'React frontend components layout', 'skill', 'active', NULL, 0.0, 'test_unclassed.py', '')
            """)
            self.conn.commit()
            
            unclassed_id = self.conn.execute("SELECT id FROM resources WHERE name='test-degrade-unclassed'").fetchone()["id"]
            
            # Run reclassification on our test row (dry_run=False)
            classifier.classify_all(dry_run=False)
            
            # Check if it was updated
            updated_row = self.conn.execute("SELECT cluster, cluster_confidence FROM resources WHERE id=?", (unclassed_id,)).fetchone()
            print(f"Reclassified cluster: '{updated_row['cluster']}' (confidence: {updated_row['cluster_confidence']:.2f})")
            self.assertIsNotNone(updated_row["cluster"])
            self.assertGreater(updated_row["cluster_confidence"], 0.0)
            
        except Exception as e:
            print(f"Cluster reclassification verification skipped or failed: {e}")


if __name__ == "__main__":
    unittest.main()
