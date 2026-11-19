#!/usr/bin/env python3
"""S2 stale-marker linter: extraction, resolution tiers, pack-block semantics.

Stdlib only: python -m unittest tests.test_stale_lint
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

NEXUS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(NEXUS_ROOT))

from server.ingest.stale_lint import GhResolver, scan_resources  # noqa: E402
from server.context_hygiene import drop_pack_blocked  # noqa: E402

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
    invalidated_at TEXT
);
CREATE TABLE hygiene_flags (
    resource_id INTEGER NOT NULL,
    flag TEXT NOT NULL,
    detail TEXT,
    found_at TEXT NOT NULL,
    resolved_at TEXT,
    PRIMARY KEY (resource_id, flag)
);
CREATE TABLE marker_cache (
    ref TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    checked_at TEXT NOT NULL
);
"""


class FakeResolver(GhResolver):
    """Deterministic tracker: CLOSED_SET -> closed, else open."""

    CLOSED_SET = {"owner/repo#7", "microsoft/TypeScript#47663"}

    def __init__(self, budget: int = 200) -> None:
        super().__init__(budget=budget)
        self.call_log: list[str] = []

    def resolve_issue(self, owner: str, repo: str, number: str) -> str | None:
        if self.calls >= self.budget:
            return None
        self.calls += 1
        ref = f"{owner}/{repo}#{number}"
        self.call_log.append(ref)
        return "closed" if ref in self.CLOSED_SET else "open"


class StaleLintTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "t.db"
        con = sqlite3.connect(str(self.db))
        con.executescript(SCHEMA)
        con.commit()
        con.close()
        self.con = self._conn()

    def tearDown(self) -> None:
        self.con.close()
        self.tmp.cleanup()

    def _conn(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.db))
        con.row_factory = sqlite3.Row
        return con

    def _add(self, name: str, body: str) -> int:
        con = self.con
        cur = con.execute(
            "INSERT INTO resources(name, resource_type, repo_name, file_path, "
            "body_text, description) VALUES(?,?,?,?,?,'')",
            (name, "skill", "somerepo", f"skills/{name}/SKILL.md", body),
        )
        con.commit()
        rid = int(cur.lastrowid)
        return rid

    def _flags(self, rid: int) -> list[sqlite3.Row]:
        con = self.con
        rows = con.execute(
            "SELECT flag, resolved_at FROM hygiene_flags WHERE resource_id=?",
            (rid,)).fetchall()
        return rows

    def test_closed_issue_packs_out(self) -> None:
        rid = self._add("closed-claim",
                        "Fixes github.com/owner/repo/issues/7 — the work in "
                        "this note tracks that closed ticket. Plenty of surrounding "
                        "body content so the stub rule never fires here.")
        s = scan_resources(self.con, FakeResolver())
        self.assertEqual(s["resolved_closed"], 1)
        flags = self._flags(rid)
        stale = [f for f in flags if f["flag"] == "stale_marker"]
        self.assertEqual(len(stale), 1)
        self.assertIsNone(stale[0]["resolved_at"])

    def test_open_issue_not_flagged(self) -> None:
        rid = self._add("open-claim",
                        "Blocked by github.com/owner/repo/issues/8 while the migration is still "
                        "in progress; see the checklist below for the steps.")
        s = scan_resources(self.con, FakeResolver())
        self.assertEqual(s["resolved_open"], 1)
        for f in self._flags(rid):
            self.assertIsNotNone(f["resolved_at"])

    def test_budget_exhaustion_is_pending_not_stale(self) -> None:
        rid = self._add("unknown-claim",
                        "See github.com/owner/repo/issues/9 for the upstream discussion and the "
                        "follow-up plan tracked there.")
        # budget 0 = resolver never called (offline / spent)
        s = scan_resources(self.con, FakeResolver(budget=0))
        self.assertEqual(s["pending"], 1)
        self.assertEqual(s["resolved_closed"], 0)
        # pending is NOT pack-blocking
        con = self.con
        kept = drop_pack_blocked(self.con, [{"name": "unknown-claim"}])
        self.assertEqual(kept[0]["name"], "unknown-claim")

    def test_buganizer_ref_is_pending_by_design(self) -> None:
        rid = self._add("buganizer", "TODO(b/12345): migrate the schema. This body is long enough that the "
                        "stub rule does not interfere with the marker test.")
        s = scan_resources(self.con, FakeResolver())
        self.assertEqual(s["pending"], 1)
        con = self.con
        kept = drop_pack_blocked(self.con, [{"name": "buganizer"}])
        self.assertEqual(len(kept), 1)

    def test_aged_date_claim_blocks_pack(self) -> None:
        rid = self._add("dated", "As of 2023-05-01 the library has no async API.")
        s = scan_resources(self.con, FakeResolver())
        self.assertEqual(s["asof_old"], 1)
        con = self.con
        kept = drop_pack_blocked(self.con, [{"name": "dated"}])
        self.assertEqual(kept, [])

    def test_recent_date_claim_ignored(self) -> None:
        self._add("recent", "As of 2026-10-01 the library has an async API.")
        s = scan_resources(self.con, FakeResolver())
        self.assertEqual(s["asof_old"], 0)

    def test_stub_flag_blocks_pack(self) -> None:
        self._add("tiny", "ok")
        s = scan_resources(self.con, FakeResolver())
        self.assertEqual(s["stubs"], 1)
        con = self.con
        kept = drop_pack_blocked(self.con, [{"name": "tiny"}, {"name": "absent"}])
        self.assertEqual(kept, [{"name": "absent"}])

    def test_plain_todo_tracked_only(self) -> None:
        rid = self._add("worker", "Some real body content here.\n\nTODO: fix later")
        s = scan_resources(self.con, FakeResolver())
        self.assertEqual(s["plain_todos"], 1)
        con = self.con
        kept = drop_pack_blocked(self.con, [{"name": "worker"}])
        self.assertEqual(len(kept), 1)

    def test_cache_prevents_repeated_calls(self) -> None:
        self._add("cached", "Ref github.com/owner/repo/issues/7.")
        r1 = FakeResolver()
        scan_resources(self.con, r1)
        self.assertEqual(len(r1.call_log), 1)
        r2 = FakeResolver()
        s2 = scan_resources(self.con, r2)
        self.assertEqual(len(r2.call_log), 0)
        self.assertEqual(s2["cache_hits"], 1)
        # cached closed verdict persists without a second tracker call
        self.assertEqual(s2["resolved_closed"], 1)

    def test_search_sql_excludes_blocking_only(self) -> None:
        # blocking flag -> excluded by the pack SQL; pending -> included
        self._add("blocked", "As of 2023-01-01 X is impossible, and this body is "
                            "deliberately long to stay clear of the stub rule.")
        self._add("tracked", "TODO(b/1) somewhere plus real content that continues "
                            "for a while so the stub rule does not apply.")
        scan_resources(self.con, FakeResolver(budget=0))
        con = self.con
        rows = con.execute(
            """SELECT r.name FROM resources r
               WHERE r.status='active' AND r.invalidated_at IS NULL
                 AND NOT EXISTS (SELECT 1 FROM hygiene_flags hf
                                 WHERE hf.resource_id = r.id
                                   AND hf.resolved_at IS NULL
                                   AND hf.flag IN ('stale_marker','stub',
                                                   'unresolved_claim'))"""
        ).fetchall()
        names = [x["name"] for x in rows]
        self.assertNotIn("blocked", names)
        self.assertIn("tracked", names)


if __name__ == "__main__":
    unittest.main()
