#!/usr/bin/env python3
"""Phase 6: KG edge rebuild service tests."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

NEXUS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(NEXUS_ROOT))
sys.path.insert(0, str(NEXUS_ROOT / "server"))

from server.kg_service import (
    get_kg_status,
    restore_backup_edges,
    rebuild_kg,
    ZERO_EDGE_TARGET,
    NULL_CLUSTER_TARGET,
)


class TestKGStatus(unittest.TestCase):
    def test_status_keys(self):
        status = get_kg_status()
        for key in (
            "total_resources",
            "total_edges",
            "zero_edge_resources",
            "embeddings",
            "edge_types",
            "gates",
        ):
            self.assertIn(key, status)

    def test_gate_structure(self):
        gates = get_kg_status()["gates"]
        self.assertEqual(gates["zero_edge_target"], ZERO_EDGE_TARGET)
        self.assertEqual(gates["null_cluster_target"], NULL_CLUSTER_TARGET)
        self.assertIn("zero_edge_pass", gates)
        self.assertIn("null_cluster_pass", gates)


class TestRestoreBackup(unittest.TestCase):
    def test_restore_dry_run(self):
        result = restore_backup_edges(dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertGreaterEqual(result["restored"], 0)
        self.assertIn("before_edges", result)


class TestRebuildKG(unittest.TestCase):
    @patch("server.kg_service.run_densify")
    @patch("server.kg_service.restore_backup_edges")
    @patch("server.kg_service.get_kg_status")
    def test_rebuild_dry_run_wiring(self, mock_status, mock_restore, mock_densify):
        mock_status.return_value = {
            "total_edges": 5,
            "zero_edge_resources": 2838,
            "gates": {"zero_edge_pass": False, "null_cluster_pass": True},
        }
        mock_restore.return_value = {"restored": 2796, "dry_run": True}
        mock_densify.return_value = {"dry_run": True, "similar_to_added": 100}

        result = rebuild_kg(dry_run=True)
        self.assertEqual(result["phase"], 6)
        self.assertEqual(result["issue"], "I-04")
        mock_restore.assert_called_once()
        mock_densify.assert_called_once()


class TestMCPHandlers(unittest.TestCase):
    def test_kg_status_json_serializable(self):
        payload = get_kg_status()
        text = json.dumps(payload)
        parsed = json.loads(text)
        self.assertIn("total_edges", parsed)


if __name__ == "__main__":
    unittest.main()