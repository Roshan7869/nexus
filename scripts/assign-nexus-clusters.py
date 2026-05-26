#!/usr/bin/env python3
import sqlite3
conn = sqlite3.connect('/home/roshan/nexus/db/nexus.db')
c = conn.cursor()
c.execute("UPDATE resources SET cluster='devops_infra' WHERE name='nexus-startup'")
c.execute("UPDATE resources SET cluster='devops_infra' WHERE name='nexus-opencode-connector'")
c.execute("UPDATE resources SET cluster='analyzer_planner' WHERE name='nexus-unified-server'")
conn.commit()
u = c.execute("SELECT COUNT(*) FROM resources WHERE cluster IS NULL OR cluster=''").fetchone()[0]
d = c.execute("SELECT COUNT(*) FROM resources WHERE status='degraded'").fetchone()[0]
t = c.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
print(f"Unassigned: {u}, Degraded: {d}, Active: {t-d}, Total: {t}")
conn.close()
