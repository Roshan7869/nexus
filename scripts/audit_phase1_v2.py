#!/usr/bin/env python3
"""Phase 1 FIXED v2: DB Integrity Audit for NEXUS"""
import sqlite3

conn = sqlite3.connect('db/nexus.db')
cur = conn.cursor()

print('=== 1g. Routing log stats ===')
cur.execute('SELECT COUNT(*) FROM routing_log')
total_routes = cur.fetchone()[0]
print(f'  Total routing decisions: {total_routes}')
cur.execute('SELECT ide, COUNT(*) FROM routing_log GROUP BY ide ORDER BY COUNT(*) DESC LIMIT 5')
for row in cur.fetchall():
    print(f'  IDE {row[0]}: {row[1]} routes')

if total_routes > 0:
    cur.execute('SELECT skill_name, COUNT(*) FROM routing_log GROUP BY skill_name ORDER BY COUNT(*) DESC LIMIT 15')
    print('  Top routed skills:')
    for row in cur.fetchall():
        print(f'    {row[0]}: {row[1]} routes')
    
    cur.execute('SELECT outcome, COUNT(*) FROM routing_log GROUP BY outcome')
    print('  Outcomes:')
    for row in cur.fetchall():
        print(f'    {row[0]}: {row[1]}')

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
    cur.execute('''
        SELECT r.name, r.resource_type, r.tier
        FROM resources r
        LEFT JOIN cluster_membership cm ON r.id = cm.resource_id
        WHERE cm.resource_id IS NULL
    ''')
    print('  Orphan resources:')
    for row in cur.fetchall():
        print(f'    {row[0]} ({row[1]}, tier-{row[2]})')
else:
    print('  ALL resources have cluster assignments!')

print()
print('=== 1j. Cluster vs claimed counts ===')
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
total_discrepancy = 0
for cluster, claimed_count in claimed.items():
    actual = actual_counts.get(cluster, 0)
    diff = claimed_count - actual
    total_discrepancy += abs(diff)
    status = 'OK' if abs(diff) <= 2 else f'MISMATCH {diff:+d}'
    print(f'    {cluster}: claimed={claimed_count} actual={actual} {status}')
print(f'  Total discrepancy: {total_discrepancy}')

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
print(f'  Global accuracy: {correct/triggered*100:.1f}%' if triggered else '  No data')
cur.execute('SELECT COUNT(*) FROM resources WHERE times_triggered = 0')
never = cur.fetchone()[0]
cur.execute('SELECT COUNT(*) FROM resources WHERE times_triggered > 0')
active = cur.fetchone()[0]
print(f'  Never-triggered: {never}/{total} ({never/total*100:.1f}%)')
print(f'  Active (triggered >=1): {active}/{total} ({active/total*100:.1f}%)')

print()
print('=== 1l. Tier-3 resources (low quality) ===')
cur.execute("SELECT name, resource_type FROM resources WHERE tier = 3")
t3 = cur.fetchall()
print(f'  Total tier-3: {len(t3)}')
for row in t3[:15]:
    print(f'    {row[0]} ({row[1]})')

print()
print('=== 1m. Status distribution ===')
cur.execute('SELECT status, COUNT(*) FROM resources GROUP BY status')
for row in cur.fetchall():
    print(f'  {row[0]}: {row[1]}')

conn.close()
print('\nPhase 1 complete.')