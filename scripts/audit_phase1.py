#!/usr/bin/env python3
"""Phase 1: DB Integrity Audit for NEXUS"""
import sqlite3, json

conn = sqlite3.connect('db/nexus.db')
cur = conn.cursor()

print('=== 1a. Resource counts by type and tier ===')
cur.execute('SELECT resource_type, tier, COUNT(*) FROM resources GROUP BY resource_type, tier ORDER BY resource_type, tier')
for row in cur.fetchall():
    print(f'  {row[0]} tier-{row[1]}: {row[2]}')

print()
print('=== 1b. Cluster membership coverage ===')
cur.execute('SELECT COUNT(DISTINCT resource_id) FROM cluster_membership')
clustered = cur.fetchone()[0]
cur.execute('SELECT COUNT(*) FROM resources')
total = cur.fetchone()[0]
pct = clustered / total * 100 if total else 0
print(f'  Resources with cluster assignment: {clustered}/{total} ({pct:.1f}%)')
print(f'  Orphan resources (no cluster): {total - clustered}')

print()
print('=== 1c. Cluster distribution ===')
cur.execute('''
    SELECT cd.name, cd.display_name, COUNT(cm.resource_id) as cnt
    FROM cluster_definitions cd
    LEFT JOIN cluster_membership cm ON cd.name = cm.cluster_name
    GROUP BY cd.name
    ORDER BY cnt DESC
''')
for row in cur.fetchall():
    print(f'  {row[0]} ({row[1]}): {row[2]} members')

print()
print('=== 1d. Resources with missing embeddings ===')
cur.execute('SELECT COUNT(*) FROM resources r LEFT JOIN resource_embeddings e ON r.id = e.resource_id WHERE e.id IS NULL')
no_emb = cur.fetchone()[0]
cur.execute('SELECT COUNT(*) FROM resource_embeddings')
has_emb = cur.fetchone()[0]
print(f'  With embeddings: {has_emb}')
print(f'  Without embeddings: {no_emb}')

print()
print('=== 1e. Resources with empty/placeholder descriptions ===')
cur.execute("SELECT COUNT(*) FROM resources WHERE description IS NULL OR description = '' OR LENGTH(description) < 10")
weak_desc = cur.fetchone()[0]
print(f'  Weak/missing descriptions: {weak_desc}')

print()
print('=== 1f. Skills with missing skill_ref ===')
cur.execute("SELECT COUNT(*) FROM resources WHERE (skill_ref IS NULL OR skill_ref = '') AND resource_type = 'skill'")
no_ref = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM resources WHERE resource_type = 'skill'")
total_skills = cur.fetchone()[0]
print(f'  Skills without skill_ref: {no_ref}/{total_skills}')

print()
print('=== 1g. Routing log stats ===')
cur.execute('SELECT COUNT(*) FROM routing_log')
print(f'  Total routing decisions: {cur.fetchone()[0]}')
cur.execute('SELECT ide, COUNT(*) FROM routing_log GROUP BY ide ORDER BY COUNT(*) DESC LIMIT 5')
for row in cur.fetchall():
    print(f'  IDE {row[0]}: {row[1]} routes')

print()
print('=== 1h. Reflection/fix stats ===')
cur.execute('SELECT COUNT(*) FROM reflection_log')
print(f'  Reflections: {cur.fetchone()[0]}')
cur.execute('SELECT COUNT(*) FROM reflection_failures')
print(f'  Failures: {cur.fetchone()[0]}')
cur.execute('SELECT COUNT(*) FROM reflection_fixes')
print(f'  Fixes: {cur.fetchone()[0]}')

print()
print('=== 1i. Orphan analysis - resources in no cluster ===')
cur.execute('''
    SELECT r.resource_type, COUNT(*) 
    FROM resources r
    LEFT JOIN cluster_membership cm ON r.id = cm.resource_id
    WHERE cm.resource_id IS NULL
    GROUP BY r.resource_type
    ORDER BY COUNT(*) DESC
''')
orphans = cur.fetchall()
if orphans:
    for row in orphans:
        print(f'  Orphan {row[0]}: {row[1]}')
else:
    print('  No orphans! All resources have cluster assignments.')

print()
print('=== 1j. Sample orphan resources (first 10) ===')
cur.execute('''
    SELECT r.name, r.resource_type, r.tier
    FROM resources r
    LEFT JOIN cluster_membership cm ON r.id = cm.resource_id
    WHERE cm.resource_id IS NULL
    LIMIT 10
''')
orphan_samples = cur.fetchall()
if orphan_samples:
    for row in orphan_samples:
        print(f'  {row[0]} ({row[1]}, tier-{row[2]})')
else:
    print('  None found.')

print()
print('=== 1k. Tier-3 resources (low quality) sample ===')
cur.execute("SELECT name, resource_type FROM resources WHERE tier = 3 LIMIT 15")
for row in cur.fetchall():
    print(f'  {row[0]} ({row[1]})')

conn.close()
print('\nPhase 1 complete.')