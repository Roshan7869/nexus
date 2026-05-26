#!/usr/bin/env python3
"""
Run all 007_a through 007_e migrations against the live NEXUS database.
Idempotent — safe to run multiple times.
"""
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "db" / "nexus.db"
MIGRATIONS_DIR = REPO_ROOT / "db" / "migrations"

MIGRATION_FILES = [
    "007_a_resource_edges_indexes.sql",
    "007_b_ingestion_tables.sql",
    "007_c_dedup_candidates.sql",
    "007_d_cluster_count_trigger.sql",
    "007_e_sync_cluster_columns.sql",
]

def run_all():
    print(f"Connecting to: {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    for fname in MIGRATION_FILES:
        fpath = MIGRATIONS_DIR / fname
        if not fpath.exists():
            print(f"  [SKIP] {fname} — file not found")
            continue
        sql = fpath.read_text()
        print(f"  [RUN]  {fname} ...")
        try:
            conn.executescript(sql)
            conn.commit()
            print(f"  [OK]   {fname}")
        except Exception as e:
            print(f"  [ERR]  {fname}: {e}")

    # Verify triggers were created
    triggers = conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall()
    indexes = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='resource_edges'"
    ).fetchall()

    print(f"\nTriggers installed: {[t[0] for t in triggers]}")
    print(f"resource_edges indexes: {[i[0] for i in indexes]}")

    # Verify table counts
    for tbl in ["ingestion_sources", "ingestion_chunks", "dedup_candidates"]:
        try:
            cnt = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            print(f"Table {tbl}: {cnt} rows")
        except Exception as e:
            print(f"Table {tbl}: ERROR — {e}")

    conn.close()
    print("\nAll migrations complete.")

if __name__ == "__main__":
    run_all()
