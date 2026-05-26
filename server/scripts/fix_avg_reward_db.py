#!/usr/bin/env python3
"""Check and fix corrupted avg_reward in model_beliefs table."""
import sqlite3

conn = sqlite3.connect('/home/roshan/nexus/db/nexus.db')
conn.row_factory = sqlite3.Row

# Count corrupted rows
bad = conn.execute('''
    SELECT id, provider, model, task_type, total_requests, total_reward, avg_reward, alpha, beta
    FROM model_beliefs 
    WHERE total_requests > 0 
      AND ABS(avg_reward - CAST(total_reward AS REAL) / total_requests) > 0.001
    ORDER BY total_requests DESC
''').fetchall()

print(f"Corrupted rows (avg_reward != total_reward/total_requests): {len(bad)}")
for r in bad:
    correct_avg = r['total_reward'] / r['total_requests']
    print(f"  id={r['id']} model={r['model'][:50]} "
          f"requests={r['total_requests']} reward={r['total_reward']} "
          f"avg={r['avg_reward']} correct={correct_avg:.4f}")

# Fix corrupted rows
fixed = conn.execute('''
    UPDATE model_beliefs 
    SET avg_reward = CAST(total_reward AS REAL) / total_requests
    WHERE total_requests > 0 
      AND ABS(avg_reward - CAST(total_reward AS REAL) / total_requests) > 0.001
''')
conn.commit()
print(f"Fixed {fixed.rowcount} corrupted rows")

# Verify
remaining = conn.execute('''
    SELECT COUNT(*) FROM model_beliefs 
    WHERE total_requests > 0 
      AND ABS(avg_reward - CAST(total_reward AS REAL) / total_requests) > 0.001
''').fetchone()[0]
print(f"Remaining corrupted rows: {remaining}")

# Check alpha/beta vs total_requests
mismatch = conn.execute('''
    SELECT COUNT(*) FROM model_beliefs 
    WHERE total_requests > 0 AND ABS((alpha + beta - 2.0) - total_requests) > 0.01
''').fetchone()[0]
print(f"Rows where alpha+beta-2 != total_requests: {mismatch}")

conn.close()