#!/usr/bin/env python3
"""
Verification script for Phase 3: Epsilon-Greedy Routing.
"""
import sys
import os
import sqlite3
import unittest
from unittest.mock import patch
from pathlib import Path

# Add project root to path
REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "server"))

from server.router import find_matching_skills

DB_PATH = REPO_ROOT / "db" / "nexus.db"

class TestEpsilonGreedyRouting(unittest.TestCase):
    
    def setUp(self):
        self.conn = sqlite3.connect(str(DB_PATH))
        self.conn.row_factory = sqlite3.Row
        
        # Clean up existing test resources
        self.conn.execute("DELETE FROM resources WHERE name LIKE 'test-cold-%'")
        
        # Insert cold test resources
        self.conn.execute("""
            INSERT INTO resources (name, description, resource_type, status, cluster, times_triggered)
            VALUES ('test-cold-skill-1', 'Cold database search tool', 'skill', 'active', 'backend_api', 0)
        """)
        self.conn.execute("""
            INSERT INTO resources (name, description, resource_type, status, cluster, times_triggered)
            VALUES ('test-cold-skill-2', 'Cold api interface script', 'skill', 'active', 'backend_api', 0)
        """)
        self.conn.commit()
        
    def tearDown(self):
        self.conn.execute("DELETE FROM resources WHERE name LIKE 'test-cold-%'")
        self.conn.commit()
        self.conn.close()

    @patch('random.random')
    def test_exploration_active(self, mock_random):
        # Force random.random to return 0.05 (< 0.10) to trigger exploration
        mock_random.return_value = 0.05
        
        # We query for skills in backend_api
        res = self.conn.execute("SELECT id FROM resources WHERE cluster='backend_api' AND status='active'").fetchall()
        filtered_ids = [r['id'] for r in res]
        
        skills = find_matching_skills(
            task="search the user database records", 
            project="test_proj", 
            filtered_ids=filtered_ids,
            max_results=5
        )
        
        print("\n--- Exploration Mode Active (random < 0.10) ---")
        for idx, skill in enumerate(skills):
            print(f"Rank {idx+1}: {skill['name']} (score: {skill['score']}, category: {skill['category']})")
            
        # Verify that our cold skills got injected and are at the top
        cold_names = {"test-cold-skill-1", "test-cold-skill-2"}
        found_cold = [s for s in skills if s["name"] in cold_names]
        
        self.assertGreater(len(found_cold), 0, "No cold skills were injected during exploration!")
        self.assertTrue(skills[0]["name"] in cold_names, "Injected cold skill was not placed at the top!")
        self.assertEqual(skills[0]["category"], "REVIEW", "Cold skill category should be REVIEW")

    @patch('random.random')
    def test_exploration_inactive(self, mock_random):
        # Force random.random to return 0.50 (>= 0.10) to bypass exploration
        mock_random.return_value = 0.50
        
        # We query for skills in backend_api
        res = self.conn.execute("SELECT id FROM resources WHERE cluster='backend_api' AND status='active'").fetchall()
        filtered_ids = [r['id'] for r in res]
        
        skills = find_matching_skills(
            task="search the user database records", 
            project="test_proj", 
            filtered_ids=filtered_ids,
            max_results=5
        )
        
        print("\n--- Exploration Mode Inactive (random >= 0.10) ---")
        for idx, skill in enumerate(skills):
            print(f"Rank {idx+1}: {skill['name']} (score: {skill['score']}, category: {skill['category']})")
            
        # Verify that cold skills are NOT injected at the top
        cold_names = {"test-cold-skill-1", "test-cold-skill-2"}
        if skills:
            self.assertFalse(skills[0]["name"] in cold_names, "Cold skill was injected at the top even when exploration was inactive!")

if __name__ == "__main__":
    print("=== STARTING EPSILON-GREEDY ROUTING VERIFICATION TESTS ===")
    unittest.main()
