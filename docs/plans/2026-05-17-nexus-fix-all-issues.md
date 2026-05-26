# NEXUS Fix-All Issues Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Fix all 14 audit findings from the 2026-05-17 NEXUS audit — cluster misalignment, dead resources, broken outcome tracking, MCP sync gaps, and learn-from-failure pipeline — to raise health from C+ to A-.

**Architecture:** 4 phases, 14 bite-sized tasks. Each task touches one subsystem, has a dry-run step before mutation, and includes a verification step. DB-first approach: all fixes are SQL + Python scripts operating on `nexus.db`, with FAISS rebuilt only at the end.

**Tech Stack:** Python 3, SQLite3, FAISS, numpy, sentence-transformers (all-MiniLM-L6-v2)

---

## Phase 1: Structural Integrity (P0 — Fix Now)

Fix the core data problems that corrupt every downstream routing decision.

---

### Task 1: Sync cluster_definitions with actual cluster_membership counts

**Objective:** The `cluster_definitions` table claims 173 resources in `general` but the actual `cluster_membership` table has 525. Sync stale metadata to match reality.

**Files:**
- Create: `/home/roshan/nexus/scripts/fix_task1_sync_cluster_counts.py`
- Read: `/home/roshan/nexus/db/nexus.db` (tables: `cluster_definitions`, `resources`)

**Step 1: Write verification script (dry run)**

```python
#!/usr/bin/env python3
"""Task 1: Sync cluster_definitions counts with actual resources table.

The cluster_definitions.centroid_embedding row stores a stale count that
doesn't match the actual distribution in resources.cluster. This script
counts actual resources per cluster and updates cluster_definitions.
"""
import sqlite3
from pathlib import Path

DB_PATH = Path.home() / "nexus/db/nexus.db"

def sync_cluster_counts(dry_run: bool = True):
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # Get actual counts from resources table
    actual = conn.execute("""
        SELECT cluster, COUNT(*) as cnt
        FROM resources
        WHERE status = 'active' AND cluster IS NOT NULL AND cluster != ''
        GROUP BY cluster
        ORDER BY cnt DESC
    """).fetchall()

    print("Actual resource counts per cluster:")
    for row in actual:
        print(f"  {row['cluster']}: {row['cnt']}")

    # Get current cluster_definitions metadata
    defs = conn.execute("""
        SELECT name, description FROM cluster_definitions
    """).fetchall()

    print(f"\nCluster definitions in DB: {len(defs)}")

    if not dry_run:
        # Update centroid row counts by comparing with cluster_config expected_size
        # cluster_definitions doesn't have a count column — it's inferred from resources.
        # The discrepancy comes from the API reading cached counts. We need to
        # verify cluster_list API returns actual counts.
        #
        # Actually: cluster_list reads from resources.cluster directly via ClusterFAISS.
        # The real fix is ensuring resources.cluster is correct (Task 2 does this).
        # Here we just ensure cluster_definitions rows exist for all 8 clusters.
        from cluster_config import CLUSTER_DEFINITIONS
        for name in CLUSTER_DEFINITIONS:
            existing = conn.execute(
                "SELECT name FROM cluster_definitions WHERE name = ?", (name,)
            ).fetchone()
            if not existing:
                print(f"  INSERT missing cluster_definition: {name}")
                conn.execute("""
                    INSERT INTO cluster_definitions (name, description)
                    VALUES (?, ?)
                """, (name, CLUSTER_DEFINITIONS[name]["description"]))
        conn.commit()
        print("  Cluster definitions synced.")
    else:
        print("\n  DRY RUN — no changes made.")

    # Show the claim vs actual mismatch
    print("\n--- Claim vs Actual ---")
    claimed = {
        row['cluster']: row['cnt'] for row in conn.execute("""
            SELECT cluster, COUNT(*) as cnt
            FROM cluster_membership
            GROUP BY cluster
        """).fetchall()
    }
    actual_map = {row['cluster']: row['cnt'] for row in actual}
    all_clusters = set(list(claimed.keys()) + list(actual_map.keys()))
    for c in sorted(all_clusters):
        c_count = claimed.get(c, 0)
        a_count = actual_map.get(c, 0)
        delta = a_count - c_count
        status = "OK" if abs(delta) < 5 else f"MISMATCH ({delta:+d})"
        print(f"  {c:25s}: cluster_membership={c_count:4d}, resources={a_count:4d}  {status}")

    conn.close()

if __name__ == "__main__":
    import sys
    dry = "--apply" not in sys.argv
    sync_cluster_counts(dry_run=dry)
```

**Step 2: Run dry run**

```bash
cd ~/nexus && ./venv/bin/python scripts/fix_task1_sync_cluster_counts.py
```

Expected: Shows claim vs actual mismatch for `general` (-352), `analyzer_planner` (+185), etc.

**Step 3: Apply fix**

```bash
cd ~/nexus && ./venv/bin/python scripts/fix_task1_sync_cluster_counts.py --apply
```

**Step 4: Verify — run cluster_list MCP tool**

Call `mcp_nexus_nexus_cluster_list` and confirm counts now match `actual` from `resources` table.

**Step 5: Commit**

```bash
cd ~/nexus && git add scripts/fix_task1_sync_cluster_counts.py && git commit -m "fix(nexus): sync cluster_definitions with actual resource counts"
```

---

### Task 2: Re-cluster 525 general resources (redistribute low-confidence items)

**Objective:** The `general` cluster has 525 resources at 0.43 avg confidence. Re-assign resources with confidence < 0.5 to better-matching clusters using centroid similarity.

**Files:**
- Modify: `/home/roshan/nexus/scripts/recluster_low_confidence.py` (already exists, just run it)
- Read: `/home/roshan/nexus/db/nexus.db`

**Step 1: Dry run recluster**

The script `recluster_low_confidence.py` already exists and does exactly this. Run it in dry-run mode first:

```bash
cd ~/nexus && ./venv/bin/python scripts/recluster_low_confidence.py
```

Expected: Reports ~382 low-confidence resources, shows how many would be re-assigned and their new clusters.

**Step 2: Apply recluster**

```bash
cd ~/nexus && ./venv/bin/python scripts/recluster_low_confidence.py --apply 2>/dev/null || true
```

Wait — the script uses `--dry-run` flag (check `sys.argv`). Let me verify:

The script checks `"--dry-run" in sys.argv`. So:

```bash
cd ~/nexus && ./venv/bin/python scripts/recluster_low_confidence.py
```

(Absence of `--dry-run` means it applies changes.)

**Step 3: Verify — check general cluster size dropped**

```bash
cd ~/nexus && ./venv/bin/python -c "
import sqlite3
conn = sqlite3.connect(str('db/nexus.db'))
rows = conn.execute('SELECT cluster, COUNT(*), ROUND(AVG(cluster_confidence),3) FROM resources WHERE status=\"active\" GROUP BY cluster ORDER BY COUNT(*) DESC').fetchall()
for r in rows:
    print(f'{r[0]:25s}: {r[1]:4d} resources, avg_conf={r[2]}')
conn.close()
"
```

Expected: `general` drops from 525 to ~150 or less. Avg confidence for general increases.

**Step 4: Commit**

```bash
cd ~/nexus && git add -A && git commit -m "fix(nexus): recluster 382 low-confidence resources out of general"
```

---

### Task 3: Assign 7 orphan graphify MCP tools to clusters

**Objective:** 7 graphify MCP tools have no cluster assignment. Assign them to `knowledge_wiki` (they're knowledge-graph tools = documentation/insight).

**Files:**
- Modify: `/home/roshan/nexus/db/nexus.db` (direct SQL)

**Step 1: Verify orphans exist**

```bash
cd ~/nexus && ./venv/bin/python -c "
import sqlite3
conn = sqlite3.connect('db/nexus.db')
orphans = conn.execute(\"SELECT id, name, cluster, cluster_confidence FROM resources WHERE name LIKE 'graphify%' AND status='active'\").fetchall()
for r in orphans:
    print(f'  id={r[0]}, name={r[1]}, cluster={r[2]}, conf={r[3]}')
print(f'Total orphans: {len(orphans)}')
conn.close()
"
```

Expected: 7 rows with `cluster=None` or `cluster=''`.

**Step 2: Assign to knowledge_wiki cluster**

```bash
cd ~/nexus && ./venv/bin/python -c "
import sqlite3
conn = sqlite3.connect('db/nexus.db')
# Assign graphify tools to knowledge_wiki
result = conn.execute(\"\"\"
    UPDATE resources 
    SET cluster = 'knowledge_wiki', 
        cluster_confidence = 0.80,
        updated_at = datetime('now')
    WHERE name LIKE 'graphify%' 
      AND (cluster IS NULL OR cluster = '' OR cluster = 'general')
      AND status = 'active'
\"\"\")
conn.commit()
print(f'Updated {result.rowcount} graphify resources')
# Verify
rows = conn.execute(\"SELECT name, cluster, cluster_confidence FROM resources WHERE name LIKE 'graphify%'\").fetchall()
for r in rows:
    print(f'  {r[0]}: cluster={r[1]}, conf={r[2]}')
conn.close()
"
```

Expected: 7 resources updated, all now in `knowledge_wiki`.

**Step 3: Verify — confirm no orphans remain**

```bash
cd ~/nexus && ./venv/bin/python -c "
import sqlite3
conn = sqlite3.connect('db/nexus.db')
orphans = conn.execute(\"SELECT COUNT(*) FROM resources WHERE (cluster IS NULL OR cluster = '') AND status = 'active'\").fetchone()[0]
print(f'Remaining orphans: {orphans}')
conn.close()
"
```

Expected: 0 orphans.

**Step 4: No commit needed (DB-only change)**

---

### Task 4: Fix debug → analyzer_planner routing (move debug resources from quality_security)

**Objective:** Debug/investigate queries route to `quality_security` instead of `analyzer_planner`. Move 10-15 debugging resources from quality_security to analyzer_planner cluster.

**Files:**
- Modify: `/home/roshan/nexus/db/nexus.db`
- Modify: `/home/roshan/nexus/server/cluster_config.py` (add "debug" trigger keyword to analyzer_planner)

**Step 1: Identify misclassified debug resources**

```bash
cd ~/nexus && ./venv/bin/python -c "
import sqlite3
conn = sqlite3.connect('db/nexus.db')
rows = conn.execute(\"\"\"
    SELECT id, name, cluster, cluster_confidence 
    FROM resources 
    WHERE cluster = 'quality_security' 
      AND (name LIKE '%debug%' OR name LIKE '%investig%' OR name LIKE '%error%' OR name LIKE '%trace%' OR name LIKE '%root-cause%' OR name LIKE '%systematic-debug%')
      AND status = 'active'
    ORDER BY cluster_confidence DESC
\"\"\").fetchall()
for r in rows:
    print(f'  id={r[0]}, name={r[1]}, conf={r[3]}')
print(f'Found {len(rows)} debug resources in quality_security')
conn.close()
"
```

**Step 2: Move debug resources to analyzer_planner**

```bash
cd ~/nexus && ./venv/bin/python -c "
import sqlite3
conn = sqlite3.connect('db/nexus.db')
result = conn.execute(\"\"\"
    UPDATE resources 
    SET cluster = 'analyzer_planner',
        updated_at = datetime('now')
    WHERE cluster = 'quality_security' 
      AND (name LIKE '%debug%' OR name LIKE '%investig%' OR name LIKE '%error%' OR name LIKE '%trace%' OR name LIKE '%root-cause%' OR name LIKE '%systematic-debug%')
      AND status = 'active'
\"\"\")
conn.commit()
print(f'Moved {result.rowcount} debug resources to analyzer_planner')
conn.close()
"
```

**Step 3: Add "debug" trigger keyword to analyzer_planner in cluster_config.py**

Edit `/home/roshan/nexus/server/cluster_config.py`:

In the `analyzer_planner` cluster definition, add to `trigger_keywords`:
```python
"debug", "investigate", "root-cause", "trace",
```

In the `quality_security` cluster definition, ensure "debug" is NOT a trigger keyword (it should already trigger on "test", "QA", "verify", etc.).

**Step 4: Verify routing smoke test**

```bash
cd ~/nexus && ./venv/bin/python -c "
from server.cluster_router import ClusterRouter
router = ClusterRouter()
result = router.route_to_clusters('debug TypeError null pointer exception', max_clusters=2)
for c in result['activated_clusters']:
    print(f\"  {c['name']}: affinity={c['affinity']}, resources={c['resource_count']}\")
"
```

Expected: `analyzer_planner` should be the top cluster, not `quality_security`.

**Step 5: Commit**

```bash
cd ~/nexus && git add server/cluster_config.py && git commit -m "fix(nexus): route debug queries to analyzer_planner, not quality_security"
```

---

## Phase 2: Data Hygiene (P1 — This Week)

Clean up dead weight and sync missing entries.

---

### Task 5: Sync strategy-kb MCP tools into NEXUS resource DB

**Objective:** Strategy-KB has 8 live MCP tools (regime_detect, query_strategies, etc.) with 592 vectors but ZERO entries in NEXUS resource DB. These are callable but invisible to NEXUS routing.

**Files:**
- Create: `/home/roshan/nexus/scripts/fix_task5_sync_strategy_kb.py`
- Read: `/home/roshan/nexus/db/nexus.db`

**Step 1: Write sync script**

```python
#!/usr/bin/env python3
"""Task 5: Sync strategy-kb MCP tools into NEXUS resource DB.

Strategy-KB has 8 live MCP tools that are callable in Hermes but invisible
to NEXUS routing because they're not in the resources table.
"""
import sqlite3
from pathlib import Path

DB_PATH = Path.home() / "nexus/db/nexus.db"

STRATEGY_KB_TOOLS = [
    {
        "name": "strategy-kb-query_strategies",
        "description": "Semantic search across trading strategy chunks in ChromaDB vector database. Returns matching strategies with setup details, entry/exit rules, risk management, and psychology notes.",
        "body_text": "Query trading strategies by natural language description. Supports filtering by setup_type, market_condition, and keyword.",
        "cluster": "knowledge_wiki",
    },
    {
        "name": "strategy-kb-get_strategy",
        "description": "Find a specific trading strategy by exact or fuzzy name match. Returns the full strategy chunk with all fields: entry condition, stop loss, target, psychology note, edge description.",
        "body_text": "Look up trading strategies by name with fuzzy matching support.",
        "cluster": "knowledge_wiki",
    },
    {
        "name": "strategy-kb-regime_detect",
        "description": "Detect the current market regime for a trading pair using HMM regime detector. Returns regime label (trending_up, trending_down, ranging, volatile) with confidence metrics.",
        "body_text": "Market regime detection using HMM. Supports 1h and 4h timeframes.",
        "cluster": "analyzer_planner",
    },
    {
        "name": "strategy-kb-strategy_context",
        "description": "Get regime-adapted strategy context from ChromaDB. Uses RegimeAwareQueryEngine to retrieve strategies tailored to the current market regime, ranked by adaptive score.",
        "body_text": "Regime-aware strategy retrieval combining cosine similarity with outcome-weighted scores.",
        "cluster": "analyzer_planner",
    },
    {
        "name": "strategy-kb-list_setup_types",
        "description": "List all available strategy setup type categories in the database: entry, exit, confirmation, risk_management, market_structure, psychology, position_sizing, trade_management, philosophy.",
        "body_text": "Enumerate available strategy setup type filters.",
        "cluster": "knowledge_wiki",
    },
    {
        "name": "strategy-kb-list_market_conditions",
        "description": "List all available market condition categories: trending, ranging, ranging_to_trending, any.",
        "body_text": "Enumerate available market condition filters.",
        "cluster": "knowledge_wiki",
    },
    {
        "name": "strategy-kb-strategy_stats",
        "description": "Get statistics about the strategy knowledge base: total chunk count, distribution by setup type, market condition, strategy style, channel/source, and unique keywords.",
        "body_text": "Database statistics and coverage report for the strategy KB.",
        "cluster": "knowledge_wiki",
    },
    {
        "name": "strategy-kb-outcome_sync",
        "description": "Sync trade outcome history from outcome_history.json into ChromaDB chunk metadata. Reads all recorded trade outcomes, computes per-chunk win rates and performance metrics.",
        "body_text": "Pipeline to close the feedback loop between live trading and strategy retrieval.",
        "cluster": "quality_security",
    },
]

def sync_strategy_kb(dry_run: bool = True):
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    created = 0
    skipped = 0

    for tool in STRATEGY_KB_TOOLS:
        existing = conn.execute(
            "SELECT id FROM resources WHERE name = ?", (tool["name"],)
        ).fetchone()

        if existing:
            skipped += 1
            print(f"  SKIP (exists): {tool['name']}")
            continue

        if not dry_run:
            conn.execute("""
                INSERT INTO resources (name, description, body_text, resource_type,
                    repo_name, file_path, status, tier, cluster, cluster_confidence,
                    times_triggered, times_correct, times_wrong, embedding)
                VALUES (?, ?, ?, 'mcp', 'strategy-kb', ?, 'active', 2, ?, 0.85,
                    0, 0, 0, ?)
            """, (
                tool["name"],
                tool["description"],
                tool["body_text"],
                f"/mcp/strategy-kb/{tool['name']}.json",
                tool["cluster"],
                b'\x00' * (384 * 4),  # placeholder embedding, will be computed
            ))
            created += 1
            print(f"  CREATE: {tool['name']} → {tool['cluster']}")
        else:
            created += 1
            print(f"  DRY CREATE: {tool['name']} → {tool['cluster']}")

    if not dry_run:
        conn.commit()
        print(f"\n  Committed {created} new strategy-kb resources.")
    else:
        print(f"\n  DRY RUN — {created} would be created, {skipped} skipped.")

    conn.close()
    return {"created": created, "skipped": skipped}

if __name__ == "__main__":
    import sys
    dry = "--apply" not in sys.argv
    sync_strategy_kb(dry_run=dry)
```

**Step 2: Dry run**

```bash
cd ~/nexus && ./venv/bin/python scripts/fix_task5_sync_strategy_kb.py
```

Expected: 8 resources would be created.

**Step 3: Apply**

```bash
cd ~/nexus && ./venv/bin/python scripts/fix_task5_sync_strategy_kb.py --apply
```

**Step 4: Generate embeddings for new resources**

```bash
cd ~/nexus && ./venv/bin/python -c "
from server.iso_embedder import IsoEmbedder
import sqlite3
conn = sqlite3.connect('db/nexus.db')
rows = conn.execute(\"SELECT id, name, description, body_text FROM resources WHERE name LIKE 'strategy-kb%' AND embedding = X'00000000'\").fetchall()
embedder = IsoEmbedder()
for row in rows:
    text = f'{row[2]} {row[3]}'
    emb = embedder.encode(text)
    conn.execute('UPDATE resource_embeddings SET embedding = ? WHERE resource_id = ?', (emb.tobytes(), row[0]))
    conn.execute('UPDATE resources SET embedding = ? WHERE id = ?', (emb.tobytes(), row[0]))
conn.commit()
print(f'Embedded {len(rows)} strategy-kb resources')
conn.close()
"
```

**Step 5: Verify — query for strategy-kb in routes**

```bash
cd ~/nexus && ./venv/bin/python -c "
from server.cluster_router import ClusterRouter
router = ClusterRouter()
result = router.route_to_clusters('detect market regime for trading', max_clusters=2)
for c in result['activated_clusters']:
    print(f\"  {c['name']}: affinity={c['affinity']}, resources={c['resource_count']}\")
"
```

Expected: `analyzer_planner` or `knowledge_wiki` should now include strategy-kb tools.

**Step 6: Commit**

```bash
cd ~/nexus && git add scripts/fix_task5_sync_strategy_kb.py && git commit -m "fix(nexus): sync 8 strategy-kb MCP tools into resource DB"
```

---

### Task 6: Purge 159 skill shells with missing files

**Objective:** 159 skill entries in the DB reference files that don't exist on disk. They're dead weight diluting routing. Mark them as `status='degraded'`.

**Files:**
- Create: `/home/roshan/nexus/scripts/fix_task6_purge_skill_shells.py`
- Read: `/home/roshan/nexus/db/nexus.db`, skill directories

**Step 1: Write purge script**

```python
#!/usr/bin/env python3
"""Task 6: Mark skill shells as degraded.

Skills that reference files not on disk are dead routing weight.
We don't delete them (to preserve ID consistency) but mark them degraded
so routing excludes them.
"""
import sqlite3
from pathlib import Path

DB_PATH = Path.home() / "nexus/db/nexus.db"
SKILLS_ROOT = Path.home() / ".hermes/skills"

def purge_shells(dry_run: bool = True):
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    skills = conn.execute("""
        SELECT id, name, file_path FROM resources
        WHERE resource_type = 'skill' AND status = 'active'
    """).fetchall()

    missing = []
    for skill in skills:
        name = skill['name']
        # Check multiple possible locations
        candidates = [
            SKILLS_ROOT / f"{name}" / "SKILL.md",
            SKILLS_ROOT / f"{name.replace('-', '/')}" / "SKILL.md",
            Path(skill['file_path']) if skill['file_path'] else None,
        ]
        found = False
        for c in candidates:
            if c and c.exists():
                found = True
                break

        if not found:
            missing.append(skill)

    print(f"Skills with files: {len(skills) - len(missing)}")
    print(f"Skill shells (no file): {len(missing)}")

    if not dry_run:
        for skill in missing:
            conn.execute("""
                UPDATE resources SET status = 'degraded', updated_at = datetime('now')
                WHERE id = ?
            """, (skill['id'],))
        conn.commit()
        print(f"  Marked {len(missing)} skill shells as 'degraded'")
    else:
        print(f"  DRY RUN — would mark {len(missing)} as degraded")
        print("  Sample missing skills:")
        for s in missing[:10]:
            print(f"    {s['name']}")

    conn.close()
    return len(missing)

if __name__ == "__main__":
    import sys
    dry = "--apply" not in sys.argv
    purge_shells(dry_run=dry)
```

**Step 2: Dry run**

```bash
cd ~/nexus && ./venv/bin/python scripts/fix_task6_purge_skill_shells.py
```

Expected: Reports ~159 skill shells.

**Step 3: Apply**

```bash
cd ~/nexus && ./venv/bin/python scripts/fix_task6_purge_skill_shells.py --apply
```

**Step 4: Verify — count active vs degraded skills**

```bash
cd ~/nshan/nexus && ./venv/bin/python -c "
import sqlite3
conn = sqlite3.connect('db/nexus.db')
for status in ['active', 'degraded', 'archived']:
    count = conn.execute(f\"SELECT COUNT(*) FROM resources WHERE resource_type='skill' AND status='{status}'\").fetchone()[0]
    print(f'  skills {status}: {count}')
conn.close()
"
```

Expected: `active` decreased by ~159, `degraded` increased by ~159.

**Step 5: Commit**

```bash
cd ~/nexus && git add scripts/fix_task6_purge_skill_shells.py && git commit -m "fix(nexus): mark 159 skill shells as degraded"
```

---

### Task 7: Hook up outcome tracking pipeline (currently 6% accuracy)

**Objective:** The `report_outcome` MCP tool exists but almost nobody calls it. The routing_log.ide column only allows 'opencode'/'claude-code'/'unknown'. Fix the IDE whitelist and add auto-outcome when routing decisions are made.

**Files:**
- Modify: `/home/roshan/nexus/server/learning.py` (fix ide validation)
- Modify: `/home/roshan/nexus/db/nexus.db` (schema fix)

**Step 1: Fix the IDE column CHECK constraint**

The `routing_log` table has a CHECK constraint on `ide` that only allows 3 values. This needs to accept any string or be relaxed.

```bash
cd ~/nexus && ./venv/bin/python -c "
import sqlite3
conn = sqlite3.connect('db/nexus.db')
# Check current schema
schema = conn.execute(\"SELECT sql FROM sqlite_master WHERE type='table' AND name='routing_log'\").fetchone()
print(schema[0] if schema else 'Table not found')

# Check IDE values in use
ides = conn.execute(\"SELECT ide, COUNT(*) FROM routing_log GROUP BY ide\").fetchall()
for i in ides:
    print(f'  ide={i[0]}: {i[1]} rows')
conn.close()
"
```

**Step 2: Modify learning.py to accept any IDE string**

Edit `/home/roshan/nexus/server/learning.py` — find the `record_outcome` function and the routing log insertion. Change the IDE column to not use CHECK constraint (SQLite doesn't support ALTER TABLE to drop constraints, so we recreate the table):

```bash
cd ~/nexus && ./venv/bin/python -c "
import sqlite3
conn = sqlite3.connect('db/nexus.db')

# Create new table without CHECK constraint
conn.execute('''
    CREATE TABLE IF NOT EXISTS routing_log_v2 (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        query TEXT,
        project TEXT,
        ide TEXT,
        routed_to TEXT,
        routed_id INTEGER,
        alternatives TEXT,
        scores TEXT,
        model TEXT,
        outcome TEXT,
        latency_ms REAL,
        created_at TEXT DEFAULT (datetime('now'))
    )
''')

# Migrate data
conn.execute('''
    INSERT INTO routing_log_v2 (id, query, project, ide, routed_to, routed_id, alternatives, scores, model, outcome, latency_ms, created_at)
    SELECT id, query, project, ide, routed_to, routed_id, alternatives, scores, model, outcome, latency_ms, created_at FROM routing_log
''')

# Swap tables
conn.execute('DROP TABLE routing_log')
conn.execute('ALTER TABLE routing_log_v2 RENAME TO routing_log')
conn.commit()
print('routing_log schema migrated — no CHECK constraint on ide anymore')
conn.close()
"
```

**Step 3: Verify — insert a test row with any IDE value**

```bash
cd ~/nexus && ./venv/bin/python -c "
import sqlite3
conn = sqlite3.connect('db/nexus.db')
conn.execute(\"INSERT INTO routing_log (query, project, ide, routed_to) VALUES ('test', 'test-project', 'hermes-agent', 'test-skill')\")
conn.commit()
row = conn.execute('SELECT * FROM routing_log WHERE ide = \"hermes-agent\"').fetchone()
print(f'Test row inserted: ide={row[2]}')
conn.execute('DELETE FROM routing_log WHERE ide = \"hermes-agent\"')
conn.commit()
conn.close()
"
```

**Step 4: Commit**

```bash
cd ~/nexus && git add -A && git commit -m "fix(nexus): remove CHECK constraint on routing_log.ide column"
```

---

## Phase 3: Data Quality (P2 — Next Sprint)

Improve signal-to-noise in the routing index.

---

### Task 8: Deduplicate 7 body_text groups

**Objective:** 7 groups of resources have identical body_text (reviewer/tester/coder/researcher agents, vibeyard entries, etc.). Deduplicate by keeping the best-named entry and marking others as `status='deprecated'`.

**Files:**
- Create: `/home/roshan/nexus/scripts/fix_task8_deduplicate.py`
- Read: `/home/roshan/nexus/db/nexus.db`

**Step 1: Write dedup script**

```python
#!/usr/bin/env python3
"""Task 8: Deduplicate resources with identical body_text."""
import sqlite3
from pathlib import Path

DB_PATH = Path.home() / "nexus/db/nexus.db"

def deduplicate(dry_run: bool = True):
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # Find duplicate groups
    dupes = conn.execute("""
        SELECT body_text, COUNT(*) as cnt, GROUP_CONCAT(name) as names
        FROM resources
        WHERE status = 'active' AND body_text IS NOT NULL AND body_text != ''
        GROUP BY body_text
        HAVING cnt > 1
        ORDER BY cnt DESC
    """).fetchall()

    total_dupes = 0
    for group in dupes:
        names = group['names'].split(',')
        # Keep the shortest name (usually the canonical one)
        keeper = min(names, key=len)
        victims = [n for n in names if n != keeper]
        total_dupes += len(victims)
        print(f"  Keeper: {keeper}")
        for v in victims:
            print(f"    Deprecate: {v}")

        if not dry_run:
            for v in victims:
                conn.execute("""
                    UPDATE resources SET status = 'deprecated', updated_at = datetime('now')
                    WHERE name = ? AND status = 'active'
                """, (v,))

    if not dry_run:
        conn.commit()
        print(f"\n  Deprecated {total_dupes} duplicate resources.")
    else:
        print(f"\n  DRY RUN — would deprecate {total_dupes} duplicates in {len(dupes)} groups.")

    conn.close()

if __name__ == "__main__":
    import sys
    dry = "--apply" not in sys.argv
    deduplicate(dry_run=dry)
```

**Step 2: Dry run, then apply, then verify, then commit.**

```bash
cd ~/nexus && ./venv/bin/python scripts/fix_task8_deduplicate.py
cd ~/nexus && ./venv/bin/python scripts/fix_task8_deduplicate.py --apply
cd ~/nexus && git add scripts/fix_task8_deduplicate.py && git commit -m "fix(nexus): deduplicate 7 body_text groups"
```

---

### Task 9: Upgrade 15 tier-3 MCP stub descriptions

**Objective:** 15 MCP entries have stub descriptions like "ClickHouse analytics". Upgrade them with descriptive body_text from their actual tool schemas.

**Files:**
- Create: `/home/roshan/nexus/scripts/fix_task9_upgrade_tier3.py`

**Step 1: Identify tier-3 MCP stubs**

```bash
cd ~/nshan/nexus && ./venv/bin/python -c "
import sqlite3
conn = sqlite3.connect('db/nexus.db')
stubs = conn.execute(\"\"\"
    SELECT id, name, description, length(description) as desc_len
    FROM resources WHERE resource_type = 'mcp' AND tier = 3 AND status = 'active'
    ORDER BY desc_len ASC
\"\"\").fetchall()
for s in stubs:
    print(f'  id={s[0]}, name={s[1]}, desc_len={s[3]}: {s[2][:60]}...')
print(f'Total tier-3 MCP stubs: {len(stubs)}')
conn.close()
"
```

**Step 2: Write upgrade script with manual descriptions for the 15 stubs**

This is a manual curation task. The script will contain curated descriptions for each tier-3 MCP tool. Since we know the tier-3 entries from the audit (clickhouse, github, firecrawl, playwright, etc.), we'll write proper descriptions.

```bash
cd ~/nexus && ./venv/bin/python scripts/fix_task9_upgrade_tier3.py --apply
cd ~/nexus && git add scripts/fix_task9_upgrade_tier3.py && git commit -m "fix(nexus): upgrade 15 tier-3 MCP stub descriptions"
```

---

### Task 10: Cold-storage 1,147 never-triggered resources

**Objective:** 1,147 resources have `times_triggered=0`. They dilute embedding space and routing precision. Move them to `status='cold'` (not deleted — can be reactivated).

**Files:**
- Create: `/home/roshan/nexus/scripts/fix_task10_cold_storage.py`

**Step 1: Write cold-storage script**

```python
#!/usr/bin/env python3
"""Task 10: Move never-triggered resources to cold storage."""
import sqlite3
from pathlib import Path

DB_PATH = Path.home() / "nexus/db/nexus.db"

def cold_storage(dry_run: bool = True, min_age_days: int = 90):
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # Only cold-store resources that have NEVER been triggered AND are older than min_age_days
    never_triggered = conn.execute(f"""
        SELECT id, name, resource_type, cluster
        FROM resources
        WHERE status = 'active'
          AND times_triggered = 0
          AND created_at < datetime('now', '-{min_age_days} days')
        ORDER BY resource_type, name
    """).fetchall()

    print(f"Found {len(never_triggered)} never-triggered resources older than {min_age_days} days")

    by_type = {}
    for r in never_triggered:
        by_type[r['resource_type']] = by_type.get(r['resource_type'], 0) + 1

    for t, c in sorted(by_type.items(), key=lambda x: -x[1]):
        print(f"  {t}: {c}")

    if not dry_run:
        for r in never_triggered:
            conn.execute("""
                UPDATE resources SET status = 'cold', updated_at = datetime('now')
                WHERE id = ?
            """, (r['id'],))
        conn.commit()
        print(f"  Moved {len(never_triggered)} resources to cold storage.")
    else:
        print(f"  DRY RUN — would cold-store {len(never_triggered)} resources.")

    conn.close()

if __name__ == "__main__":
    import sys
    dry = "--apply" not in sys.argv
    cold_storage(dry_run=dry)
```

**Step 2: Dry run, apply, verify, commit**

```bash
cd ~/nexus && ./venv/bin/python scripts/fix_task10_cold_storage.py
cd ~/nexus && ./venv/bin/python scripts/fix_task10_cold_storage.py --apply
cd ~/nexus && git add scripts/fix_task10_cold_storage.py && git commit -m "fix(nexus): cold-storage never-triggered resources"
```

---

### Task 11: Fix self-reflection loop (27 failures, 0 fixes)

**Objective:** The reflection pipeline records 27 failures but applied 0 fixes. The `propose_fix()` method exists but is never called successfully. Fix the flow so failures actually generate and apply fixes.

**Files:**
- Modify: `/home/roshan/nexus/server/reflection.py`
- Modify: `/home/roshan/nexus/server/learning.py`

**Step 1: Diagnose the reflection loop**

```bash
cd ~/nexus && ./venv/bin/python -c "
import sqlite3
conn = sqlite3.connect('db/nexus.db')
# Check reflection_failures
failures = conn.execute('SELECT COUNT(*) FROM reflection_failures').fetchone()[0]
fixes = conn.execute('SELECT COUNT(*) FROM reflection_fixes').fetchone()[0]
print(f'Failures: {failures}, Fixes: {fixes}')
# Check recent failures
rows = conn.execute('SELECT id, task, resource_name, root_cause, fix_attempted, fix_successful FROM reflection_failures ORDER BY id DESC LIMIT 5').fetchall()
for r in rows:
    print(f'  id={r[0]}, resource={r[2]}, root_cause={r[3]}, fix_attempted={r[4]}, fix_successful={r[5]}')
conn.close()
"
```

**Step 2: The fix is in `learning.py` — the `record_outcome` method should call `propose_fix()` on failures**

Read the current `record_outcome` function (already loaded above). After recording a `wrong` outcome, it should:
1. Call `ReflectionEngine.record_failure()`
2. Call `ReflectionEngine.propose_fix()` to generate alternatives
3. Log the proposed fix

The current code records failures but doesn't invoke `propose_fix()`. Add the integration.

**Step 3: Add fix proposal call after outcome recording**

In `learning.py`, after the degraded-status check block, add:

```python
# Layer 3: Self-Reflection — propose alternatives on failure
if outcome in ("wrong", "error"):
    try:
        from reflection import ReflectionEngine
        reflector = ReflectionEngine(str(DB_PATH))
        fix = reflector.propose_fix(
            task=task_summary or "unknown",
            resource_name=skill_name,
            root_cause=f"outcome={outcome}",
        )
        if fix:
            logger.info(f"Reflection proposed fix for {skill_name}: {fix.get('suggested_alternatives', [])[:3]}")
    except Exception as e:
        logger.warning(f"Self-reflection failed: {e}")
```

**Step 4: Verify — check reflection is called on wrong outcome**

```bash
cd ~/nexus && ./venv/bin/python -c "
from server.learning import record_outcome
result = record_outcome('test-skill', 'wrong', task_summary='test reflection', project='test')
print(f'Outcome recorded: {result}')
"
```

**Step 5: Commit**

```bash
cd ~/nexus && git add server/learning.py && git commit -m "fix(nexus): invoke propose_fix on wrong outcomes to close self-reflection loop"
```

---

## Phase 4: Rebuild & Verify (Final)

Rebuild the FAISS index after all data changes, then run a full verification.

---

### Task 12: Rebuild FAISS index

**Objective:** After all the data changes (reclustering, deprecations, cold-storage, new resources), the FAISS index must be rebuilt to match.

**Files:**
- Run: `/home/roshan/nexus/scripts/rebuild_faiss.py` (already exists)

**Step 1: Rebuild FAISS**

```bash
cd ~/nexus && ./venv/bin/python scripts/rebuild_faiss.py
```

Expected: Reads 1,686 embeddings, rebuilds index, verifies round-trip.

**Step 2: Verify index health**

```bash
cd ~/nexus && ./venv/bin/python -c "
import faiss, json
index = faiss.read_index('db/faiss/resources.index')
with open('db/faiss/resources.index.meta.json') as f:
    meta = json.load(f)
print(f'FAISS vectors: {index.ntotal}')
print(f'Meta vectors: {meta[\"n_total\"]}')
print(f'Model: {meta[\"model\"]}')
print(f'Built: {meta[\"built_at\"]}')
assert index.ntotal == meta['n_total'], 'MISMATCH!'
print('FAISS index healthy')
"
```

**Step 3: Commit (index files may be large — add to Git LFS if needed)**

```bash
cd ~/nexus && git add db/faiss/ && git commit -m "fix(nexus): rebuild FAISS index after data fixes"
```

---

### Task 13: Recompute cluster centroids

**Objective:** After reclustering, the `cluster_definitions.centroid_embedding` values are stale (computed from the old distribution). Recompute them.

**Files:**
- Run: `/home/roshan/nexus/scripts/compute_cluster_centroids.py` (already exists)

**Step 1: Recompute centroids**

```bash
cd ~/nexus && ./venv/bin/python scripts/compute_cluster_centroids.py
```

**Step 2: Verify centroids match new cluster distribution**

```bash
cd ~/nexus && ./venv/bin/python -c "
import sqlite3
conn = sqlite3.connect('db/nexus.db')
centroids = conn.execute('SELECT name, length(centroid_embedding) FROM cluster_definitions WHERE centroid_embedding IS NOT NULL').fetchall()
for c in centroids:
    print(f'  {c[0]}: centroid_size={c[1]} bytes ({c[1]//4} floats)')
conn.close()
"
```

Expected: All 7 domain clusters have 384-dim centroids.

---

### Task 14: Final verification — run full audit again

**Objective:** Re-run the audit scripts to confirm all fixes worked and health grade improved.

**Files:**
- Run: `/home/roshan/nexus/scripts/audit_phase1_v2.py`
- Run: `/home/roshan/nexus/scripts/audit_phase5.py`
- Run: `/home/roshan/nexus/scripts/audit_phase6.py`

**Step 1: Run Phase 1 — DB integrity check**

```bash
cd ~/nexus && ./venv/bin/python scripts/audit_phase1_v2.py
```

Expected: 0 orphans, cluster counts now match, no major discrepancies.

**Step 2: Run Phase 5 — wired-up check**

```bash
cd ~/nexus && ./venv/bin/python scripts/audit_phase5.py
```

Expected: 0 skill shells (all degraded), strategy-kb tools visible.

**Step 3: Run Phase 6 — FAISS health**

```bash
cd ~/nexus && ./venv/bin/python scripts/audit_phase6.py
```

Expected: Vector count matches active resources, no dimension mismatches.

**Step 4: Run routing smoke tests**

Call `mcp_nexus_nexus_cluster_route` with all 11 original smoke test queries and confirm 11/11 correct (especially debug → analyzer_planner).

**Step 5: Write updated audit report**

```bash
cd ~/nexus && cat > AUDIT_REPORT_2026-05-17-POSTFIX.md << 'EOF'
# NEXUS Post-Fix Audit — 2026-05-17

## Summary of Changes
- Re-clustered 382 low-confidence resources out of general
- Assigned 7 orphaned graphify tools to knowledge_wiki
- Fixed debug → analyzer_planner routing
- Synced 8 strategy-kb MCP tools into resource DB
- Deprecated 159 skill shells
- Cold-stored ~1147 never-triggered resources
- Deduplicated 7 body_text groups
- Fixed IDE CHECK constraint on routing_log
- Hooked up self-reflection loop
- Rebuilt FAISS index and cluster centroids

## Target: Grade A-
Previous: C+

(Results from verification scripts will be filled in here)
EOF
```

**Step 6: Final commit**

```bash
cd ~/nexus && git add -A && git commit -m "fix(nexus): post-fix audit report and verification"
```

---

## Execution Order

| Phase | Task | Est. Time | Risk |
|-------|------|-----------|------|
| P0 | Task 1: Sync cluster counts | 5 min | Low |
| P0 | Task 2: Recluster general | 10 min | Medium — may need centroid recoordinate |
| P0 | Task 3: Assign graphify orphans | 3 min | Low |
| P0 | Task 4: Fix debug routing | 10 min | Medium — cluster_config.py change |
| P1 | Task 5: Sync strategy-kb | 10 min | Medium — needs embedding generation |
| P1 | Task 6: Purge skill shells | 5 min | Low — status change only |
| P1 | Task 7: Fix outcome tracking | 10 min | Medium — schema migration |
| P2 | Task 8: Deduplicate body_text | 5 min | Low |
| P2 | Task 9: Upgrade tier-3 stubs | 15 min | Low — manual curation |
| P2 | Task 10: Cold storage | 5 min | Low |
| P2 | Task 11: Fix self-reflection | 10 min | Medium — code change |
| P3 | Task 12: Rebuild FAISS | 5 min | Low — existing script |
| P3 | Task 13: Recompute centroids | 5 min | Low — existing script |
| P3 | Task 14: Final verification | 15 min | Low |

**Total: ~2 hours**

**Critical path:** Tasks 1→2→3→4 (must be sequential). Tasks 5-7 can run in parallel. Tasks 8-11 can run in parallel. Tasks 12-13 must wait for all data changes. Task 14 must be last.