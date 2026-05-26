#!/usr/bin/env python3
"""Final NEXUS state verification."""
import sqlite3, sqlite_vec

NEXUS_DB = '/home/roshan/nexus/db/nexus.db'
conn = sqlite3.connect(NEXUS_DB)
conn.enable_load_extension(True)
sqlite_vec.load(conn)
c = conn.cursor()

t = c.execute('SELECT COUNT(*) FROM resources').fetchone()[0]
d = c.execute("SELECT COUNT(*) FROM resources WHERE status='degraded'").fetchone()[0]
a = c.execute("SELECT COUNT(*) FROM resources WHERE status='active'").fetchone()[0]
u = c.execute("SELECT COUNT(*) FROM resources WHERE cluster IS NULL OR cluster=''").fetchone()[0]
zt = c.execute('SELECT COUNT(*) FROM resources WHERE token_count=0').fetchone()[0]

print('NEXUS FINAL STATE:')
print(f'  Total:       {t}')
print(f'  Active:      {a}')
print(f'  Degraded:    {d}')
print(f'  Unassigned:  {u}')
print(f'  Zero-tokens: {zt}')

print('\nBY TYPE:')
c.execute('SELECT resource_type, COUNT(*) FROM resources GROUP BY resource_type ORDER BY COUNT(*) DESC')
for r in c.fetchall():
    print(f'  {r[0]:20s} {r[1]}')

print('\nCLUSTER DISTRIBUTION:')
c.execute("SELECT cluster, COUNT(*) FROM resources WHERE cluster IS NOT NULL AND cluster != '' GROUP BY cluster ORDER BY COUNT(*) DESC")
for r in c.fetchall():
    print(f'  {r[0]:20s} {r[1]}')

print('\nREMAINING UNASSIGNED:')
c.execute("SELECT name, resource_type FROM resources WHERE cluster IS NULL OR cluster=''")
for r in c.fetchall():
    print(f'  {r[0]:30s} {r[1]}')

print('\nREMAINING DEGRADED:')
c.execute("SELECT name, resource_type, times_correct, times_wrong FROM resources WHERE status='degraded'")
for r in c.fetchall():
    print(f'  {r[0]:30s} {r[1]:12s} correct={r[2]} wrong={r[3]}')

conn.close()
