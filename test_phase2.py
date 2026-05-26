#!/usr/bin/env python3
"""
Verification script for Phase 2: Intent & Error Classification.
"""
import sys
import os
import sqlite3
from pathlib import Path

# Add project root to path
sys.path.insert(0, "/home/roshan/nexus")
sys.path.insert(0, "/home/roshan/nexus/server")

from core.learner import NexusLearner
from server.learning import record_outcome

DB_PATH = Path.home() / "nexus/db/nexus.db"

def run_tests():
    print("=== STARTING PHASE 2 VERIFICATION TESTS ===")
    
    # Clear task classifications cache to ensure semantic routing is tested fresh
    conn_init = sqlite3.connect(str(DB_PATH))
    conn_init.execute("DELETE FROM task_classifications")
    conn_init.commit()
    conn_init.close()
    
    # 1. Test Learner task classification
    print("\n--- Testing Task Classification ---")
    learner = NexusLearner(db_path=str(DB_PATH))
    
    queries = [
        ("build a modern react dashboard button", "frontend"),
        ("write a script to deploy to kubernetes and docker", "devops"),
        ("debug a compiler error in python script", "coding"),
        ("analyze performance bottlenecks and scaling of database", "reasoning"),
        ("autonomous agent loop to automatically write code and deploy", "agentic")
    ]
    
    for query, expected_type in queries:
        t_type, confidence = learner.classify_task(query)
        print(f"Query: '{query}'")
        print(f"  -> Classified as: '{t_type}' (confidence: {confidence:.2f})")
        print(f"  -> Expected: '{expected_type}'")
        assert t_type == expected_type, f"Mismatch: expected {expected_type}, got {t_type}"
        
    print("Task classification tests PASSED!")
    
    # 2. Test Error logging, Tool Trace classification, and Fix success mapping
    print("\n--- Testing Error Classification & Reflection Fixes ---")
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    
    # Clean up any potential stale test rows first
    conn.execute("DELETE FROM resources WHERE name IN ('test-failed-skill', 'test-alt-skill')")
    conn.execute("DELETE FROM tool_traces WHERE task_run_id='test_run_123'")
    conn.execute("DELETE FROM reflection_failures WHERE task='run a python data science script'")
    
    # Insert test resources
    conn.execute("INSERT INTO resources (name, resource_type, status, cluster) VALUES ('test-failed-skill', 'skill', 'active', 'backend_api')")
    conn.execute("INSERT INTO resources (name, resource_type, status, cluster) VALUES ('test-alt-skill', 'skill', 'active', 'backend_api')")
    
    # Get ID of the failed skill
    res = conn.execute("SELECT id FROM resources WHERE name='test-failed-skill'").fetchone()
    failed_id = res["id"]
    
    # Insert a dummy tool trace with an import error
    conn.execute("""
        INSERT INTO tool_traces (task_run_id, phase_id, subtask_name, resource_name, resource_type, status, outcome, notes)
        VALUES ('test_run_123', 'phase1', 'Subtask', 'test-failed-skill', 'skill', 'failed', 'failed', 'ImportError: No module named numpy')
    """)
    conn.commit()
    
    # Report outcome as wrong for test-failed-skill
    task_sum = "run a python data science script"
    print(f"Recording outcome 'wrong' for 'test-failed-skill' on task: '{task_sum}'...")
    result_wrong = record_outcome("test-failed-skill", "wrong", task_summary=task_sum, project="test_proj")
    
    # Check reflection_failures
    fail_row = conn.execute("""
        SELECT * FROM reflection_failures 
        WHERE resource_name='test-failed-skill' 
        ORDER BY id DESC LIMIT 1
    """).fetchone()
    
    print(f"Reflection Failure Root Cause: '{fail_row['root_cause']}'")
    assert fail_row["root_cause"] == "missing_dependency", f"Expected missing_dependency, got {fail_row['root_cause']}"
    
    # Check reflection_fixes
    fix_row = conn.execute("""
        SELECT * FROM reflection_fixes 
        WHERE resource_id=? AND failure_summary LIKE '%missing_dependency'
        ORDER BY id DESC LIMIT 1
    """, (failed_id,)).fetchone()
    
    print(f"Reflection Fix logged: {dict(fix_row) if fix_row else 'None'}")
    assert fix_row is not None, "Reflection fix was not logged!"
    assert fix_row["times_applied"] == 1
    assert fix_row["times_successful"] == 0
    
    # Now simulate successful alternative routing by reporting outcome as correct for test-alt-skill
    print(f"Recording outcome 'correct' for 'test-alt-skill' on task: '{task_sum}'...")
    result_correct = record_outcome("test-alt-skill", "correct", task_summary=task_sum, project="test_proj")
    
    # Check if reflection_failures updated
    fail_row_updated = conn.execute("SELECT * FROM reflection_failures WHERE id=?", (fail_row["id"],)).fetchone()
    print(f"Updated failure fix_successful: {fail_row_updated['fix_successful']}, fix_attempted: '{fail_row_updated['fix_attempted']}'")
    assert fail_row_updated["fix_successful"] == 1
    
    # Check if reflection_fixes updated
    fix_row_updated = conn.execute("SELECT * FROM reflection_fixes WHERE id=?", (fix_row["id"],)).fetchone()
    print(f"Updated fix times_successful: {fix_row_updated['times_successful']}")
    assert fix_row_updated["times_successful"] == 1
    
    # Clean up test rows
    print("\nCleaning up test data...")
    conn.execute("DELETE FROM resources WHERE name IN ('test-failed-skill', 'test-alt-skill')")
    conn.execute("DELETE FROM tool_traces WHERE task_run_id='test_run_123'")
    conn.execute("DELETE FROM reflection_failures WHERE task=?", (task_sum,))
    conn.execute("DELETE FROM reflection_fixes WHERE resource_id=?", (failed_id,))
    conn.commit()
    conn.close()
    
    print("\n=== ALL TESTS PASSED SUCCESSFULLY! ===")

if __name__ == "__main__":
    run_tests()
