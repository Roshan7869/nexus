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
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "server") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "server"))

from server.db import get_db_path as _resolve_db_path
import server.router as _router
from server.router import find_matching_skills


class TestEpsilonGreedyRouting(unittest.TestCase):
    
    def setUp(self):
        self.db_path = _resolve_db_path()
        _router.DB_PATH = self.db_path
        self.conn = sqlite3.connect(str(self.db_path))
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
        
        print(f"\n--- Exploration Active (random=0.05) ---")
        print(f"Matched Skills ({len(skills)}): {[s['name'] for s in skills]}")
        
        # Assertions
        self.assertGreater(len(skills), 0)
        # Check if at least one cold resource was surfaced via exploration
        cold_found = any(s['name'] in ['test-cold-skill-1', 'test-cold-skill-2'] for s in skills)
        self.assertTrue(cold_found, "Epsilon exploration failed to surface cold start resource!")

    @patch('random.random')
    def test_exploitation_active(self, mock_random):
        # Force random.random to return 0.50 (> 0.10) for pure exploitation
        mock_random.return_value = 0.50
        
        # Insert a warm resource that should beat the cold ones in exploitation
        self.conn.execute("""
            INSERT INTO resources (name, description, resource_type, status, cluster, times_triggered, times_correct)
            VALUES ('test-warm-skill', 'Warm reliable database search tool', 'skill', 'active', 'backend_api', 10, 10)
        """)
        self.conn.commit()
        
        res = self.conn.execute("SELECT id FROM resources WHERE cluster='backend_api' AND status='active'").fetchall()
        filtered_ids = [r['id'] for r in res]
        
        skills = find_matching_skills(
            task="search the user database records", 
            project="test_proj", 
            filtered_ids=filtered_ids,
            max_results=5
        )
        
        print(f"\n--- Exploitation Active (random=0.50) ---")
        print(f"Matched Skills ({len(skills)}): {[s['name'] for s in skills]}")
        
        self.assertGreater(len(skills), 0)
        # Clean warm skill
        self.conn.execute("DELETE FROM resources WHERE name='test-warm-skill'")
        self.conn.commit()


if __name__ == "__main__":
    unittest.main()
