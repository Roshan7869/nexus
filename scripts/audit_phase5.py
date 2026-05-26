#!/usr/bin/env python3
"""Phase 5: Wired-up check - which resources are callable vs metadata-only shells"""
import sqlite3, os, json

conn = sqlite3.connect('db/nexus.db')
cur = conn.cursor()

# Skills with file_path check
print('=== 5a. Skills - file_path resolution ===')
cur.execute("SELECT name, file_path, resource_type, tier FROM resources WHERE resource_type = 'skill' LIMIT 50")
skills = cur.fetchall()
present = 0
missing = 0
missing_list = []
for name, file_path, rtype, tier in skills:
    if file_path and os.path.exists(file_path):
        present += 1
    else:
        missing += 1
        if missing <= 15:
            missing_list.append(f'  {name} -> {file_path}')

cur.execute("SELECT COUNT(*) FROM resources WHERE resource_type = 'skill'")
total_skills = cur.fetchone()[0]
print(f'  Sample: {present} present, {missing} missing (out of {len(skills)} sampled)')
if missing_list:
    print('  Missing files:')
    for m in missing_list:
        print(m)

# Check full skill path resolution
print()
print('=== 5b. Full skill file_path resolution ===')
cur.execute("SELECT name, file_path FROM resources WHERE resource_type = 'skill'")
all_skills = cur.fetchall()
path_missing = 0
path_present = 0
path_empty = 0
for name, file_path in all_skills:
    if not file_path or file_path == '':
        path_empty += 1
    elif os.path.exists(file_path):
        path_present += 1
    else:
        path_missing += 1

print(f'  Total skills: {len(all_skills)}')
print(f'  File present on disk: {path_present}')
print(f'  File missing on disk: {path_missing}')
print(f'  No file_path set: {path_empty}')

# Check MCP resources - are the servers actually connected?
print()
print('=== 5c. MCP resources - active vs metadata ===')
cur.execute("SELECT name, description, tier FROM resources WHERE resource_type = 'mcp' ORDER BY tier, name")
mcps = cur.fetchall()
print(f'  Total MCP entries in DB: {len(mcps)}')
for name, desc, tier in mcps:
    print(f'  [{tier}] {name}: {(desc or "")[:60]}')

# Check body_text quality
print()
print('=== 5d. Body text quality (non-empty, meaningful content) ===')
cur.execute("SELECT resource_type, COUNT(*) FROM resources WHERE body_text IS NOT NULL AND LENGTH(body_text) > 50 GROUP BY resource_type")
for row in cur.fetchall():
    print(f'  {row[0]} with body_text >50 chars: {row[1]}')
cur.execute("SELECT resource_type, COUNT(*) FROM resources WHERE body_text IS NULL OR body_text = '' OR LENGTH(body_text) <= 50 GROUP BY resource_type")
for row in cur.fetchall():
    print(f'  {row[0]} with weak/empty body_text: {row[1]}')

# Check if tier-3 are dead weight
print()
print('=== 5e. Tier-3 resources deep dive ===')
cur.execute("SELECT name, resource_type, description, body_text FROM resources WHERE tier = 3")
t3_resources = cur.fetchall()
print(f'  Total tier-3: {len(t3_resources)}')
for name, rtype, desc, body in t3_resources:
    desc_len = len(desc) if desc else 0
    body_len = len(body) if body else 0
    print(f'  {name} ({rtype}): desc={desc_len}chars body={body_len}chars')

# Check times_triggered = 0 (cold resources)
print()
print('=== 5f. Never-triggered resources by type ===')
cur.execute("""
    SELECT resource_type, COUNT(*) 
    FROM resources 
    WHERE times_triggered = 0 
    GROUP BY resource_type 
    ORDER BY COUNT(*) DESC
""")
for row in cur.fetchall():
    print(f'  {row[0]}: {row[1]} never triggered')

cur.execute("""
    SELECT resource_type, COUNT(*) 
    FROM resources 
    WHERE times_triggered > 0 
    GROUP BY resource_type 
    ORDER BY COUNT(*) DESC
""")
print()
print('  Triggered at least once:')
for row in cur.fetchall():
    print(f'  {row[0]}: {row[1]} triggered >= 1')

# Top triggered resources
print()
print('=== 5g. Top 20 most-triggered resources ===')
cur.execute("SELECT name, resource_type, times_triggered, times_correct, times_wrong FROM resources ORDER BY times_triggered DESC LIMIT 20")
for row in cur.fetchall():
    acc = f"{row[3]/row[2]*100:.0f}%" if row[2] > 0 else "N/A"
    print(f'  {row[0]} ({row[1]}): triggered={row[2]} correct={row[3]} wrong={row[4]} acc={acc}')

conn.close()
print('\nPhase 5 complete.')