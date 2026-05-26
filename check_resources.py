#!/usr/bin/env python3
"""Check current state of NEXUS resources for classification fixes."""
import sqlite3
conn = sqlite3.connect('/home/roshan/nexus/db/nexus.db')

# NULL cluster resources
null_cluster = conn.execute("SELECT COUNT(*) FROM resources WHERE cluster IS NULL OR cluster = ''").fetchone()[0]
# cluster = 'general' resources  
general_cluster = conn.execute("SELECT COUNT(*) FROM resources WHERE cluster = 'general'").fetchone()[0]
# confidence = 0.0 resources (effectively invisible to routing)
zero_conf = conn.execute("SELECT COUNT(*) FROM resources WHERE (confidence = 0.0 OR confidence IS NULL) AND status='active'").fetchone()[0]
zero_conf_with_cluster = conn.execute("SELECT COUNT(*) FROM resources WHERE (confidence = 0.0 OR confidence IS NULL) AND cluster != 'general' AND cluster IS NOT NULL AND cluster != '' AND status='active'").fetchone()[0]

# Active resources by cluster
clusters = conn.execute("SELECT cluster, COUNT(*) as cnt FROM resources WHERE status='active' GROUP BY cluster ORDER BY cnt DESC").fetchall()

# Confidence distribution
conf_dist = conn.execute("""
    SELECT 
        CASE 
            WHEN confidence IS NULL THEN 'NULL'
            WHEN confidence = 0.0 THEN '0.0'
            WHEN confidence < 0.3 THEN '<0.3'
            WHEN confidence < 0.5 THEN '<0.5'
            WHEN confidence < 0.7 THEN '<0.7'
            WHEN confidence < 0.9 THEN '<0.9'
            ELSE '0.9+'
        END as bucket,
        COUNT(*) as cnt
    FROM resources WHERE status='active'
    GROUP BY bucket ORDER BY bucket
""").fetchall()

# NULL cluster details
null_details = conn.execute("SELECT id, name, resource_type FROM resources WHERE (cluster IS NULL OR cluster = '') AND status='active' LIMIT 15").fetchall()

# Resources with embeddings missing
no_embedding = conn.execute("""
    SELECT COUNT(*) FROM resources r 
    WHERE r.status='active' AND r.id NOT IN (SELECT resource_id FROM resource_embeddings)
""").fetchone()[0]

# Embeddings table exists?
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
table_names = [t[0] for t in tables]

print(f'NULL/empty cluster: {null_cluster}')
print(f'General cluster: {general_cluster}')
print(f'Zero/NULL confidence (ALL active): {zero_conf}')
print(f'Zero/NULL confidence (excl general): {zero_conf_with_cluster}')
print(f'Resources missing embeddings: {no_embedding}')
print(f'\nTables: {table_names}')

print('\nActive resources by cluster:')
for row in clusters:
    print(f'  {row[0] or "NULL"}: {row[1]}')

print('\nConfidence distribution:')
for row in conf_dist:
    print(f'  {row[0]}: {row[1]}')

print('\nSample NULL cluster resources:')
for row in null_details:
    print(f'  id={row[0]} name={row[1]} type={row[2]}')

conn.close()