#!/usr/bin/env python3
"""Phase 1: DB Integrity Audit for NEXUS - Fixed"""
import sqlite3

conn = sqlite3.connect('db/nexus.db')
cur = conn.cursor()

print('=== 1d. Embedding coverage ===')
cur.execute('SELECT COUNT(*) FROM resources WHERE embedding IS NOT NULL')
has_emb = cur.fetchone()[0]
cur.execute('SELECT COUNT(*) FROM resources')
total = cur.fetchone()[0]
cur.execute('SELECT COUNT(*) FROM resource_embeddings')
emb_table = cur.fetchone()[0]
print(f'  Resources with inline embedding: {has_emb}/{total}')
print(f'  Entries in resource_embeddings table: {emb_table}')

print()
print('=== 1e. Resources with weak/missing descriptions ===')
cur.execute("SELECT COUNT(*) FROM resources WHERE description IS NULL OR description = '' OR LENGTH(description) < 10")
weak_desc = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM resources WHERE description IS NOT NULL AND LENGTH(description) >= 10")
good_desc = cur.fetchone()[0]
print(f'  Good descriptions (>=10 chars): {good_desc}')
print(f'  Weak/missing descriptions: {weak_desc}/{total} ({weak_desc/total*100:.1f}%)')

print()
print('=== 1f. Skills with missing skill_ref ===')
cur.execute("SELECT COUNT(*) FROM resources WHERE (file_path IS NULL OR file_path = '') AND resource_type = 'skill'")
no_path = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM resources WHERE resource_type = 'skill'")
total_skills = cur.fetchone()[0]
print(f'  Skills without file_path: {no_path}/{total_skills}')

print()
print('=== 1g. Routing log stats ===')
cur.execute('SELECT COUNT(*) FROM routing_log')
total_routes = cur.fetchone()[0]
print(f'  Total routing decisions: {total_routes}')
if total_routes > 0:
    cur.execute('SELECT ide, COUNT(*) FROM routing_log GROUP BY ide ORDER BY COUNT(*) DESC LIMIT 5')
    for row in cur.fetchall():
        print(f'  IDE {row[0]}: {row[1]} routes')
    cur.execute('SELECT tool_name, COUNT(*) FROM routing_log GROUP BY tool_name ORDER BY COUNT(*) DESC LIMIT 10')
    print('  Top routed tools:')
    for row in cur.fetchall():
        print(f'    {row[0]}: {row[1]} routes')

print()
print('=== 1h. Reflection/fix stats ===')
cur.execute('SELECT COUNT(*) FROM reflection_log')
print(f'  Reflections: {cur.fetchone()[0]}')
cur.execute('SELECT COUNT(*) FROM reflection_failures')
print(f'  Failures: {cur.fetchone()[0]}')
cur.execute('SELECT COUNT(*) FROM reflection_fixes')
print(f'  Fixes: {cur.fetchone()[0]}')

print()
print('=== 1i. Orphan analysis ===')
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
    print('  Orphan resources by type:')
    for row in orphans:
        print(f'    {row[0]}: {row[1]}')
else:
    print('  ALL resources have cluster assignments.')

print()
print('=== 1j. Cluster vs claimed counts (from cluster_list API) ===')
cur.execute('''
    SELECT cm.cluster_name, COUNT(DISTINCT cm.resource_id) 
    FROM cluster_membership cm 
    GROUP BY cm.cluster_name 
    ORDER BY COUNT(*) DESC
''')
actual_counts = {}
for row in cur.fetchall():
    actual_counts[row[0]] = row[1]
    print(f'  {row[0]}: {row[1]} actual members')

claimed = {
    'analyzer_planner': 336, 'architect': 216, 'frontend_ui': 307,
    'backend_api': 120, 'devops_infra': 192, 'quality_security': 101,
    'knowledge_wiki': 241, 'general': 173
}
print()
print('  Discrepancies (claimed API vs actual DB):')
for cluster, claimed_count in claimed.items():
    actual = actual_counts.get(cluster, 0)
    diff = claimed_count - actual
    status = 'OK' if abs(diff) <= 2 else f'MISMATCH {diff:+d}'
    print(f'    {cluster}: claimed={claimed_count} actual={actual} {status}')

print()
print('=== 1k. Trigger/accuracy stats ===')
cur.execute('SELECT SUM(times_triggered), SUM(times_correct), SUM(times_wrong) FROM resources')
triggered, correct, wrong = cur.fetchone()
triggered = triggered or 0
correct = correct or 0
wrong = wrong or 0
print(f'  Total times triggered: {triggered}')
print(f'  Total times correct: {correct}')
print(f'  Total times wrong: {wrong}')
print(f'  Never-triggered resources: ', end='')
cur.execute('SELECT COUNT(*) FROM resources WHERE times_triggered = 0')
print(cur.fetchone()[0])
print(f'  Active resources (triggered >=1): ', end='')
cur.execute('SELECT COUNT(*) FROM resources WHERE times_triggered > 0')
print(cur.fetchone()[0])

print()
print('=== 1l. Tier-3 resources (low quality) ===')
cur.execute("SELECT name, resource_type FROM resources WHERE tier = 3")
t3 = cur.fetchall()
print(f'  Total tier-3 resources: {len(t3)}')
for row in t3[:10]:
    print(f'    {row[0]} ({row[1]})')
if len(t3) > 10:
    print(f'    ... and {len(t3)-10} more')

print()
print('=== 1m. Resources by status ===')
cur.execute('SELECT status, COUNT(*) FROM resources GROUP BY status')
for row in cur.fetchall():
    print(f'  {row[0]}: {row[1]}')

conn.close()
print('\nPhase 1 complete.')