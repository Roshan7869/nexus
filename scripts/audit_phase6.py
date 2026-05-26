#!/usr/bin/env python3
"""Phase 6: FAISS index health and detailed retrieval audit"""
import sqlite3, numpy as np, json

conn = sqlite3.connect('db/nexus.db')
cur = conn.cursor()

print('=== 6a. FAISS index check ===')
try:
    import faiss
    index = faiss.read_index('db/faiss/resources.index')
    print(f'  FAISS vectors: {index.ntotal}')
    print(f'  Dimension: {index.d}')
    print(f'  Type: {type(index).__name__}')
except Exception as e:
    print(f'  FAISS load error: {e}')

print()
print('=== 6b. Embedding dimension consistency ===')
cur.execute('SELECT COUNT(*) FROM resource_embeddings WHERE embedding IS NOT NULL')
emb_count = cur.fetchone()[0]
cur.execute('SELECT COUNT(*) FROM resources')
res_count = cur.fetchone()[0]
print(f'  Resources: {res_count}')
print(f'  Embedding rows: {emb_count}')
print(f'  Coverage: {emb_count/res_count*100:.1f}%')

# Sample an embedding to check dimension
cur.execute('SELECT resource_id, model_name, LENGTH(embedding) FROM resource_embeddings LIMIT 1')
row = cur.fetchone()
if row:
    print(f'  Sample embedding: resource_id={row[0]}, model={row[1]}, blob_size={row[2]} bytes')

print()
print('=== 6c. Cluster centroid confidence ===')
cur.execute('''
    SELECT cluster, AVG(cluster_confidence), MIN(cluster_confidence), MAX(cluster_confidence), COUNT(*)
    FROM resources 
    WHERE cluster IS NOT NULL 
    GROUP BY cluster 
    ORDER BY AVG(cluster_confidence) DESC
''')
for row in cur.fetchall():
    avg_conf = row[1] if row[1] else 0
    min_conf = row[2] if row[2] else 0
    max_conf = row[3] if row[3] else 0
    print(f'  {row[0]}: avg_conf={avg_conf:.3f} min={min_conf:.3f} max={max_conf:.3f} count={row[4]}')

# Low confidence resources (borderline cluster assignment)
print()
print('=== 6d. Low-confidence cluster assignments (confidence < 0.3) ===')
cur.execute('''
    SELECT name, resource_type, cluster, cluster_confidence
    FROM resources
    WHERE cluster_confidence < 0.3 AND cluster IS NOT NULL
    ORDER BY cluster_confidence ASC
    LIMIT 20
''')
low_conf = cur.fetchall()
print(f'  Count: {len(low_conf)} below 0.3 threshold')
for row in low_conf:
    print(f'  {row[0]} ({row[1]}) -> {row[2]} conf={row[3]:.3f}')

print()
print('=== 6e. Duplicate body_text check ===')
cur.execute('''
    SELECT body_text, COUNT(*) as cnt, GROUP_CONCAT(name) 
    FROM resources 
    WHERE LENGTH(body_text) > 20
    GROUP BY body_text 
    HAVING cnt > 1 
    ORDER BY cnt DESC
    LIMIT 15
''')
dupes = cur.fetchall()
if dupes:
    print(f'  Found {len(dupes)} duplicate body_text groups:')
    for row in dupes:
        names = row[2][:80] if len(row[2]) > 80 else row[2]
        print(f'    {row[1]}x duplicates: {names}')
else:
    print('  No duplicate body_text found.')

print()
print('=== 6f. Resource name duplicates ===')
cur.execute('''
    SELECT name, COUNT(*) as cnt
    FROM resources
    GROUP BY name
    HAVING cnt > 1
    ORDER BY cnt DESC
    LIMIT 15
''')
name_dupes = cur.fetchall()
if name_dupes:
    print(f'  Found {len(name_dupes)} duplicate name groups:')
    for row in name_dupes:
        print(f'    {row[0]}: {row[1]}x')
else:
    print('  No duplicate names found.')

# Cluster assignment sanity - are resources in sensible clusters?
print()
print('=== 6g. Cross-domain contamination check ===')
suspect_combos = [
    ('devops_infra', 'skill'),
    ('quality_security', 'design_system'),
    ('frontend_ui', 'command'),
    ('backend_api', 'design_system'),
]
for cluster, rtype in suspect_combos:
    cur.execute('''
        SELECT r.name FROM resources r
        JOIN cluster_membership cm ON r.id = cm.resource_id
        WHERE cm.cluster_name = ? AND r.resource_type = ?
        LIMIT 5
    ''', (cluster, rtype))
    results = cur.fetchall()
    if results:
        print(f'  {cluster}/{rtype}: {[r[0] for r in results]}')
    else:
        print(f'  {cluster}/{rtype}: none')

conn.close()
print('\nPhase 6 complete.')