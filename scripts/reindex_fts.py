#!/usr/bin/env python3
"""Maintain the `resources_fts` FTS5 index over `resources`.

Why this exists
---------------
`router.find_matching_skills` and `router_v2._resource_search` used to answer with
`LIKE '%token%'` over `body_text`. Measured on the live corpus that was 1083 ms and
40.9 MB pulled into Python per call; the FTS5 index answers the same query in 6-7 ms.

Deliberately **no triggers**: `route_task` updates `times_triggered` on a hot path, and an
AFTER UPDATE trigger would rebuild index rows on every routing decision — trading a read
cost for a worse write cost. Ingestion is batch, so reindexing after ingest is enough.

    python scripts/reindex_fts.py            # rebuild only if the content changed
    python scripts/reindex_fts.py --force    # unconditional rebuild
    python scripts/reindex_fts.py --check    # report staleness, change nothing
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.db import get_db_path  # noqa: E402

DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS resources_fts USING fts5(
    name, description, body_text,
    content='resources', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
)
"""


def _live_counts(con: sqlite3.Connection) -> tuple[int, int]:
    """(resources rows, docs the index can actually answer for).

    External-content FTS5 has no cheap document count: `count(*)` on the FTS table reads
    the *content* table, and `resources_fts_data` counts internal segments. So probe the
    index with a query that must match something — 0 means the index is empty or missing.
    """
    resources = con.execute("SELECT count(*) FROM resources").fetchone()[0]
    try:
        indexable = con.execute(
            "SELECT count(DISTINCT rowid) FROM resources_fts "
            "WHERE resources_fts MATCH 'the OR a OR to'").fetchone()[0]
    except sqlite3.Error:
        indexable = -1  # no index
    return resources, indexable


def _fingerprint(con: sqlite3.Connection) -> str:
    """Cheap content signature: row count + newest updated_at + total body bytes."""
    row = con.execute(
        "SELECT count(*), coalesce(max(updated_at),''), coalesce(sum(length(coalesce(body_text,''))),0) "
        "FROM resources").fetchone()
    return f"{row[0]}:{row[1]}:{row[2]}"


def _stored_fingerprint(con: sqlite3.Connection) -> str:
    try:
        con.execute("CREATE TABLE IF NOT EXISTS fts_meta (key TEXT PRIMARY KEY, val TEXT)")
        row = con.execute("SELECT val FROM fts_meta WHERE key='resources_fts'").fetchone()
        return row[0] if row else ""
    except sqlite3.Error:
        return ""


def _store_fingerprint(con: sqlite3.Connection, value: str) -> None:
    con.execute("CREATE TABLE IF NOT EXISTS fts_meta (key TEXT PRIMARY KEY, val TEXT)")
    con.execute("INSERT INTO fts_meta(key,val) VALUES('resources_fts',?) "
                "ON CONFLICT(key) DO UPDATE SET val=excluded.val", (value,))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    db = Path(args.db or get_db_path())
    con = sqlite3.connect(str(db), timeout=60)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=60000")
    try:
        resources, indexed = _live_counts(con)
        current = _fingerprint(con)
        stored = _stored_fingerprint(con)

        if args.check:
            print(f"db          : {db}")
            print(f"resources   : {resources}")
            print(f"index probe : {indexed if indexed >= 0 else 'NO INDEX'} docs answerable")
            print(f"fingerprint : {'match' if current == stored else 'STALE'}")
            return 0 if (current == stored and indexed > 0) else 1

        if not args.force and current == stored and indexed > 0:
            print(f"resources_fts up to date ({resources} resources) — nothing to do")
            return 0

        con.execute(DDL)
        t0 = time.perf_counter()
        con.execute("INSERT INTO resources_fts(resources_fts) VALUES('rebuild')")
        con.execute("INSERT INTO resources_fts(resources_fts) VALUES('optimize')")
        _store_fingerprint(con, current)
        con.commit()
        elapsed = time.perf_counter() - t0
        after = con.execute("SELECT count(*) FROM resources_fts_data").fetchone()[0]
        size_mb = db.stat().st_size / 1e6
        print(f"resources_fts rebuilt in {elapsed:.1f}s: {after} index rows "
              f"for {resources} resources ({size_mb:.0f} MB db)")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
