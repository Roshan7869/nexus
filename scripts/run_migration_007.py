#!/usr/bin/env python3
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "db" / "nexus.db"
SQL_PATH = Path(__file__).parent.parent / "db" / "migrations" / "007_graph_topology.sql"

def main():
    print(f"Connecting to database: {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH))
    try:
        print(f"Reading migration from {SQL_PATH}")
        with open(SQL_PATH, "r") as f:
            sql = f.read()
        conn.executescript(sql)
        conn.commit()
        print("007_graph_topology migration applied successfully!")
    except Exception as e:
        print(f"Migration failed: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    main()
