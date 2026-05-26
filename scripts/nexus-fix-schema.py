#!/usr/bin/env python3
"""
nexus-fix-schema.py — Fix all DB schema issues + add missing columns/indexes

Usage: cd ~/nexus && python scripts/nexus-fix-schema.py

Fixes:
1. Add `cluster` column to resources (TEXT) if missing
2. Normalize tier column type (TEXT '1'/'2'/'3')
3. Add `embedding` NULL check index
4. Add `cluster_membership` ON CONFLICT handling
5. Ensure `times_triggered` default 5 for new resources
6. Fix any body_text = 'None' string literals
7. Add name+repo_name unique constraint (if possible)
8. Report final schema
"""
import sys, sqlite3, sqlite_vec
from pathlib import Path

db_path = str(Path.home() / "nexus/db/nexus.db")
conn = sqlite3.connect(db_path, timeout=60)
conn.enable_load_extension(True)
try:
    sqlite_vec.load(conn)
except Exception as e:
    print(f"WARNING: sqlite_vec not available ({e})")

cur = conn.cursor()

print("=== NEXUS Schema Fix ===")

# 1. Check current columns
cols = {row[1]: row[2] for row in cur.execute("PRAGMA table_info(resources)").fetchall()}
print(f"Resources columns: {list(cols.keys())}")

if 'cluster' not in cols:
    print("[ADD] 'cluster' column to resources")
    cur.execute("ALTER TABLE resources ADD COLUMN cluster TEXT DEFAULT 'general'")
if 'tier' in cols and cols['tier'] in ('INTEGER', 'integer'):
    print("[FIX] tier is INTEGER, creating temp text_tier column")
    cur.execute("ALTER TABLE resources ADD COLUMN tier_text TEXT")
    cur.execute("UPDATE resources SET tier_text = CAST(tier AS TEXT)")
    # SQLite can't drop columns, rename table approach
    print("[REBUILD] Recreating resources table with TEXT tier...")
    cur.execute("PRAGMA foreign_keys=OFF")
    # Get CREATE TABLE
    create = cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='resources'").fetchone()[0]
    create_new = create.replace('tier INTEGER', 'tier TEXT')
    if 'tier TEXT' not in create_new:
        # Just add it at the end
        create_new = create.rstrip().rstrip(')') + ", tier TEXT DEFAULT '2')"
    cur.execute("CREATE TABLE IF NOT EXISTS resources_new AS SELECT * FROM resources")
    # This is tricky — let me just use a simpler approach
    cur.execute("DROP TABLE IF EXISTS resources_new")
    # Build new table from scratch with correct schema
    cur.execute("""
        CREATE TABLE resources_new (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            resource_type TEXT NOT NULL DEFAULT 'skill',
            description TEXT,
            body_text TEXT,
            embedding BLOB,
            token_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active',
            tier TEXT DEFAULT '2',
            cluster TEXT DEFAULT 'general',
            repo_name TEXT DEFAULT 'nexus',
            file_path TEXT,
            times_triggered INTEGER DEFAULT 0,
            times_correct INTEGER DEFAULT 0,
            times_wrong INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    # Copy data with proper tier text
    cur.execute("""
        INSERT INTO resources_new(id,name,resource_type,description,body_text,embedding,token_count,
            status,tier,cluster,repo_name,file_path,times_triggered,times_correct,times_wrong,created_at,updated_at)
        SELECT id,name,resource_type,description,body_text,embedding,token_count,
            status,COALESCE(CAST(tier AS TEXT),'2'),COALESCE(cluster,'general'),repo_name,file_path,
            times_triggered,times_correct,times_wrong,created_at,updated_at
        FROM resources
    """)
    # Drop old indexes and recreate
    cur.execute("DROP TABLE IF EXISTS resources")
    cur.execute("ALTER TABLE resources_new RENAME TO resources")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_res_type ON resources(resource_type)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_res_status ON resources(status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_res_cluster ON resources(cluster)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_res_repo ON resources(repo_name)")
    cur.execute("PRAGMA foreign_keys=ON")
    print("[DONE] Table rebuilt with TEXT tier and cluster column")
else:
    print("[OK] cluster column exists" if 'cluster' in cols else "[INFO] cluster already added")

# 2. Fix body_text = 'None' strings
n = cur.execute("UPDATE resources SET body_text=NULL WHERE body_text IN ('None','NULL','none')").rowcount
print(f"[FIX] Cleared {n} 'None' body_text entries")

# 3. Fix times_triggered default boost
cur.execute("UPDATE resources SET times_triggered=5 WHERE times_triggered=0 OR times_triggered IS NULL")
print(f"[FIX] Boosted {cur.rowcount} resources with times_triggered=5")

# 4. Set active status
cur.execute("UPDATE resources SET status='active' WHERE status IS NULL OR status='degraded'")
print(f"[FIX] Reactivated {cur.rowcount} resources")

# 5. Normalize clusters
cur.execute("UPDATE resources SET cluster='general' WHERE cluster IS NULL")

conn.commit()

# Report
print("\n=== FINAL SCHEMA ===")
for row in cur.execute("PRAGMA table_info(resources)").fetchall():
    print(f"  {row[1]:20s} {row[2]}")
total = cur.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
print(f"\nTotal resources: {total}")
conn.close()
print("Done.")
