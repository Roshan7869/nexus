#!/usr/bin/env python3
"""Generate faiss_eval_dataset.json from routing_log in nexus.db."""
import sqlite3, json, random
from pathlib import Path

conn = sqlite3.connect('/home/roshan/nexus/db/nexus.db')
conn.row_factory = sqlite3.Row

rows = conn.execute('''
    SELECT DISTINCT task_summary, matched_skills, skill_name, project
    FROM routing_log 
    WHERE outcome = 'correct' 
      AND skill_name IS NOT NULL 
      AND skill_name != 'unknown'
      AND task_summary IS NOT NULL 
      AND LENGTH(TRIM(task_summary)) > 10
    ORDER BY id DESC
    LIMIT 500
''').fetchall()

seen_tasks = set()
dataset = []
for r in rows:
    task = r['task_summary'].strip()
    if task.lower() in seen_tasks:
        continue
    seen_tasks.add(task.lower())
    try:
        matched = json.loads(r['matched_skills'])
    except (json.JSONDecodeError, TypeError):
        matched = []
    skill_name = r['skill_name']
    expected = list(set([skill_name] + matched))
    expected = [s for s in expected if s and s != 'unknown']
    if not expected:
        continue
    dataset.append({
        "query_id": len(dataset) + 1,
        "task_text": task,
        "expected_resource_names": expected,
        "expected_resource_types": ["skill"],
        "tags": [r['project']],
        "difficulty": "medium"
    })
    if len(dataset) >= 50:
        break

adversarial = [
    {"query_id": 0, "task_text": "what is the meaning of life", "expected_resource_names": [], "expected_resource_types": [], "tags": [], "difficulty": "adversarial"},
    {"query_id": 0, "task_text": "hello world", "expected_resource_names": [], "expected_resource_types": [], "tags": [], "difficulty": "adversarial"},
    {"query_id": 0, "task_text": "the quick brown fox jumps over the lazy dog", "expected_resource_names": [], "expected_resource_types": [], "tags": [], "difficulty": "adversarial"},
    {"query_id": 0, "task_text": "tell me a joke", "expected_resource_names": [], "expected_resource_types": [], "tags": [], "difficulty": "adversarial"},
    {"query_id": 0, "task_text": "1234567890", "expected_resource_names": [], "expected_resource_types": [], "tags": [], "difficulty": "adversarial"},
    {"query_id": 0, "task_text": "write a poem about artificial intelligence", "expected_resource_names": [], "expected_resource_types": [], "tags": [], "difficulty": "adversarial"},
]
dataset.extend(adversarial)
random.shuffle(dataset)
for i, entry in enumerate(dataset):
    entry['query_id'] = i + 1

# Write to scripts location (read by router.py)
p1 = Path.home() / 'nexus/server/scripts/faiss_eval_dataset.json'
with open(p1, 'w') as f:
    json.dump(dataset, f, indent=2)
print(f"Created: {p1} ({len(dataset)} entries)")

# Write to db/faiss/ location (referenced in task)
p2 = Path('/home/roshan/nexus/db/faiss/faiss_eval_dataset.json')
with open(p2, 'w') as f:
    json.dump(dataset, f, indent=2)
print(f"Created: {p2} ({len(dataset)} entries)")
print(f"  Non-adversarial: {sum(1 for d in dataset if d['expected_resource_names'])}")
print(f"  Adversarial: {sum(1 for d in dataset if not d['expected_resource_names'])}")
