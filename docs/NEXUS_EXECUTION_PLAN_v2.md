# NEXUS Execution Plan v2 — Gap Remediation

**Date:** 2026-07-12  
**Author:** Security/architecture audit  
**DB snapshot:** 3,254 resources, 14,475 edges, 181 communities, 6,671 routing_log, 2,875 outcome_log  
**Predecessor:** `NEXUS_ACADEMIC_GAP_ANALYSIS_v2.md` (8 gaps, 20 citations, 7-phase plan)

---

## Updated Gap Status (2026-07-12 Audit)

The original 8 gaps were assessed against the live DB. Three are now fixed, three are partially fixed, two remain open. The execution plan below targets only the remaining work.

| # | Gap | Original Severity | Current Status | Evidence |
|---|-----|-------------------|----------------|----------|
| G1 | KG edge collapse | CRITICAL | **FIXED** | 14,475 edges (was 5), 181 communities (was 0), null_cluster=0 |
| G2 | Never-triggered waste | CRITICAL | **OPEN (worse)** | 2,076/3,254 = 63.8% (was 58.8%) |
| G3 | EFC bridge failure | CRITICAL | **FIXED** | 2,875/2,875 = 100% EFC coverage; 5 EFC columns populated |
| G4 | Outcome coverage drift | HIGH | **FIXED** | 6,657/6,671 = 99.8%; auto-close disabled (correct) |
| G5 | Multi-router fragmentation | HIGH | **PARTIAL** | HTTP daemon has `USE_UNIFIED_ROUTER=2` in supervisord.conf; MCP process env does NOT set it |
| G6 | Chunk-resource link desert | HIGH | **PARTIAL** | 60,945/91,286 = 66.8% linked (was 0.27%); 30,341 unlinked |
| G7 | Reflexion not wired | MEDIUM | **PARTIAL** | 322 failures recorded, 20 fix-attempted, 8/2,486 fixes successful (0.3%) |
| G8 | Dashboard dead | MEDIUM | **PARTIAL** | :8080 health OK; :8765 dashboard not responding |

**Remaining work: 5 items (G2, G5, G6, G7, G8) + 1 cleanup (G1 zero-edge)**

---

## Phase 1 — Port Reflexion to v5, Then Router Cutover + Process Supervision (G5, G7, G8)

**Goal:** Port the reflexion consultation from legacy `router.py` into `v5.py`, then make v5 the production router on ALL processes (MCP + HTTP daemon), start supervisord, bring dashboard online.

**Rationale:** The HTTP daemon already runs with `USE_UNIFIED_ROUTER=2` via supervisord.conf, but the live MCP process (`nexus-mcp-enhanced.py`) has no `USE_UNIFIED_ROUTER` env var set — it falls back to the legacy v3/v4 router. This is the root cause of G2 (never-triggered) because the two-tier cold path only activates under the unified router.

**CRITICAL DEPENDENCY:** The legacy `router.py:452-465` consults `reflection_fixes` and `reflection_failures` during scoring — it builds a `fixes_map` (resource_id → alternative names with success counts) and a `recent_failures` set (resource_name → unfixed failures). The v5 router (`router_v2/v5.py`) has ZERO references to `reflection_fixes`. Cutting over to v5 without porting this logic would DISCONNECT the only reflexion consumer. Therefore Step 1.0 (port reflexion to v5) must happen BEFORE Step 1.2 (env flag flip).

### Step 1.0 — Port reflexion consultation into v5 router

The legacy `router.py:440-475` does two things with reflexion data:
1. Builds `fixes_map`: maps `resource_id` → list of `(alternative_name, times_successful)` by parsing `fix_description` strings that match pattern `"Use X instead"`.
2. Builds `recent_failures`: set of `resource_name.lower()` for failures where `fix_successful IS NULL OR fix_successful = 0`.

These are used to downweight failed resources and upweight known-successful alternatives during scoring.

**Action:** Add a `_load_reflexion_context(conn)` helper in `server/router_v2/v5.py` that replicates this logic, and apply the adjustments in the L3 scoring stage (after base HGMF score, before final ranking).

```python
# In server/router_v2/v5.py, add after the L3 scoring stage:

def _load_reflexion_context(conn):
    """Port of router.py:452-465 reflexion consultation for v5."""
    fixes_map = {}
    recent_failures = set()
    try:
        for f in conn.execute("""
            SELECT resource_id, fix_description, times_successful
            FROM reflection_fixes WHERE times_applied > 0
        """):
            rid, desc, succ = f
            desc = desc or ""
            if desc.startswith("Use ") and desc.endswith(" instead"):
                alt_name = desc[4:-8]
                fixes_map.setdefault(rid, []).append((alt_name, succ or 0))
        for fr in conn.execute("""
            SELECT resource_name FROM reflection_failures
            WHERE fix_successful IS NULL OR fix_successful = 0
        """):
            if fr[0]:
                recent_failures.add(fr[0].lower())
    except Exception:
        pass
    return fixes_map, recent_failures

# In the scoring loop, after computing base_score for each candidate:
# if resource.name.lower() in recent_failures: base_score *= 0.7
# if resource.id in fixes_map: upweight alternatives by 1 + 0.1 * times_successful
```

**Code path:** `server/router.py:446-472` (legacy source); `server/router_v2/v5.py:258-270` (target — `_v5_rerank` scoring loop, after `finals` computed and before `scored.sort()`); `server/learning.py:1232` (`_trigger_reflection` function that populates the tables).

**PITFALL:** The `fix_description` parsing is fragile — it relies on exact string format `"Use X instead"`. If alternative skills are proposed with different phrasing, they won't be picked up. Consider also storing `alternative_resource_id` as a proper column in `reflection_fixes` instead of parsing free text.

### Step 1.1 — Verify which router the MCP is using

```bash
# Check the running MCP process environment
cat /proc/$(pgrep -f nexus-mcp-enhanced.py)/environ 2>/dev/null | tr '\0' '\n' | grep USE_UNIFIED_ROUTER
```

If empty, the MCP is on legacy routing. This is the primary blocker.

### Step 1.2 — Set USE_UNIFIED_ROUTER in MCP environment

**CRITICAL:** The MCP server (`nexus-mcp-enhanced.py`) is NOT started by supervisord. The supervisord.conf explicitly sets `autostart=false` for `nexus-mcp-server` because the IDE (Claude Code / OpenCode) owns the stdio pipe via `.mcp.json`. Therefore, supervisord restart will NOT affect the MCP process.

The env var must be set in the IDE's MCP configuration, not supervisord:

**Option A (Claude Code `.mcp.json`):** Add `env` to the nexus MCP server entry:
```json
{
  "mcpServers": {
    "nexus": {
      "command": "/home/roshan/nexus/venv/bin/python3",
      "args": ["/home/roshan/nexus/server/nexus-mcp-enhanced.py"],
      "env": {
        "USE_UNIFIED_ROUTER": "2",
        "NEXUS_DB_PATH": "/home/roshan/nexus/db/nexus.db"
      }
    }
  }
}
```
Then restart the MCP connection in the IDE (reload window or `/mcp` restart).

Value `2` = canary mode (adds information-gain bonus to cold tier, per `v5.py:275`). Value `1` = v5 without canary bonus.

**Option B (shell export, for manual testing):**
```bash
export USE_UNIFIED_ROUTER=2
export NEXUS_DB_PATH=/home/roshan/nexus/db/nexus.db
venv/bin/python3 server/nexus-mcp-enhanced.py
```

**PITFALL:** Changing `.mcp.json` requires an IDE reload. Killing the live MCP from inside a session terminates this session. This MUST be done out-of-session.

**Code path:** `server/router_v2/v5.py:275` — `is_canary = os.environ.get("USE_UNIFIED_ROUTER") == "2"`; `server/nexus-mcp-enhanced.py:89-94` — Phase 7 cutover comment block; `supervisord.conf` — `[program:nexus-mcp-server]` with `autostart=false` (IDE owns stdio).

### Step 1.3 — Start supervisord + dashboard

```bash
# Ensure run/ and logs/ dirs exist
mkdir -p /home/roshan/nexus/run /home/roshan/nexus/logs

# Start supervisord (manages HTTP daemon, MCP, dashboard)
venv/bin/supervisord -c /home/roshan/nexus/supervisord.conf
venv/bin/supervisorctl status
```

The supervisord.conf defines:
- `nexus-http-daemon` — FastAPI bridge on :8080 (autostart=false, primary = systemd unit)
- Dashboard program — :8765

### Step 1.4 — Verify dashboard

```bash
curl -s --connect-timeout 3 http://127.0.0.1:8765/ | head -5
curl -s --connect-timeout 3 http://127.0.0.1:9001/ | head -5  # supervisor web UI
```

### Verification Gate P1
- [ ] `_load_reflexion_context()` function exists in `server/router_v2/v5.py` and is called during scoring
- [ ] `route_ab_compare` on 20 queries shows v5 with reflexion matches or beats legacy router on failure cases
- [ ] `cat /proc/$(pgrep -f nexus-mcp-enhanced)/environ | grep USE_UNIFIED_ROUTER` returns `2`
- [ ] `curl http://127.0.0.1:8765/` returns HTML (not empty)
- [ ] `venv/bin/supervisorctl status` shows all programs RUNNING
- [ ] Router v5 handles a test query: `venv/bin/python3 -c "import os; os.environ['USE_UNIFIED_ROUTER']='2'; import sys; sys.path.insert(0,'server'); from router_v2 import unified_route; print(unified_route('test query'))"`

**Rollback:** `export USE_UNIFIED_ROUTER=0` + restart MCP. Legacy router resumes.

**Risk:** MEDIUM — cutover changes live routing for all IDE sessions. The v5 router has passed A/B suite (`router_ab_suite` tool). Canary mode (`=2`) adds information-gain bonus which may surface unfamiliar resources — this is intentional exploration.

---

## Phase 2 — Cold-Start Warming (G2)

**Goal:** Reduce never-triggered from 63.8% to <50% within 7 days of router cutover.

**Rationale:** 2,076/3,254 resources have `times_triggered=0`. The unified router's two-tier split (ToolkenGPT, arXiv:2310.03054) reserves 30% of result sets for cold resources ranked by UCB1 + semantic similarity. But this only fires when `USE_UNIFIED_ROUTER` is set AND queries flow through the unified path. Additionally, 1,420 resources have NO embeddings — they cannot be ranked by semantic similarity at all.

### Step 2.1 — Generate missing embeddings

1,420 resources have no embedding in `resource_embeddings`. Without embeddings, the v5 router cannot compute semantic similarity scores.

**Verified model:** `sentence-transformers/all-MiniLM-L6-v2` (384-dim). The codebase uses `IsoEmbedder` (`server/iso_embedder.py:24`) wrapped by `LazyEmbedder` (`server/router_v2/lazy_embedder.py:32`). Always use the existing singleton — do NOT instantiate a separate `SentenceTransformer` as it would load a second ~300MB model instance.

```bash
# Check which resources lack embeddings
venv/bin/python3 -c "
import sqlite3
conn = sqlite3.connect('db/nexus.db')
rows = conn.execute('''
    SELECT r.id, r.name, r.type FROM resources r
    LEFT JOIN resource_embeddings e ON r.id = e.resource_id
    WHERE e.embedding IS NULL
    LIMIT 10
''').fetchall()
for r in rows: print(r)
print(f'Total missing: {conn.execute(\"SELECT COUNT(*) FROM resources r LEFT JOIN resource_embeddings e ON r.id = e.resource_id WHERE e.embedding IS NULL\").fetchone()[0]}')
conn.close()
"
```

Action: Generate embeddings using the existing IsoEmbedder singleton:

```bash
venv/bin/python3 -c "
import sqlite3, sys, os
sys.path.insert(0, 'server')
os.environ['NEXUS_DB_PATH'] = 'db/nexus.db'
from router_v2.lazy_embedder import LazyEmbedder
emb = LazyEmbedder()
conn = sqlite3.connect('db/nexus.db')
rows = conn.execute('''
    SELECT r.id, r.name, COALESCE(r.description, r.name) as text
    FROM resources r
    LEFT JOIN resource_embeddings e ON r.id = e.resource_id
    WHERE e.embedding IS NULL
''').fetchall()
print(f'Embedding {len(rows)} resources with all-MiniLM-L6-v2...')
for i, (rid, name, text) in enumerate(rows):
    vec = emb.encode_single(text)  # returns L2-normalized 384-dim float32 np.ndarray
    conn.execute('INSERT OR REPLACE INTO resource_embeddings (resource_id, model_name, embedding, created_at) VALUES (?, ?, ?, datetime(\"now\"))',
                 (rid, 'all-MiniLM-L6-v2', vec.tobytes()))
    if i % 100 == 0:
        print(f'  {i}/{len(rows)}')
        conn.commit()
conn.commit()
conn.close()
print('Done')
"
```

**PITFALL:** The `LazyEmbedder` first call takes ~12-15s to load the model (cold start). Subsequent calls are fast. Do NOT parallelize with multiprocessing — `SentenceTransformer` is thread-safe for inference but the singleton must be loaded once per process.

### Step 2.2 — Run cold_warm.py with --commit

```bash
# Dry run first — preview which cold resources get surfaced
venv/bin/python3 scripts/cold_warm.py --sample 200 --top-k 10

# Commit — writes routing_log rows with outcome='routed' for surfaced cold resources
venv/bin/python3 scripts/cold_warm.py --commit --top-k 10
```

**Code path:** `scripts/cold_warm.py:16-20` — `--commit` writes `routing_log` with `outcome='routed'` (intermediate state, not fake feedback). Real outcome feedback must come from `record_outcome()` when the resource is actually used.

### Step 2.3 — Organic warming (passive)

Once `USE_UNIFIED_ROUTER=2` is live on the MCP, every real query from IDE sessions will surface 30% cold-tier resources via the two-tier split. No further manual action needed — the metric improves organically as queries flow.

### Verification Gate P2
- [ ] `SELECT COUNT(*) FROM resource_embeddings WHERE embedding IS NOT NULL` = 3,254 (100%)
- [ ] `cold_warm.py --sample 200` surfaces ≥100 unique cold resources in the cold tier
- [ ] After 7 days: `SELECT COUNT(*) FROM resources WHERE times_triggered=0` < 1,627 (<50%)
- [ ] No Thompson belief inflation: `SELECT AVG(alpha), AVG(beta) FROM thompson_beliefs` should not spike (cold_warm --commit writes outcome='routed', not 'correct')

**Rollback:** Cold warming is read-only + `outcome='routed'` rows. To undo: `DELETE FROM routing_log WHERE outcome='routed' AND created_at > <cutover_date>`.

**Risk:** LOW — cold_warm.py is explicitly designed as read-only exploration. The `--commit` flag only writes intermediate `routed` state, not fake outcomes. The auto-close functions that previously inflated beliefs (`_auto_close_routed`, `_auto_close_stale_null`) are disabled in `learning.py:722-746`.

---

## Phase 3 — Chunk Re-Linking (G6)

**Goal:** Link remaining 30,341 unlinked ingestion chunks to their parent resources (66.8% → >90%).

**Rationale:** 91,286 total chunks, 60,945 linked, 30,341 unlinked. Unlinked chunks are invisible to the vector search pipeline — they have embeddings but no `resource_id` join, so the router cannot trace a chunk hit back to a resource.

### Step 3.1 — Profile unlinked chunks

```bash
venv/bin/python3 -c "
import sqlite3
conn = sqlite3.connect('db/nexus.db')
# What source files do unlinked chunks come from?
rows = conn.execute('''
    SELECT source_file_path, COUNT(*) as cnt 
    FROM ingestion_chunks 
    WHERE resource_id IS NULL 
    GROUP BY source_file_path 
    ORDER BY cnt DESC 
    LIMIT 20
''').fetchall()
for r in rows: print(f'  {r[0]}: {r[1]}')
# How many have a source_id?
with_source = conn.execute('SELECT COUNT(*) FROM ingestion_chunks WHERE resource_id IS NULL AND source_id IS NOT NULL').fetchone()[0]
print(f'Unlinked with source_id: {with_source}')
print(f'Unlinked without source_id: {30341 - with_source}')
conn.close()
"
```

### Step 3.2 — Link chunks via source_id → resource_id mapping

If `source_id` exists on unlinked chunks, the fix is a SQL JOIN:

```sql
-- Preview
SELECT ic.id, ic.source_id, r.id as resource_id
FROM ingestion_chunks ic
JOIN resources r ON ic.source_id = r.id  -- or r.name = ic.source_id, depends on schema
WHERE ic.resource_id IS NULL
LIMIT 10;

-- Apply
UPDATE ingestion_chunks
SET resource_id = (
    SELECT r.id FROM resources r WHERE r.id = ingestion_chunks.source_id
    -- OR: WHERE r.name = ingestion_chunks.source_id
)
WHERE resource_id IS NULL
AND EXISTS (SELECT 1 FROM resources r WHERE r.id = ingestion_chunks.source_id);
```

### Step 3.3 — Re-ingest truly orphaned chunks

Chunks with no `source_id` match need re-ingestion. Check the ingestion pipeline:

```bash
ls scripts/ingest*.py scripts/reingest*.py 2>/dev/null
```

For truly orphaned chunks (skill files from `~/.claude/skills/` or `~/.hermes/skills/`), re-run the ingestion script with the resource mapping enabled.

### Verification Gate P3
- [ ] `SELECT COUNT(*) FROM ingestion_chunks WHERE resource_id IS NULL` < 9,129 (<10%)
- [ ] Vector search returns resource_id for >90% of chunk hits
- [ ] No duplicate resources created (check: `SELECT name, COUNT(*) FROM resources GROUP BY name HAVING COUNT(*) > 1`)

**Rollback:** `UPDATE ingestion_chunks SET resource_id = NULL WHERE id IN (<affected_ids>)`.

**Risk:** LOW — linking is a metadata update, not a content change. Worst case: wrong resource_id mapping, which is detectable by checking a sample.

---

## Phase 4 — Reflexion Fix-Success Repair (G7)

**Goal:** Increase fix-success rate from 0.3% (8/2,486) to >15%, and process 302 unfixed failures.

**Rationale:** The reflexion system IS wired — `_trigger_reflection()` in `learning.py:587-591` fires on `outcome='wrong'` or high-error partials. It records failures in `reflection_failures` and proposes alternatives in `reflection_fixes`. But only 20/322 failures have a fix attempted, and only 8/2,486 proposed fixes were marked successful. The system records failures but rarely acts on the proposed fixes.

**DEPENDENCY:** Phase 1 Step 1.0 ports the reflexion consultation (`_load_reflexion_context()`) into v5. This phase operates on the DATA side — processing unfixed failures and ensuring the proposed fixes are actionable. Without P1 Step 1.0, even processed fixes would not be consulted by the router.

### Step 4.1 — Diagnose why fixes aren't applied

```bash
venv/bin/python3 -c "
import sqlite3
conn = sqlite3.connect('db/nexus.db')
# Check: are fixes being proposed but not applied?
total_fixes = conn.execute('SELECT COUNT(*) FROM reflection_fixes').fetchone()[0]
applied = conn.execute('SELECT COUNT(*) FROM reflection_fixes WHERE times_applied > 0').fetchone()[0]
successful = conn.execute('SELECT COUNT(*) FROM reflection_fixes WHERE times_successful > 0').fetchone()[0]
print(f'Fixes: {total_fixes} total, {applied} applied, {successful} successful')

# Check: what happens after a fix is proposed?
# Is there a retry mechanism that uses the proposed fix?
sample = conn.execute('''
    SELECT rf.fix_description, rf.times_applied, rf.times_successful,
           rf.resource_id, r.name
    FROM reflection_fixes rf
    LEFT JOIN resources r ON rf.resource_id = r.id
    WHERE rf.times_applied > 0
    LIMIT 10
''').fetchall()
for s in sample: print(f'  {s[4]}: applied={s[1]}, success={s[2]}, fix={s[0]}')

# The gap: fixes are PROPOSED but who APPLIES them?
conn.close()
"
```

### Step 4.2 — Verify v5 reflexion integration (from P1 Step 1.0)

After P1 Step 1.0, v5 should consult `reflection_fixes` via `_load_reflexion_context()`. Verify:

```bash
# Confirm the function exists and is called
grep -n "_load_reflexion_context\|recent_failures\|fixes_map" server/router_v2/v5.py | head -10

# Run a test query through v5 with a known-failed resource
venv/bin/python3 -c "
import sqlite3, sys, os
sys.path.insert(0, 'server')
os.environ['NEXUS_DB_PATH'] = 'db/nexus.db'
os.environ['USE_UNIFIED_ROUTER'] = '2'
from router_v2.v5 import unified_route
# Pick a resource that has a reflection_failure
conn = sqlite3.connect('db/nexus.db')
failed = conn.execute('SELECT resource_name FROM reflection_failures LIMIT 1').fetchone()
if failed:
    result = unified_route(failed[0])
    print(f'Query for failed resource: {failed[0]}')
    print(f'Result: {result}')
conn.close()
"
```

If v5 does NOT yet have the reflexion integration, P1 Step 1.0 must be completed first.

### Step 4.3 — Process the 302 unfixed failures

```bash
# Run find_alternatives for all unfixed failures
venv/bin/python3 -c "
import sqlite3, sys, os
sys.path.insert(0, 'server')
os.environ['NEXUS_DB_PATH'] = 'db/nexus.db'
from learning import _trigger_reflection
conn = sqlite3.connect('db/nexus.db')
unfixed = conn.execute('''
    SELECT id, task, resource_name, resource_id, root_cause
    FROM reflection_failures WHERE fix_successful IS NULL
    LIMIT 50
''').fetchall()
print(f'Processing {len(unfixed)} unfixed failures...')
for f in unfixed:
    result = _trigger_reflection(conn, f[2], f[1], None)
    print(f'  {f[2]}: {result.get(\"action\", \"none\")} — {len(result.get(\"alternatives\", []))} alternatives')
conn.close()
"
```

### Step 4.4 — Add `alternative_resource_id` column to `reflection_fixes`

The current `fix_description` parsing in both legacy router and the P1 Step 1.0 port relies on exact string format `"Use X instead"`. This is fragile. Add a proper column:

```sql
ALTER TABLE reflection_fixes ADD COLUMN alternative_resource_id INTEGER REFERENCES resources(id);
```

Then update `_trigger_reflection()` in `learning.py:1232` to populate `alternative_resource_id` alongside the `fix_description` text, and update `_load_reflexion_context()` in v5 to use the column instead of parsing text.

### Verification Gate P4
- [ ] `SELECT COUNT(*) FROM reflection_failures WHERE fix_successful IS NULL` < 50
- [ ] `SELECT COUNT(*) FROM reflection_fixes WHERE times_successful > 0` > 100 (>4% of 2,486)
- [ ] `_load_reflexion_context()` in v5.py is called during scoring and returns non-empty `fixes_map` for test queries
- [ ] `alternative_resource_id` column exists in `reflection_fixes` schema

**Rollback:** Reflexion is additive — disabling `REFLECTION_ENABLED` reverts to no-reflection routing. The `reflection_failures` and `reflection_fixes` tables are append-only diagnostics.

**Risk:** MEDIUM — wiring fix application into the router changes live scoring. Test with A/B: `route_ab_compare` tool on 20 queries before/after.

---

## Phase 5 — Zero-Edge Resource Cleanup (G1 residual)

**Goal:** Reduce 411 zero-edge resources to <50.

**Rationale:** The KG densify ran successfully (14,475 edges, 181 communities) but 411 resources still have no edges. All 411 are also never-triggered — they have no usage data to generate `co_triggered` edges, and without embeddings they can't get `similar_to` edges.

### Step 5.1 — Generate embeddings for zero-edge resources

These 411 resources overlap with the 1,420 missing embeddings from Phase 2. After Step 2.1 generates embeddings, re-run densify:

```bash
venv/bin/python3 -c "
import sqlite3, sys, os
sys.path.insert(0, 'server')
os.environ['NEXUS_DB_PATH'] = 'db/nexus.db'
from kg_service import run_densify
result = run_densify(threshold=0.65, write_communities=True)
print(f'Densify result: {result}')
"
```

**Code path:** `server/kg_service.py:160-193` — `run_densify()` runs passes 7b–7g: semantic edges (`similar_to`), orphan assignment, member edges, Louvain communities, co-trigger promotion, pruning, community write.

### Step 5.2 — Verify zero-edge reduction

```bash
venv/bin/python3 -c "
import sqlite3
conn = sqlite3.connect('db/nexus.db')
zero = conn.execute('''
    SELECT COUNT(*) FROM resources r
    WHERE NOT EXISTS (
        SELECT 1 FROM resource_edges e WHERE e.source_id=r.id OR e.target_id=r.id
    )
''').fetchone()[0]
print(f'Zero-edge resources: {zero}')
conn.close()
"
```

### Verification Gate P5
- [ ] Zero-edge resources < 50
- [ ] Communities > 200
- [ ] `kg_status` tool returns `zero_edge_pass=True`

**Rollback:** `restore_backup_edges()` in `kg_service.py:196-229` can restore the pre-densify state.

**Risk:** LOW — densify is idempotent and has dry-run mode. The `threshold=0.65` is the default; lowering it creates more edges (more false positives), raising it creates fewer (misses true similarities).

---

## Phase 6 — Dashboard Activation (G8)

**Goal:** Dashboard on :8765 serving real-time ops metrics.

**Rationale:** The HTTP daemon on :8080 is alive but the dashboard on :8765 is not responding. supervisord.conf has the full service definitions but supervisord may not be running.

### Step 6.1 — Start supervisord (if not done in Phase 1)

```bash
mkdir -p /home/roshan/nexus/run /home/roshan/nexus/logs
venv/bin/supervisord -c /home/roshan/nexus/supervisord.conf
venv/bin/supervisorctl status
```

### Step 6.2 — Verify dashboard port

```bash
curl -s http://127.0.0.1:8765/ | head -10
```

If the dashboard program is not in supervisord.conf, check for a standalone dashboard script:

```bash
ls scripts/dashboard*.py server/dashboard*.py 2>/dev/null
grep -rn "8765" supervisord.conf server/ scripts/ | head -10
```

### Step 6.3 — Check ops_metrics integration

The dashboard should surface FTSR, MRC, TLT, CDR metrics from `ops_metrics.py`:

```bash
grep -rn "8765\|dashboard" server/ops_metrics.py 2>/dev/null | head -5
```

### Verification Gate P6
- [ ] `curl http://127.0.0.1:8765/` returns HTML
- [ ] Dashboard shows: resource count, edge count, outcome coverage, router flag, active sessions
- [ ] `supervisorctl status` shows all programs RUNNING

**Rollback:** `supervisorctl stop all` + `supervisorctl shutdown`.

**Risk:** LOW — dashboard is read-only. No routing or learning impact.

---

## Execution Order + Timeline

| Phase | Target Gaps | Est. Time | Prerequisite | Can Parallel? |
|-------|------------|-----------|--------------|---------------|
| P1 | G5, G7, G8 | 3h (out-of-session) | None | No (blocks P2) |
| P2 | G2 | 2h + 7d organic | P1 (router cutover) | No (needs v5 live) |
| P3 | G6 | 2h | None | Yes (with P2) |
| P4 | G7 | 4h | P1 Step 1.0 (reflexion ported to v5) | Yes (with P2) |
| P5 | G1 residual | 1h | P2 Step 2.1 (embeddings) | Yes (after 2.1) |
| P6 | G8 | 30min | P1 (supervisord) | Yes (with P1) |

**Critical path:** P1 → P2 → (organic warming 7d)  
**Parallel track:** P3, P4, P5, P6 can run alongside P2's organic warming phase.

**Total active engineering time:** ~12h (P1 increased by 2h for reflexion port)  
**Total wall-clock time:** ~8 days (dominated by organic cold-start warming)

---

## Cross-Phase Verification

After all phases complete, run the full health check:

```bash
# 1. KG health
venv/bin/python3 -c "
import sys; sys.path.insert(0, 'server')
from kg_service import get_kg_status
print(get_kg_status())
"

# 2. Learning pipeline health
venv/bin/python3 -c "
import sys; sys.path.insert(0, 'server')
from learning import get_learning_stats
print(get_learning_stats())
"

# 3. Router A/B suite (v4 vs v5 agreement)
# Use the MCP tool: router_ab_suite

# 4. Process health
venv/bin/supervisorctl status
curl -s http://127.0.0.1:8080/health | python3 -m json.tool
curl -s http://127.0.0.1:8765/ | head -5

# 5. DB health
venv/bin/python3 -c "
import sqlite3
conn = sqlite3.connect('db/nexus.db')
checks = {
    'never_triggered_pct': conn.execute('SELECT COUNT(*) FROM resources WHERE times_triggered=0').fetchone()[0] / 3254 * 100,
    'outcome_coverage_pct': conn.execute('SELECT COUNT(*) FROM routing_log WHERE outcome IS NOT NULL AND outcome != \"routed\"').fetchone()[0] / conn.execute('SELECT COUNT(*) FROM routing_log').fetchone()[0] * 100,
    'efc_coverage_pct': conn.execute('SELECT COUNT(*) FROM outcome_log WHERE efc_score IS NOT NULL').fetchone()[0] / conn.execute('SELECT COUNT(*) FROM outcome_log').fetchone()[0] * 100,
    'chunk_link_pct': conn.execute('SELECT COUNT(*) FROM ingestion_chunks WHERE resource_id IS NOT NULL').fetchone()[0] / conn.execute('SELECT COUNT(*) FROM ingestion_chunks').fetchone()[0] * 100,
    'zero_edge': conn.execute('SELECT COUNT(*) FROM resources r WHERE NOT EXISTS (SELECT 1 FROM resource_edges e WHERE e.source_id=r.id OR e.target_id=r.id)').fetchone()[0],
    'communities': conn.execute('SELECT COUNT(*) FROM kg_communities').fetchone()[0],
    'embeddings': conn.execute('SELECT COUNT(*) FROM resource_embeddings WHERE embedding IS NOT NULL').fetchone()[0],
    'reflection_fix_success_pct': conn.execute('SELECT COUNT(*) FROM reflection_fixes WHERE times_successful > 0').fetchone()[0] / max(conn.execute('SELECT COUNT(*) FROM reflection_fixes').fetchone()[0], 1) * 100,
}
for k, v in checks.items():
    print(f'{k}: {v:.1f}')
conn.close()
"
```

### Target End State

| Metric | Current | Target |
|--------|---------|--------|
| Never-triggered % | 63.8% | <50% (7d post-cutover) |
| Outcome coverage % | 99.8% | >95% (maintain) |
| EFC coverage % | 100% | 100% (maintain) |
| Chunk link % | 66.8% | >90% |
| Zero-edge resources | 411 | <50 |
| Communities | 181 | >200 |
| Embeddings | 1,834/3,254 (56%) | 3,254/3,254 (100%) |
| Reflection fix-success % | 0.3% | >15% |
| Dashboard :8765 | dead | alive |
| Router flag | not set on MCP | `USE_UNIFIED_ROUTER=2` on all processes |

---

## Academic References

| Gap | Paper | Relevance |
|-----|-------|-----------|
| G2 | ToolkenGPT (arXiv:2310.03054) | Two-tier cold/warm split for tool retrieval |
| G2 | Bandit Tool Selection (arXiv:2406.09121) | UCB1 exploration bonus for never-triggered resources |
| G3 | EFC framework (arXiv:2605.29682) | Expected Free Value for information-theoretic outcome scoring |
| G4 | Reflexion (arXiv:2303.11366) | Self-reflection on failure for iterative improvement |
| G5 | HGMF (arXiv:2508.07602) | Hierarchical generative mixture model for fusion |
| G5 | RRF (arXiv:2210.11934) | Reciprocal Rank Fusion for multi-signal score combination |
| G7 | CRITIC (arXiv:2407.06567) | Self-correction with tool-interactive learning |
| G1 | Graph densification (arXiv:2511.11017) | Semantic edge augmentation for sparse KGs |

---

## Guardrails

1. **Never kill the live MCP from inside a session** — the MCP is owned by the IDE via `.mcp.json` (supervisord `autostart=false`), not supervisord. Env var changes require `.mcp.json` edit + IDE reload, out-of-session only
2. **Never write fake outcomes** — `_auto_close_routed` and `_auto_close_stale_null` are disabled for this reason (learning.py:722-746)
3. **Always dry-run densify** before commit — `run_densify(dry_run=True)` first
4. **Always run `route_ab_compare`** before and after router changes — 20-query A/B suite
5. **Cold warming is read-only** — `cold_warm.py` does not write outcomes, only `routed` state
6. **Verify embedding model name** before generating — mismatched models break the router