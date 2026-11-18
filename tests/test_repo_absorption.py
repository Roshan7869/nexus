#!/usr/bin/env python3
"""Repo resource absorption: frontmatter, discovery, idempotency, conflicts.

Stdlib only (sqlite3 + tempfile), so it runs without the repo's heavy deps:
    python -m unittest tests.test_repo_absorption
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

NEXUS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(NEXUS_ROOT))

from server.ingest.repo_resources import (  # noqa: E402
    absorb,
    discover,
    parse_frontmatter,
)

SKILL_MD = """---
name: {name}
license: Apache-2.0
description: >-
  Folded description spanning
  two lines.
---

# {name}

Body content for {name}.
"""

AGENT_MD = """---
name: {name}
description: A single-line agent description.
---

Agent body.
"""

PLAIN_MD = "# No frontmatter\n\nJust a body paragraph.\n"

# Minimal subset of the real schema that absorption touches.
SCHEMA = """
CREATE TABLE resources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    resource_type TEXT NOT NULL,
    repo_name TEXT NOT NULL DEFAULT 'unknown',
    file_path TEXT NOT NULL,
    description TEXT DEFAULT '',
    body_text TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    tier INTEGER NOT NULL DEFAULT 2,
    token_count INTEGER DEFAULT 0,
    times_triggered INTEGER NOT NULL DEFAULT 0,
    times_correct INTEGER NOT NULL DEFAULT 0,
    times_wrong INTEGER NOT NULL DEFAULT 0,
    cluster TEXT,
    updated_at TEXT,
    absorbed_at_commit TEXT,
    absorbed_at_date TEXT,
    invalidated_at TEXT
);
CREATE TABLE resource_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_id INTEGER NOT NULL,
    body_hash TEXT NOT NULL,
    absorbed_at_commit TEXT,
    absorbed_at_date TEXT,
    valid_from TEXT NOT NULL,
    invalidated_at TEXT,
    UNIQUE(resource_id, valid_from)
);
CREATE TABLE invalidation_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_id INTEGER NOT NULL,
    reason TEXT NOT NULL,
    invalidated_at TEXT NOT NULL,
    reopened_at TEXT
);
CREATE TABLE hygiene_flags (
    resource_id INTEGER NOT NULL,
    flag TEXT NOT NULL,
    detail TEXT,
    found_at TEXT NOT NULL,
    resolved_at TEXT,
    PRIMARY KEY (resource_id, flag)
);
CREATE TABLE ingestion_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,
    source_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    total_chunks INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    last_ingested_at TEXT,
    created_at TEXT,
    updated_at TEXT,
    metadata TEXT
);
CREATE TABLE ingestion_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    resource_id INTEGER,
    chunk_index INTEGER NOT NULL DEFAULT 0,
    chunk_type TEXT NOT NULL DEFAULT 'section',
    heading TEXT,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    token_count INTEGER NOT NULL DEFAULT 0,
    start_line INTEGER,
    end_line INTEGER
);
CREATE VIRTUAL TABLE chunks_fts USING fts5(
    content, heading, resource_id UNINDEXED, chunk_type UNINDEXED, source_id UNINDEXED
);
CREATE TABLE cluster_membership (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_name TEXT NOT NULL,
    resource_id INTEGER NOT NULL,
    affinity REAL NOT NULL DEFAULT 0.0,
    UNIQUE(cluster_name, resource_id)
);
"""


def make_repo(root: Path, skills=("alpha", "beta"), agents=("gamma",)) -> Path:
    for name in skills:
        d = root / "skills" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(SKILL_MD.format(name=name), encoding="utf-8")
        (d / "TEST.md").write_text("# test\n", encoding="utf-8")
        (d / "references").mkdir(exist_ok=True)
        (d / "references" / "notes.md").write_text("# notes\n", encoding="utf-8")
    for name in agents:
        a = root / "agents"
        a.mkdir(parents=True, exist_ok=True)
        (a / f"{name}.md").write_text(AGENT_MD.format(name=name), encoding="utf-8")
    return root


class TestFrontmatter(unittest.TestCase):
    def test_simple_keys(self):
        meta, body = parse_frontmatter("---\nname: x\ndescription: hello\n---\nBody\n")
        self.assertEqual(meta["name"], "x")
        self.assertEqual(meta["description"], "hello")
        self.assertEqual(body, "Body")

    def test_folded_block_scalar(self):
        meta, _ = parse_frontmatter(
            "---\nname: x\ndescription: >-\n  one\n  two\n---\nB\n")
        self.assertEqual(meta["description"], "one two")

    def test_literal_block_keeps_newlines(self):
        meta, _ = parse_frontmatter(
            "---\nname: x\ndescription: |\n  one\n  two\n---\nB\n")
        self.assertEqual(meta["description"], "one\ntwo")

    def test_missing_frontmatter_returns_body(self):
        meta, body = parse_frontmatter("# Title\n\ntext\n")
        self.assertEqual(meta, {})
        self.assertIn("# Title", body)

    def test_unterminated_frontmatter_is_not_parsed(self):
        meta, body = parse_frontmatter("---\nname: x\nno close")
        self.assertEqual(meta, {})
        self.assertIn("name: x", body)

    def test_quoted_value_unwrapped(self):
        meta, _ = parse_frontmatter('---\nname: "y"\n---\n')
        self.assertEqual(meta["name"], "y")


class TestDiscovery(unittest.TestCase):
    def test_discovers_skills_and_agents(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp))
            found = discover(repo)
            got = {(r.resource_type, r.name) for r in found}
            self.assertEqual(got, {
                ("skill", "alpha"), ("skill", "beta"), ("agent", "gamma"),
            })

    def test_skips_test_md_and_side_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp), skills=("alpha",), agents=())
            names = [r.name for r in discover(repo)]
            self.assertEqual(names, ["alpha"])
            self.assertNotIn("notes", names)

    def test_paths_are_repo_relative_posix(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp), skills=("alpha",), agents=("gamma",))
            for r in discover(repo):
                self.assertNotIn("\\", r.file_path)
                self.assertFalse(r.file_path.startswith("/"))
            by_name = {r.name: r.file_path for r in discover(repo)}
            self.assertEqual(by_name["alpha"], "skills/alpha/SKILL.md")
            self.assertEqual(by_name["gamma"], "agents/gamma.md")

    def test_name_falls_back_to_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            d = repo / "skills" / "no-frontmatter"
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(PLAIN_MD, encoding="utf-8")
            found = discover(repo)
            self.assertEqual([r.name for r in found], ["no-frontmatter"])
            self.assertTrue(found[0].description)  # derived from first paragraph

    def test_missing_layout_dirs_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(discover(Path(tmp)), [])


class TestAbsorb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.repo = make_repo(base / "myrepo")
        self.db = base / "test.db"
        con = sqlite3.connect(self.db)
        con.executescript(SCHEMA)
        con.commit()
        con.close()

    def tearDown(self):
        self.tmp.cleanup()

    def _count(self, table: str) -> int:
        con = sqlite3.connect(self.db)
        try:
            return con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        finally:
            con.close()

    def test_inserts_resources_and_chunks(self):
        out = absorb(self.repo, self.db)
        self.assertEqual(out["discovered"], 3)
        self.assertEqual(out["inserted"], 3)
        self.assertEqual(self._count("resources"), 3)
        self.assertEqual(self._count("ingestion_chunks"), 3)
        self.assertEqual(self._count("chunks_fts"), 3)

    def test_records_are_populated(self):
        absorb(self.repo, self.db)
        con = sqlite3.connect(self.db)
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT * FROM resources WHERE name='alpha'").fetchone()
        con.close()
        self.assertEqual(row["resource_type"], "skill")
        self.assertEqual(row["repo_name"], "myrepo")
        self.assertEqual(row["status"], "active")
        self.assertIn("Folded description", row["description"])
        self.assertGreater(row["token_count"], 0)
        self.assertGreater(len(row["body_text"]), 0)

    def test_second_run_is_idempotent(self):
        absorb(self.repo, self.db)
        out = absorb(self.repo, self.db)
        self.assertEqual(out["inserted"], 0)
        self.assertEqual(out["updated"], 0)
        self.assertEqual(out["unchanged"], 3)
        self.assertEqual(self._count("resources"), 3)
        self.assertEqual(self._count("ingestion_chunks"), 3)

    def test_edit_is_detected_as_update(self):
        absorb(self.repo, self.db)
        p = self.repo / "agents" / "gamma.md"
        p.write_text(AGENT_MD.format(name="gamma") + "\nExtra line.\n", encoding="utf-8")
        out = absorb(self.repo, self.db)
        self.assertEqual(out["updated"], 1)
        self.assertEqual(out["unchanged"], 2)

    def test_conflict_with_other_repo_is_not_stolen(self):
        absorb(self.repo, self.db)
        other = make_repo(Path(self.tmp.name) / "other", skills=("alpha",), agents=())
        out = absorb(other, self.db)
        self.assertEqual(len(out["conflicts"]), 1)
        self.assertEqual(out["conflicts"][0]["owner"], "myrepo")
        con = sqlite3.connect(self.db)
        owner = con.execute(
            "SELECT repo_name FROM resources WHERE name='alpha'").fetchone()[0]
        con.close()
        self.assertEqual(owner, "myrepo")  # untouched

    def test_dry_run_writes_nothing(self):
        out = absorb(self.repo, self.db, dry_run=True)
        self.assertTrue(out["dry_run"])
        self.assertEqual(len(out["names"]), 3)
        self.assertEqual(self._count("resources"), 0)
        self.assertEqual(self._count("ingestion_chunks"), 0)

    def test_fts_search_finds_absorbed_resource(self):
        absorb(self.repo, self.db)
        con = sqlite3.connect(self.db)
        hits = con.execute(
            "SELECT heading FROM chunks_fts WHERE chunks_fts MATCH 'body' "
            "ORDER BY bm25(chunks_fts)").fetchall()
        con.close()
        self.assertTrue(hits)

    def test_cluster_membership_written_when_requested(self):
        absorb(self.repo, self.db, cluster="architect")
        self.assertEqual(self._count("cluster_membership"), 3)

    def test_missing_repo_raises(self):
        with self.assertRaises(FileNotFoundError):
            absorb(Path(self.tmp.name) / "nope", self.db)


if __name__ == "__main__":
    unittest.main(verbosity=2)
