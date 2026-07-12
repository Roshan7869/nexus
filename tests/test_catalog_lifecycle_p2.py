#!/usr/bin/env python3
"""P2 catalog lifecycle unit tests (temp SQLite)."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

NEXUS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(NEXUS_ROOT))
sys.path.insert(0, str(NEXUS_ROOT / "server"))

from server.catalog_lifecycle import (  # noqa: E402
    CatalogLifecycle,
    ensure_schema,
    is_promote_eligible,
    catalog_metrics,
)


def _init_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE resources (
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE,
            resource_type TEXT,
            repo_name TEXT DEFAULT 'unknown',
            file_path TEXT DEFAULT '',
            description TEXT DEFAULT '',
            body_text TEXT DEFAULT '',
            status TEXT DEFAULT 'active',
            tier INTEGER DEFAULT 2,
            embedding BLOB,
            token_count INTEGER DEFAULT 0,
            times_triggered INTEGER DEFAULT 0,
            times_correct INTEGER DEFAULT 0,
            times_wrong INTEGER DEFAULT 0,
            last_used_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            cluster TEXT
        );
        CREATE TABLE cluster_membership (
            id INTEGER PRIMARY KEY,
            cluster_name TEXT NOT NULL,
            resource_id INTEGER NOT NULL,
            affinity REAL DEFAULT 0.0,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(cluster_name, resource_id)
        );
        CREATE TABLE cluster_definitions (
            name TEXT PRIMARY KEY,
            resource_count INTEGER DEFAULT 0,
            updated_at TEXT
        );
        CREATE TABLE resource_embeddings (
            resource_id INTEGER PRIMARY KEY,
            embedding BLOB
        );
        INSERT INTO cluster_definitions(name, resource_count) VALUES
          ('general', 0), ('architect', 0);
        INSERT INTO resources(id,name,resource_type,body_text,token_count,status,cluster,times_triggered,created_at)
        VALUES
          (1,'stub-mcp','mcp','',0,'active','general',0, datetime('now')),
          (2,'good-skill','skill', 'body text for good skill with enough length for promote rules here', 200,'active','architect',5, datetime('now')),
          (3,'cold-old','skill','short body only',10,'active','general',0, datetime('now','-120 days')),
          (4,'dual','skill', 'dual membership skill body with enough characters present', 100,'active','general',1, datetime('now'));
        INSERT INTO cluster_membership(cluster_name, resource_id, affinity) VALUES
          ('general', 1, 0.5),
          ('architect', 2, 0.9),
          ('general', 3, 0.5),
          ('general', 4, 0.4),
          ('architect', 4, 0.8);
        """
    )
    conn.commit()
    conn.close()


class TestPromoteRules(unittest.TestCase):
    def test_promote_blocks_empty(self):
        ok, reasons = is_promote_eligible(
            {
                "status": "active",
                "body_text": "",
                "token_count": 0,
                "cluster": None,
            }
        )
        self.assertFalse(ok)
        self.assertTrue(any("token" in r or "body" in r for r in reasons))


class TestLifecycleApply(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.path = Path(self.tmp.name)
        self.tmp.close()
        _init_db(self.path)

    def tearDown(self):
        try:
            self.path.unlink()
        except OSError:
            pass

    def test_dry_run_no_write(self):
        lc = CatalogLifecycle(self.path)
        report = lc.run_all(dry_run=True, rebuild_faiss=False, cold_days=90)
        self.assertTrue(report.dry_run)
        self.assertGreaterEqual(report.archived_zero_token, 1)
        conn = sqlite3.connect(str(self.path))
        active = conn.execute(
            "SELECT COUNT(*) FROM resources WHERE status='active'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(active, 4)

    def test_apply_archives_and_prunes(self):
        lc = CatalogLifecycle(self.path)
        report = lc.run_all(dry_run=False, rebuild_faiss=False, cold_days=90)
        self.assertFalse(report.errors)
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        stub = conn.execute(
            "SELECT status, archive_reason FROM resources WHERE id=1"
        ).fetchone()
        self.assertEqual(stub["status"], "archived")
        self.assertEqual(stub["archive_reason"], "zero_token_stub")
        cold = conn.execute(
            "SELECT status, archive_reason FROM resources WHERE id=3"
        ).fetchone()
        self.assertEqual(cold["status"], "archived")
        self.assertTrue(str(cold["archive_reason"]).startswith("cold_"))
        multi = conn.execute(
            "SELECT COUNT(*) FROM cluster_membership WHERE resource_id=4"
        ).fetchone()[0]
        self.assertEqual(multi, 1)
        primary = conn.execute(
            "SELECT cluster FROM resources WHERE id=4"
        ).fetchone()[0]
        self.assertEqual(primary, "architect")  # higher affinity
        # divergence zero among remaining memberships
        div = conn.execute(
            """
            SELECT COUNT(*) FROM resources r
            JOIN cluster_membership cm ON cm.resource_id=r.id
            WHERE r.cluster IS NOT NULL AND r.cluster != cm.cluster_name
            """
        ).fetchone()[0]
        self.assertEqual(div, 0)
        # counts updated
        arch_count = conn.execute(
            "SELECT resource_count FROM cluster_definitions WHERE name='architect'"
        ).fetchone()[0]
        self.assertGreaterEqual(arch_count, 1)
        conn.close()
        self.assertEqual(report.metrics_after["cluster_divergence"], 0)
        self.assertEqual(report.metrics_after["multi_membership"], 0)

    def test_gap1_metrics_split(self):
        from server.catalog_lifecycle import catalog_metrics

        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        # ensure schema columns for metrics that join sections
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS resource_sections ("
            "id INTEGER PRIMARY KEY, resource_id INTEGER, heading TEXT, content TEXT);"
            "CREATE TABLE IF NOT EXISTS routing_log ("
            "id INTEGER PRIMARY KEY, skill_name TEXT);"
        )
        m = catalog_metrics(conn)
        self.assertIn("invoke_never_triggered_pct", m)
        self.assertIn("reference_never_triggered", m)
        self.assertIn("invoke_cold_gate_pass", m)
        conn.close()

    def test_gap1_campaign_archives_reference(self):
        conn = sqlite3.connect(str(self.path))
        conn.execute(
            "INSERT INTO resources(id,name,resource_type,body_text,token_count,"
            "status,cluster,times_triggered,created_at) VALUES "
            "(5,'old-doc','doc','doc body enough length here',80,'active',"
            "'knowledge_wiki',0, datetime('now','-100 days'))"
        )
        conn.execute(
            "INSERT INTO cluster_membership(cluster_name,resource_id,affinity) "
            "VALUES ('knowledge_wiki',5,0.5)"
        )
        conn.commit()
        conn.close()
        lc = CatalogLifecycle(self.path)
        report = lc.run_gap1_campaign(dry_run=False, rebuild_faiss=False)
        self.assertGreaterEqual(report.archived_reference_cold, 1)
        conn = sqlite3.connect(str(self.path))
        st = conn.execute("SELECT status, archive_reason FROM resources WHERE id=5").fetchone()
        self.assertEqual(st[0], "archived")
        self.assertEqual(st[1], "cold_reference_unused")
        conn.close()


if __name__ == "__main__":
    unittest.main()
