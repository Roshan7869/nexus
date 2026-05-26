#!/usr/bin/env python3
import sqlite3, sys
conn = sqlite3.connect('/home/roshan/nexus/db/nexus.db')
conn.row_factory = sqlite3.Row

print('=== Resource Status ===')
for r in conn.execute('SELECT status, COUNT(*) as cnt FROM resources GROUP BY status'):
    print(f"  {r['status']}: {r['cnt']}")

orphans = conn.execute("SELECT COUNT(*) FROM cluster_membership WHERE resource_id NOT IN (SELECT id FROM resources WHERE status='active')").fetchone()[0]
print(f'\nOrphan cluster memberships: {orphans}')

null_routes = conn.execute("SELECT COUNT(*) FROM routing_log WHERE outcome IS NULL OR outcome='routed'").fetchone()[0]
print(f'Unclosed routing_log entries: {null_routes}')

total_edges = conn.execute("SELECT COUNT(*) FROM resource_edges").fetchone()[0]
updated_edges = conn.execute("SELECT COUNT(*) FROM resource_edges WHERE updated_at > created_at").fetchone()[0]
print(f'Edges updated by learning: {updated_edges}/{total_edges}')

cold = conn.execute("SELECT COUNT(*) FROM model_beliefs WHERE alpha=1.0 AND beta=1.0").fetchone()[0]
total_b = conn.execute("SELECT COUNT(*) FROM model_beliefs").fetchone()[0]
print(f'Cold beliefs: {cold}/{total_b} ({cold/max(total_b,1)*100:.1f}%)')

print('\n=== Active Triggers ===')
for t in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'"):
    print(f"  {t['name']}")

print('\n=== cluster_membership sample check ===')
dupe = conn.execute("SELECT COUNT(*) FROM cluster_membership WHERE resource_id NOT IN (SELECT id FROM resources)").fetchone()[0]
print(f"  Completely dangling (no resource at all): {dupe}")

conn.close()
print('Done.')
