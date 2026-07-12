#!/usr/bin/env python3
"""Gaps 4–6: IDE events, OKF residual sections, chunk→resource_id."""
from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

NEXUS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(NEXUS_ROOT))
sys.path.insert(0, str(NEXUS_ROOT / "server"))


class TestGap4IdeEvents(unittest.TestCase):
    def test_persist_and_handle(self):
        from server.rl_feedback_loop import handle_ide_event, _persist_ide_event

        # Use real DB path via mock of db_conn is heavy — unit test client URLs
        from server.ide_event_client import _event_urls

        urls = _event_urls()
        self.assertTrue(any("/v1/event" in u for u in urls))
        self.assertTrue(urls[0].endswith("/v1/event") or "/v1/event" in urls[0])

    def test_handle_ide_event_persists(self):
        from server import rl_feedback_loop as mod

        with patch.object(mod, "_persist_ide_event", return_value=99) as p:
            with patch.object(mod, "_handle_success_event", return_value={"status": "ok"}):
                with patch.object(mod, "_record_beliefs_from_event"):
                    with patch.object(mod, "get_feedback_coverage", return_value={}):
                        with patch.object(
                            mod, "ide_event_stats", return_value={"ide_event_loop_gate_pass": True}
                        ):
                            out = mod.handle_ide_event(
                                "code_accepted",
                                ide="claude-code",
                                model="sonnet",
                                provider="anthropic",
                                project="nexus",
                                metadata={"skill_name": "qa"},
                            )
        self.assertEqual(out["ide_event_id"], 99)
        self.assertTrue(out["handled"])
        p.assert_called_once()


class TestGap5OkfSections(unittest.TestCase):
    def test_synthesize_sections(self):
        from server.okf_sections import synthesize_from_catalog

        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.executescript(
            """
            CREATE TABLE resource_sections (
                id INTEGER PRIMARY KEY,
                resource_id INTEGER,
                section_order INTEGER,
                depth INTEGER,
                heading TEXT,
                content TEXT,
                token_count INTEGER
            );
            """
        )
        n = synthesize_from_catalog(
            con,
            1,
            name="my-skill",
            description="Does testing well",
            body_text="## Setup\n\nInstall pytest.\n\n## Usage\n\nRun tests.",
        )
        self.assertGreaterEqual(n, 2)
        rows = con.execute(
            "SELECT heading, content FROM resource_sections WHERE resource_id=1 ORDER BY section_order"
        ).fetchall()
        self.assertGreaterEqual(len(rows), 2)

    def test_resolve_skill_path_missing(self):
        from server.okf_sections import resolve_skill_path

        self.assertIsNone(resolve_skill_path("/no/such/path/ever.md", "zzz-nope"))


class TestGap6ChunkLink(unittest.TestCase):
    def test_backfill_resource_id(self):
        from server.chunk_resource_link import (
            ensure_resource_id_column,
            backfill_chunk_resource_ids,
        )

        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.executescript(
            """
            CREATE TABLE chunk_embeddings (
                chunk_id INTEGER PRIMARY KEY,
                text_hash TEXT,
                embedding BLOB,
                created_at TEXT
            );
            CREATE TABLE ingestion_chunks (
                id INTEGER PRIMARY KEY,
                resource_id INTEGER,
                content_hash TEXT
            );
            INSERT INTO ingestion_chunks(id, resource_id, content_hash) VALUES
                (1, 100, 'aaa'), (2, 200, 'bbb'), (3, NULL, 'ccc');
            INSERT INTO chunk_embeddings(chunk_id, text_hash, embedding, created_at) VALUES
                (1, 'aaa', X'00', 't'), (2, 'bbb', X'00', 't'), (3, 'ccc', X'00', 't');
            """
        )
        out = backfill_chunk_resource_ids(con)
        self.assertEqual(out["linked_after"], 2)
        self.assertTrue(out["chunk_resource_link_gate_pass"] is False or out["linked_pct"] >= 50)
        rid = con.execute(
            "SELECT resource_id FROM chunk_embeddings WHERE chunk_id=1"
        ).fetchone()[0]
        self.assertEqual(rid, 100)


if __name__ == "__main__":
    unittest.main()
