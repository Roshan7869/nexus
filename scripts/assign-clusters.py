#!/usr/bin/env python3
"""Assign clusters to unassigned resources and fix zero-token issues."""
import sqlite3, sqlite_vec, ollama, re

NEXUS_DB = '/home/roshan/nexus/db/nexus.db'
conn = sqlite3.connect(NEXUS_DB)
conn.enable_load_extension(True)
sqlite_vec.load(conn)
cur = conn.cursor()

# Cluster assignments based on domain analysis of each resource
cluster_map = {
    # optillm family - reasoning proxy → analyzer_planner
    'optillm':               'analyzer_planner',
    'optillm-moa':           'analyzer_planner',
    'optillm-mcts':          'analyzer_planner',
    'optillm-bon':           'analyzer_planner',
    'optillm-cot-reflection':'analyzer_planner',
    'optillm-plansearch':    'analyzer_planner',
    'optillm-cepo':          'analyzer_planner',
    'optillm-mcp-plugin':    'analyzer_planner',
    'optillm-memory-plugin': 'analyzer_planner',
    'optillm-plugins':       'analyzer_planner',

    # yournextstore - e-commerce → backend_api
    'yournextstore':         'backend_api',
    'yournextstore-agent':   'backend_api',

    # scrapling - scraping framework → knowledge_wiki (data gathering)
    'scrapling':             'knowledge_wiki',
    'scrapling-mcp':         'knowledge_wiki',
    'scrapling-cli':         'knowledge_wiki',

    # gemma4 browser extension → frontend_ui
    'gemma4-browser-extension': 'frontend_ui',

    # LLM wiki → knowledge_wiki
    'llm-wiki-obsidian':     'knowledge_wiki',

    # medusa - headless commerce → backend_api
    'medusa':                'backend_api',
    'medusa-commerce-platform': 'backend_api',

    # sherlock - OSINT → analyzer_planner
    'sherlock':              'analyzer_planner',

    # scrapecraft - scraping → knowledge_wiki
    'scrapecraft':           'knowledge_wiki',

    # leadiq - business/leads → general
    'leadiq-nexus-learnings': 'general',
    'leadiq-wiring-execution-plan': 'general',

    # CLI tools - system utilities → general
    'ollama':                'devops_infra',
    'docker':                'devops_infra',
    'curl':                  'devops_infra',
    'wget':                  'devops_infra',
    'npx':                   'devops_infra',
    'git':                   'devops_infra',
    'bun':                   'devops_infra',
    'cargo':                 'devops_infra',
    'supabase-cli':          'backend_api',
    'aider':                 'analyzer_planner',
    'tmux':                  'devops_infra',
}

# Fix cluster assignments
assigned = 0
for name, cluster in cluster_map.items():
    cur.execute("UPDATE resources SET cluster=? WHERE name=? AND (cluster IS NULL OR cluster='')", (cluster, name))
    if cur.rowcount > 0:
        # FIX 1: Sync to cluster_membership (authoritative routing source)
        cur.execute(
            "INSERT OR REPLACE INTO cluster_membership (cluster_name, resource_id, affinity) "
            "SELECT ?, id, 1.0 FROM resources WHERE name=? AND cluster=?",
            (cluster, name, cluster),
        )
        print(f"  ASSIGNED: {name:35s} → {cluster}")
        assigned += 1

# Fix the zero-token resources
cur.execute("SELECT id, name, body_text FROM resources WHERE token_count=0 AND body_text IS NOT NULL AND body_text!=''")
fixed_tokens = 0
for r in cur.fetchall():
    rid, name, body = r
    tcount = len(body) // 3
    cur.execute("UPDATE resources SET token_count=? WHERE id=?", (max(tcount, 1), rid))
    print(f"  FIXED TOKEN: {name:35s} → {max(tcount,1)} tokens")
    fixed_tokens += 1

# Degraded resources with wrong>0: reset them to active + zero out stats
cur.execute("SELECT id, name FROM resources WHERE status='degraded' AND times_wrong=0 AND times_correct=0")
reset_degraded = cur.fetchall()
for r in reset_degraded:
    rid, name = r
    cur.execute("UPDATE resources SET status='active' WHERE id=?", (rid,))
    print(f"  RESET TO ACTIVE: {name} (degraded but never used)")

conn.commit()

print(f"\n=== SUMMARY ===")
print(f"  Cluster assignments made: {assigned}")
print(f"  Token counts fixed: {fixed_tokens}")
print(f"  Degraded→Active reset: {len(reset_degraded)}")

# Final unassigned check
unassigned = cur.execute("SELECT COUNT(*) FROM resources WHERE cluster IS NULL OR cluster=''").fetchone()[0]
print(f"  Remaining unassigned: {unassigned}")
total_degraded = cur.execute("SELECT COUNT(*) FROM resources WHERE status='degraded'").fetchone()[0]
print(f"  Remaining degraded: {total_degraded}")
total = cur.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
print(f"  Total resources: {total}")

conn.close()
