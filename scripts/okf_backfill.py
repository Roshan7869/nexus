#!/usr/bin/env python3
"""Backfill all active NEXUS resources with OKF-structured content.

For each active resource with a file_path pointing to an existing file
(or a SKILL.md inside a directory), runs ingest_okf() to parse sections,
then mirrors those sections into the resource_sections table.

Usage:
    python3 scripts/okf_backfill.py

Idempotent: ingest_okf wipes and re-writes source chunks each call,
and this script deletes + re-inserts resource_sections for each resource.
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "db" / "nexus.db"
sys.path.insert(0, str(DB.parent.parent))

from server.ingest.okf_ingest import ingest_okf  # noqa: E402


def _resolve_path(file_path: str) -> str | None:
    """Resolve a resource's file_path to an actual OKF file on disk."""
    p = Path(file_path)
    if p.is_file():
        return str(p.resolve())
    if p.is_dir():
        skill = p / "SKILL.md"
        if skill.is_file():
            return str(skill.resolve())
    return None


def main() -> None:
    con = sqlite3.connect(str(DB), timeout=60)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    con.row_factory = sqlite3.Row

    resources = con.execute(
        "SELECT r.id, r.name, r.file_path FROM resources r "
        "LEFT JOIN resource_sections rs ON rs.resource_id = r.id "
        "WHERE r.status='active' AND r.file_path IS NOT NULL AND r.file_path != '' "
        "AND rs.id IS NULL"  # skip already-sectioned
    ).fetchall()

    print(f"Found {len(resources)} active resources with file_path")

    succeeded = 0
    failed = 0
    total_sections = 0
    t0 = time.perf_counter()

    for r in resources:
        rid = r["id"]
        name = r["name"]
        fp = r["file_path"]

        resolved = _resolve_path(fp)
        if resolved is None:
            print(f"  SKIP {name}: file not found ({fp})")
            continue

        try:
            result = ingest_okf(rid, resolved, db_path=str(DB))
        except Exception as e:
            print(f"  FAIL {name}: {e}")
            failed += 1
            continue

        sid_row = con.execute(
            "SELECT id FROM ingestion_sources WHERE source_path=? AND source_type='okf'",
            (resolved,),
        ).fetchone()
        if sid_row is None:
            print(f"  WARN {name}: no ingestion_source found after ingest")
            succeeded += 1
            continue

        sid = sid_row["id"]
        chunks = con.execute(
            "SELECT chunk_index, heading, content, token_count FROM ingestion_chunks "
            "WHERE source_id=? ORDER BY chunk_index",
            (sid,),
        ).fetchall()

        con.execute("DELETE FROM resource_sections WHERE resource_id=?", (rid,))

        n_sec = len(chunks)
        for row in chunks:
            con.execute(
                "INSERT INTO resource_sections(resource_id, section_order, depth, heading, content, token_count) "
                "VALUES (?, ?, 0, ?, ?, ?)",
                (rid, row["chunk_index"], row["heading"], row["content"], row["token_count"]),
            )

        con.commit()
        total_sections += n_sec
        succeeded += 1
        print(f"  OKF backfill: {name} -> {n_sec} sections")

    elapsed = time.perf_counter() - t0
    print()
    print(f"Summary: {succeeded} succeeded, {failed} failed, "
          f"{total_sections} total sections ({elapsed:.2f}s)")
    con.close()


if __name__ == "__main__":
    main()
