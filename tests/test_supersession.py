#!/usr/bin/env python3
"""S1 supersession ledger: stamps, tombstones, pack filters, consistency check.

Stdlib only (sqlite3 + tempfile + git), so it runs without the repo's heavy deps:
    python -m unittest tests.test_supersession
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

NEXUS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(NEXUS_ROOT))

from server.ingest.repo_resources import absorb  # noqa: E402
from server.context_hygiene import (  # noqa: E402
    drop_superseded,
    flag_resource,
    invalidate_resource,
    reopen_orphaned_invalidations,
    resolve_flag,
)

SKILL_MD = """---
name: {name}
description: A test skill for {name}.
---

# {name}

Body for {name}. Use X, never Y.
"""


def _write_skill(repo: Path, name: str) -> None:
    p = repo / "skills" / name
    p.mkdir(parents=True, exist_ok=True)
    (p / "SKILL.md").write_text(SKILL_MD.format(name=name), encoding="utf-8")


def _git(repo: Path, *args: str) -> None:
    env = dict(os.environ,
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, env=env)


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
CREATE INDEX idx_resources_validity
    ON resources(repo_name, file_path, invalidated_at);
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
CREATE TABLE hygiene_flags (
    resource_id INTEGER NOT NULL,
    flag TEXT NOT NULL,
    detail TEXT,
    found_at TEXT NOT NULL,
    resolved_at TEXT,
    PRIMARY KEY (resource_id, flag)
);
CREATE TABLE invalidation_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_id INTEGER NOT NULL,
    reason TEXT NOT NULL,
    invalidated_at TEXT NOT NULL,
    reopened_at TEXT
);
CREATE TABLE note_ledger (
    note_id INTEGER PRIMARY KEY,
    resource_name TEXT,
    claim_key TEXT,
    body_hash TEXT,
    valid_from TEXT NOT NULL,
    invalidated_at TEXT
);
CREATE TABLE user_learnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    note TEXT NOT NULL,
    scope TEXT DEFAULT '',
    project TEXT DEFAULT 'default',
    task_type TEXT DEFAULT 'general',
    created_at TEXT
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
    resource_id INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL DEFAULT 0,
    chunk_type TEXT NOT NULL DEFAULT 'section',
    heading TEXT,
    content TEXT,
    content_hash TEXT,
    token_count INTEGER DEFAULT 0
);
CREATE TABLE cluster_membership (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_name TEXT NOT NULL,
    resource_id INTEGER NOT NULL,
    affinity REAL NOT NULL DEFAULT 0.0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(cluster_name, resource_id)
);
CREATE TABLE resource_sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_id INTEGER NOT NULL,
    heading TEXT,
    content TEXT
);
"""


class SupersessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.repo = t / "repo-alpha"
        self.repo.mkdir()
        self.db = t / "test.db"
        con = sqlite3.connect(str(self.db))
        con.executescript(SCHEMA)
        con.commit()
        con.close()
        # git repo so _git_head has something to stamp
        _git(self.repo, "init", "-q")
        _write_skill(self.repo, "alpha-one")
        _write_skill(self.repo, "alpha-two")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "initial")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _rows(self, q: str, *a) -> list[sqlite3.Row]:
        con = sqlite3.connect(str(self.db))
        con.row_factory = sqlite3.Row
        try:
            return con.execute(q, a).fetchall()
        finally:
            con.close()

    # ── absorb stamps + version ledger ────────────────────────────────────

    def test_absorb_stamps_head_and_opens_version(self) -> None:
        s = absorb(self.repo, self.db)
        self.assertEqual(s["inserted"], 2)
        rows = self._rows("SELECT name, absorbed_at_commit, absorbed_at_date, "
                          "invalidated_at FROM resources")
        self.assertTrue(all(r["absorbed_at_commit"] for r in rows))
        self.assertTrue(all(r["absorbed_at_date"] for r in rows))
        versions = self._rows("SELECT COUNT(*) c FROM resource_versions "
                              "WHERE invalidated_at IS NULL")
        self.assertEqual(versions[0]["c"], 2)

    def test_update_closes_prior_version_and_restamps(self) -> None:
        absorb(self.repo, self.db)
        # change one skill's body + commit upstream
        (self.repo / "skills" / "alpha-one" / "SKILL.md").write_text(
            SKILL_MD.format(name="alpha-one").replace("never Y", "always Y"),
            encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "update alpha-one")
        s = absorb(self.repo, self.db)
        self.assertEqual(s["updated"], 1)
        self.assertEqual(s["unchanged"], 1)
        rid = self._rows("SELECT id FROM resources WHERE name='alpha-one'")[0]["id"]
        open_v = self._rows(
            "SELECT COUNT(*) c FROM resource_versions "
            "WHERE resource_id=? AND invalidated_at IS NULL", rid)
        closed_v = self._rows(
            "SELECT COUNT(*) c FROM resource_versions "
            "WHERE resource_id=? AND invalidated_at IS NOT NULL", rid)
        self.assertEqual(open_v[0]["c"], 1)
        self.assertEqual(closed_v[0]["c"], 1)
        live = self._rows("SELECT invalidated_at FROM resources WHERE id=?", rid)
        self.assertIsNone(live[0]["invalidated_at"])

    # ── tombstones ────────────────────────────────────────────────────────

    def test_tombstone_on_upstream_delete(self) -> None:
        absorb(self.repo, self.db)
        (self.repo / "skills" / "alpha-two" / "SKILL.md").unlink()
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "delete alpha-two")
        s = absorb(self.repo, self.db)
        self.assertEqual(s["tombstoned"], 1)
        row = self._rows("SELECT status, invalidated_at FROM resources "
                         "WHERE name='alpha-two'")[0]
        self.assertEqual(row["status"], "archived")
        self.assertIsNotNone(row["invalidated_at"])
        audit = self._rows("SELECT reason FROM invalidation_audit "
                           "WHERE resource_id=(SELECT id FROM resources "
                           "WHERE name='alpha-two')")
        self.assertEqual(audit[0]["reason"], "upstream_delete")

    def test_tombstone_guard_skips_partial_discovery(self) -> None:
        absorb(self.repo, self.db)
        # remove the whole skills/ layout: discovery now sees 0 of 2 actives
        # -> guard must fire, nothing tombstoned
        import shutil
        shutil.rmtree(self.repo / "skills")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "wipe layout")
        s = absorb(self.repo, self.db)
        self.assertEqual(s["tombstoned"], 0)
        self.assertTrue(any("tombstone skipped" in e for e in s["errors"]))
        row = self._rows("SELECT status, invalidated_at FROM resources "
                         "WHERE name='alpha-two'")[0]
        self.assertEqual(row["status"], "active")
        self.assertIsNone(row["invalidated_at"])

    # ── pack filters ──────────────────────────────────────────────────────

    def test_drop_superseded_and_fail_open(self) -> None:
        rows = [{"id": 1, "invalidated_at": None},
                {"id": 2, "invalidated_at": "2026-11-19"},
                {"id": 3}]  # missing key -> passes (fail-open)
        kept = drop_superseded(rows)
        self.assertEqual([r["id"] for r in kept], [1, 3])

    def test_flags_block_and_resolve(self) -> None:
        absorb(self.repo, self.db)
        rid = self._rows("SELECT id FROM resources WHERE name='alpha-one'")[0]["id"]
        con = sqlite3.connect(str(self.db))
        con.row_factory = sqlite3.Row
        flag_resource(con, rid, "stale_marker", "TODO(b/123) unresolved")
        con.commit()
        flagged = con.execute(
            "SELECT COUNT(*) c FROM hygiene_flags WHERE resolved_at IS NULL"
        ).fetchone()["c"]
        self.assertEqual(flagged, 1)
        # same (rid, flag) again: idempotent, not duplicated
        flag_resource(con, rid, "stale_marker", "again")
        con.commit()
        flagged = con.execute(
            "SELECT COUNT(*) c FROM hygiene_flags WHERE resource_id=? AND flag=?",
            (rid, "stale_marker")).fetchone()["c"]
        self.assertEqual(flagged, 1)
        # a second, different flag coexists (composite PK)
        flag_resource(con, rid, "stub", "title-only")
        con.commit()
        resolve_flag(con, rid, "stale_marker")
        con.commit()
        still = con.execute(
            "SELECT COUNT(*) c FROM hygiene_flags "
            "WHERE resource_id=? AND resolved_at IS NULL", (rid,)
        ).fetchone()["c"]
        self.assertEqual(still, 1)  # 'stub' still unresolved
        con.close()

    # ── consistency check ─────────────────────────────────────────────────

    def test_orphaned_invalidation_reopened_tombstone_kept(self) -> None:
        absorb(self.repo, self.db)
        rid1 = self._rows("SELECT id FROM resources WHERE name='alpha-one'")[0]["id"]
        rid2 = self._rows("SELECT id FROM resources WHERE name='alpha-two'")[0]["id"]

        con = sqlite3.connect(str(self.db))
        con.row_factory = sqlite3.Row
        # 1. orphan: 'upstream_change' with NO replacement row -> must reopen
        invalidate_resource(con, rid1, "upstream_change")
        # 2. legit tombstone: 'upstream_delete' -> must stay closed
        invalidate_resource(con, rid2, "upstream_delete")
        con.commit()
        con.close()

        summary = reopen_orphaned_invalidations(self.db)
        self.assertEqual(summary["reopened"], 1)
        r1 = self._rows("SELECT invalidated_at FROM resources WHERE id=?", rid1)[0]
        r2 = self._rows("SELECT invalidated_at FROM resources WHERE id=?", rid2)[0]
        self.assertIsNone(r1["invalidated_at"])       # reopened
        self.assertIsNotNone(r2["invalidated_at"])     # tombstone stays
        audits = self._rows("SELECT reason FROM invalidation_audit WHERE resource_id=?", rid1)
        self.assertIn("suspect_reopen", [a["reason"] for a in audits])

    # ── note ledger pack filter (SQL-level, as wired in nexus_bridge) ─────

    def test_note_ledger_sql_excludes_superseded(self) -> None:
        con = sqlite3.connect(str(self.db))
        con.execute("INSERT INTO user_learnings(note) VALUES ('use alpha-one now')")
        con.execute("INSERT INTO user_learnings(note) VALUES ('use alpha-two, never X')")
        con.commit()
        n1, n2 = (r[0] for r in con.execute(
            "SELECT id FROM user_learnings ORDER BY id"))
        # note 2 superseded by a newer claim on the same key
        con.execute(
            "INSERT INTO note_ledger(note_id, resource_name, claim_key, valid_from, "
            "invalidated_at) VALUES(?, 'alpha-two', 'config-claim', '2026-01-01', "
            "'2026-06-01')", (n2,))
        con.commit()
        kept = con.execute(
            """SELECT u.id FROM user_learnings u
               WHERE NOT EXISTS (SELECT 1 FROM note_ledger nl
                                 WHERE nl.note_id = u.id
                                   AND nl.invalidated_at IS NOT NULL)"""
        ).fetchall()
        self.assertEqual([k[0] for k in kept], [n1])
        con.close()

    # ── S4 provenance stamps ──────────────────────────────────────────────

    def test_stamp_formats(self) -> None:
        from server.context_hygiene import provenance_stamp
        full = provenance_stamp("elephant-goldfish-model",
                                "70406c4d22cc7ea8299637c2c5b44625c48b3f08",
                                "2026-11-17")
        self.assertEqual(full, "elephant-goldfish-model @70406c4d · absorbed 2026-11-17")
        no_sha = provenance_stamp("llm-wiki", None, "2026-11-18")
        self.assertEqual(no_sha, "llm-wiki · absorbed 2026-11-18")
        # a stamp we cannot date is worse than none
        self.assertIsNone(provenance_stamp("repo", "deadbeef", None))
        self.assertIsNone(provenance_stamp(None, None, ""))
        self.assertIsNone(provenance_stamp(None, None, None))

    def test_attach_provenance_batches_and_fail_open(self) -> None:
        from server.context_hygiene import attach_provenance
        absorb(self.repo, self.db)
        con = sqlite3.connect(str(self.db))
        con.row_factory = sqlite3.Row
        entries = [
            {"name": "alpha-one"},
            {"name": "alpha-two"},
            {"name": "never-heard-of"},
            {"name": "alpha-one", "provenance": "keep-me"},  # never overwritten
        ]
        attach_provenance(con, entries)
        self.assertTrue(entries[0]["provenance"].startswith("repo-alpha @"))
        self.assertIn("absorbed", entries[0]["provenance"])
        self.assertIn("absorbed", entries[1]["provenance"])
        self.assertNotIn("provenance", entries[2])  # unknown name: no field
        self.assertEqual(entries[3]["provenance"], "keep-me")
        # fail-open on garbage
        attach_provenance(con, [{"name": None}, "junk", 42])
        con.close()

    # ── pack surfaces carry stamps (SQL-level, as wired) ──────────────────

    def test_search_resources_returns_stamp_fields(self) -> None:
        absorb(self.repo, self.db)
        con = sqlite3.connect(str(self.db))
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT r.repo_name, r.absorbed_at_commit, r.absorbed_at_date "
            "FROM resources r WHERE r.name='alpha-one'"
        ).fetchone()
        stamp = __import__("server.context_hygiene", fromlist=["x"]).provenance_stamp(
            row["repo_name"], row["absorbed_at_commit"], row["absorbed_at_date"])
        self.assertIn("repo-alpha @", stamp)
        self.assertIn("absorbed", stamp)
        con.close()


if __name__ == "__main__":
    unittest.main()
