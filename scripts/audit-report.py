#!/usr/bin/env python3
"""Final audit: health stats, degraded, unassigned, cluster gaps."""
import sqlite3, sqlite_vec

NEXUS_DB = '/home/roshan/nexus/db/nexus.db'
conn = sqlite3.connect(NEXUS_DB)
conn.enable_load_extension(True)
sqlite_vec.load(conn)
cur = conn.cursor()

# 1. Degraded by type
cur.execute("SELECT resource_type, COUNT(*) FROM resources WHERE status='degraded' GROUP BY resource_type ORDER BY COUNT(*) DESC")
print("=== DEGRADED BY TYPE ===")
for r in cur.fetchall():
    print(f"  {r[0]:20s} {r[1]}")

# 2. Top degraded resources
cur.execute("SELECT name, resource_type, times_correct, times_wrong FROM resources WHERE status='degraded' ORDER BY times_wrong DESC LIMIT 10")
print("\n=== MOST DEGRADED (top 10) ===")
for r in cur.fetchall():
    print(f"  {r[0]:30s} {r[1]:15s} correct={r[2]} wrong={r[3]}")

# 3. Unassigned count and sample
total_unassigned = cur.execute("SELECT COUNT(*) FROM resources WHERE cluster IS NULL OR cluster=''").fetchone()[0]
print(f"\n=== UNASSIGNED RESOURCES ===")
print(f"  Total: {total_unassigned}")
if total_unassigned > 0:
    cur.execute("SELECT name, resource_type FROM resources WHERE cluster IS NULL OR cluster='' LIMIT 20")
    for r in cur.fetchall():
        print(f"  U: {r[0]:30s} {r[1]:12s}")

# 4. Never triggered by type
cur.execute("SELECT resource_type, COUNT(*) FROM resources WHERE times_triggered=0 GROUP BY resource_type ORDER BY COUNT(*) DESC")
print("\n=== NEVER TRIGGERED BY TYPE ===")
for r in cur.fetchall():
    print(f"  {r[0]:20s} {r[1]}")

# 5. Newly registered (tier=2)
cur.execute("SELECT name, resource_type, repo_name FROM resources WHERE tier=2 AND times_triggered=0 AND resource_type IN ('skill','mcp','agent','cli_tool','command') ORDER BY resource_type, name")
print("\n=== NEWLY REGISTERED (from this audit) ===")
rows = cur.fetchall()
for r in rows:
    print(f"  + {r[0]:35s} {r[1]:12s} [{r[2]}]")
print(f"  Total: {len(rows)}")

# 6. Cluster distribution
cur.execute("SELECT COALESCE(cluster,'unassigned'), COUNT(*) FROM resources GROUP BY cluster ORDER BY COUNT(*) DESC")
print("\n=== CLUSTER DISTRIBUTION ===")
for r in cur.fetchall():
    cl = r[0] if r[0] else 'unassigned'
    print(f"  {cl:20s} {r[1]}")

# 7. Zero-token resources and empty bodies
zt = cur.execute("SELECT COUNT(*) FROM resources WHERE token_count=0").fetchone()[0]
eb = cur.execute("SELECT COUNT(*) FROM resources WHERE body_text IS NULL OR body_text=''").fetchone()[0]
print(f"\n=== DATA QUALITY ===")
print(f"  Zero-token resources: {zt}")
print(f"  Empty body: {eb}")

conn.close()
