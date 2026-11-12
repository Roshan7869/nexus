#!/usr/bin/env python3
"""Apply migration 022_systemic_repair to /home/roshan/nexus/db/nexus.db safely."""
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path("/home/roshan/nexus/db/nexus.db")

def apply_migration():
    if not DB_PATH.exists():
        print(f"Error: {DB_PATH} does not exist", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Configure pragmas
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA synchronous=NORMAL")

    print(f"Applying migration 022 to {DB_PATH}...")

    # Step 1: Merge edges from resource_edges_backup into resource_edges
    edges_before = cursor.execute("SELECT count(*) FROM resource_edges").fetchone()[0]
    backup_count = cursor.execute("SELECT count(*) FROM resource_edges_backup").fetchone()[0]
    print(f"resource_edges before: {edges_before}, resource_edges_backup: {backup_count}")

    cursor.execute("""
        INSERT OR IGNORE INTO resource_edges
            (source_id, target_id, edge_type, weight, co_trigger_count, created_at, updated_at)
        SELECT source_id, target_id, edge_type, weight, co_trigger_count, created_at, updated_at
        FROM resource_edges_backup;
    """)
    edges_after = cursor.execute("SELECT count(*) FROM resource_edges").fetchone()[0]
    print(f"resource_edges after merge: {edges_after} (+{edges_after - edges_before} restored)")

    # Step 2: Add composite indexes to routing_log
    print("Creating indexes on routing_log...")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_routing_log_skill_outcome ON routing_log(skill_name, outcome);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_routing_log_intent ON routing_log(intent);")

    # Step 3: Safely add lifecycle columns to resources
    print("Checking resources lifecycle columns...")
    existing_cols = {row["name"] for row in cursor.execute("PRAGMA table_info(resources)").fetchall()}
    if "archive_reason" not in existing_cols:
        print("Adding archive_reason column to resources...")
        cursor.execute("ALTER TABLE resources ADD COLUMN archive_reason TEXT;")
    else:
        print("archive_reason column already exists in resources.")

    if "lifecycle_state" not in existing_cols:
        print("Adding lifecycle_state column to resources...")
        cursor.execute("ALTER TABLE resources ADD COLUMN lifecycle_state TEXT;")
    else:
        print("lifecycle_state column already exists in resources.")

    # Step 4: Drop empty temporary tables
    print("Dropping temporary tables if present...")
    cursor.execute("DROP TABLE IF EXISTS routing_log_new;")

    # Step 5: Record in schema_migrations
    print("Ensuring schema_migrations table...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version     TEXT PRIMARY KEY,
            applied_at  REAL NOT NULL DEFAULT (strftime('%s','now')),
            description TEXT
        );
    """)

    # Reconcile existing tables in schema_migrations
    migrations_to_record = [
        ("BASELINE", "Live DDL baseline of KG/edge tables"),
        ("010_fts5_outcome_log", "FTS5 chunks virtual table + outcome_log + schema_migrations registry"),
        ("020_local_harness", "Session ledger for IDE-local harness"),
        ("021_experiments", "Experiment tree ledger"),
        ("022_systemic_repair", "Restore edges from backup, routing_log composite indexes, resources lifecycle columns, drop routing_log_new"),
    ]

    for version, desc in migrations_to_record:
        cursor.execute("""
            INSERT OR IGNORE INTO schema_migrations (version, description)
            VALUES (?, ?);
        """, (version, desc))

    conn.commit()
    print("Migration 022 committed successfully!")

    # Verify integrity and state
    print("\nVerifying database state...")
    integrity = cursor.execute("PRAGMA integrity_check").fetchone()[0]
    print(f"PRAGMA integrity_check: {integrity}")

    migrations_rows = cursor.execute("SELECT version, description FROM schema_migrations ORDER BY version").fetchall()
    print("\nRecorded schema_migrations:")
    for row in migrations_rows:
        print(f"  - {row['version']}: {row['description']}")

    # Verify indexes
    indexes = cursor.execute("SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='routing_log'").fetchall()
    print("\nrouting_log indexes:")
    for idx in indexes:
        print(f"  - {idx['name']}: {idx['sql']}")

    # Verify routing_log_new is gone
    has_temp = cursor.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='routing_log_new'").fetchone()[0]
    print(f"\nrouting_log_new table exists: {bool(has_temp)}")

    # Verify resource_edges_backup is intact
    has_backup = cursor.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='resource_edges_backup'").fetchone()[0]
    backup_remaining = cursor.execute("SELECT count(*) FROM resource_edges_backup").fetchone()[0]
    print(f"resource_edges_backup table exists: {bool(has_backup)}, row count: {backup_remaining}")

    conn.close()

if __name__ == "__main__":
    apply_migration()
