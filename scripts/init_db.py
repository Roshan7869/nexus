#!/usr/bin/env python3
"""Apply schema.sql + all migrations to nexus.db. Idempotent — safe to re-run."""
import sqlite3, sys, os
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "db" / "nexus.db"
SCHEMA = Path(__file__).resolve().parent.parent / "db" / "schema.sql"
DB_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "db" / "migrations"
ROOT_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

DB_MIGRATION_ORDER = [
    "001_add_clusters.sql",
    "006_unified_learner.sql",
    "001_constraints.sql",
    "006_okf_resource_sections.sql",
    "007_graph_topology.sql",
    "007_a_resource_edges_indexes.sql",
    "007_b_ingestion_tables.sql",
    "007_c_dedup_candidates.sql",
    "007_d_cluster_count_trigger.sql",
    "007_e_sync_cluster_columns.sql",
]

ROOT_MIGRATION_ORDER = [
    "008_state_snapshot.sql",
    "010_fts5_outcome_log.sql",
    "011_chunk_source_file_path.sql",
    "012_kg_communities.sql",
    "013_ide_enum_documentation.sql",
    "014_optillm_bon_deprecation.sql",
]


def run_scripts(conn, label, file_paths):
    for p in file_paths:
        sql = p.read_text()
        try:
            conn.executescript(sql)
            print(f"  OK  {label}/{p.name}")
        except Exception as e:
            print(f"  ERR {label}/{p.name}: {e}", file=sys.stderr)


def main():
    print(f"Initializing DB: {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH), timeout=30)

    print("\n--- schema.sql ---")
    run_scripts(conn, "schema", [SCHEMA])

    print("\n--- db/migrations/ ---")
    db_migrations = []
    for name in DB_MIGRATION_ORDER:
        p = DB_MIGRATIONS_DIR / name
        if p.exists():
            db_migrations.append(p)
        else:
            print(f"  SKIP db/migrations/{name} (not found)")
    run_scripts(conn, "db/migrations", db_migrations)

    print("\n--- migrations/ (root) ---")
    root_migrations = []
    for name in ROOT_MIGRATION_ORDER:
        p = ROOT_MIGRATIONS_DIR / name
        if p.exists():
            root_migrations.append(p)
        else:
            print(f"  SKIP migrations/{name} (not found)")
    run_scripts(conn, "migrations", root_migrations)

    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")

    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    triggers = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' ORDER BY name"
    ).fetchall()
    indices = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()

    conn.close()

    print(f"\n{'='*50}")
    print(f"Tables:   {len(tables)}")
    for t in tables:
        print(f"  - {t[0]}")
    print(f"Triggers: {len(triggers)}")
    for t in triggers:
        print(f"  - {t[0]}")
    print(f"Indexes:  {len(indices)}")
    print(f"{'='*50}")
    print("Done.")


if __name__ == "__main__":
    main()
