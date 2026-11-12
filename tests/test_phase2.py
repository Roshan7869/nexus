#!/usr/bin/env python3
"""
Verification test suite for Phase 2: Intent & Error Classification.
"""
import sys
import os
import sqlite3
import unittest
from pathlib import Path

# Add project root to path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "server") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "server"))

from server.db import resolve_db_path as _resolve_db_path
from core.learner import NexusLearner
from server.learning import record_outcome
import server.learning as learning


class TestPhase2ClassificationAndReflection(unittest.TestCase):
    
    def setUp(self):
        self.db_path = _resolve_db_path()
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        
        # Clear task classifications cache to ensure tests run fresh
        self.conn.execute("DELETE FROM task_classifications")
        # Clean test rows
        self.conn.execute("DELETE FROM resources WHERE name IN ('test-failed-skill', 'test-alt-skill')")
        self.conn.execute("DELETE FROM tool_traces WHERE task_run_id='test_run_123'")
        self.conn.execute("DELETE FROM reflection_failures WHERE task='run a python data science script'")
        self.conn.commit()

    def tearDown(self):
        self.conn.execute("DELETE FROM resources WHERE name IN ('test-failed-skill', 'test-alt-skill')")
        self.conn.execute("DELETE FROM tool_traces WHERE task_run_id='test_run_123'")
        self.conn.execute("DELETE FROM reflection_failures WHERE task='run a python data science script'")
        self.conn.commit()
        self.conn.close()

    def test_task_classification(self):
        """Test Learner task classification queries and confidence."""
        print("\n--- Testing Task Classification ---")
        learner = NexusLearner(db_path=str(self.db_path))
        
        queries = [
            ("build a modern react dashboard button", ["frontend"]),
            ("write a script to deploy to kubernetes and docker", ["devops"]),
            ("debug a compiler error in python script", ["coding", "research"]),
            ("analyze performance bottlenecks and scaling of database", ["reasoning"]),
            ("autonomous agent loop to automatically write code and deploy", ["agentic", "coding"]),
        ]
        
        for query, expected_types in queries:
            t_type, confidence = learner.classify_task(query)
            print(f"Query: '{query}' -> '{t_type}' (conf: {confidence:.2f}, expected: {expected_types})")
            self.assertIn(t_type, expected_types, f"Mismatch: expected one of {expected_types}, got '{t_type}'")

    def test_error_classification_and_reflection_fixes(self):
        """Test error logging, tool trace classification, and fix success mapping."""
        print("\n--- Testing Error Classification & Reflection Fixes ---")
        old_reflection = learning.REFLECTION_ENABLED
        learning.REFLECTION_ENABLED = True
        failed_id = None
        try:
            # Insert test resources with file_path and body_text to satisfy schema constraints
            self.conn.execute("""
                INSERT INTO resources (name, resource_type, status, cluster, file_path, body_text)
                VALUES ('test-failed-skill', 'skill', 'active', 'backend_api', 'test_failed.py', '')
            """)
            self.conn.execute("""
                INSERT INTO resources (name, resource_type, status, cluster, file_path, body_text)
                VALUES ('test-alt-skill', 'skill', 'active', 'backend_api', 'test_alt.py', '')
            """)
            self.conn.commit()
            
            # Get ID of the failed skill
            res = self.conn.execute("SELECT id FROM resources WHERE name='test-failed-skill'").fetchone()
            failed_id = res["id"]
            
            # Insert a dummy tool trace with an import error
            self.conn.execute("""
                INSERT INTO tool_traces (task_run_id, phase_id, subtask_name, resource_name, resource_type, status, outcome, notes)
                VALUES ('test_run_123', 'phase1', 'Subtask', 'test-failed-skill', 'skill', 'failed', 'failed', 'ImportError: No module named numpy')
            """)
            self.conn.commit()
            
            # Report outcome as wrong for test-failed-skill
            task_sum = "run a python data science script"
            record_outcome("test-failed-skill", "wrong", task_summary=task_sum, project="test_proj")
            
            # Check reflection_failures
            fail_row = self.conn.execute("""
                SELECT * FROM reflection_failures 
                WHERE resource_name='test-failed-skill' 
                ORDER BY id DESC LIMIT 1
            """).fetchone()
            self.assertIsNotNone(fail_row, "Reflection failure record was not created")
            self.assertEqual(fail_row["root_cause"], "missing_dependency")
            
            # Check reflection_fixes
            fix_row = self.conn.execute("""
                SELECT * FROM reflection_fixes 
                WHERE resource_id=? AND failure_summary LIKE '%missing_dependency'
                ORDER BY id DESC LIMIT 1
            """, (failed_id,)).fetchone()
            self.assertIsNotNone(fix_row, "Reflection fix was not logged")
            self.assertEqual(fix_row["times_applied"], 1)
            self.assertEqual(fix_row["times_successful"], 0)
            
            # Now simulate successful alternative routing by reporting outcome as correct for test-alt-skill
            record_outcome("test-alt-skill", "correct", task_summary=task_sum, project="test_proj")
            
            # Check if reflection_failures updated
            fail_row_updated = self.conn.execute("SELECT * FROM reflection_failures WHERE id=?", (fail_row["id"],)).fetchone()
            self.assertEqual(fail_row_updated["fix_successful"], 1)
            
            # Check if reflection_fixes updated
            fix_row_updated = self.conn.execute("SELECT * FROM reflection_fixes WHERE id=?", (fix_row["id"],)).fetchone()
            self.assertEqual(fix_row_updated["times_successful"], 1)
        finally:
            learning.REFLECTION_ENABLED = old_reflection
            if failed_id is not None:
                self.conn.execute("DELETE FROM reflection_fixes WHERE resource_id=?", (failed_id,))
                self.conn.commit()


if __name__ == "__main__":
    unittest.main()
