# NEXUS Audit Remediation — Execution Plan

**Based on**: `nexus_multidimensional_audit.md` (2026-07-01)
**Goal**: Wire orphaned fixes, activate dormant systems, clean dead resources
**Total estimated effort**: 8-12 hours

---

## Dependency Graph

```
Level 0 (parallel — independent)
  ├── N1: Delete orphaned SSE reconnect         [0.1h]
  ├── N2: Add NEXUS env vars to supervisor      [0.5h]
  └── N3: Migrate legacy 'partial' → 'routed'   [0.5h]

Level 1 (after N2 — env config needed)
  ├── N4: Purge 301 inactive MCP resources      [0.5h]
  └── N5: VACUUM DB + prune old embeddings       [0.5h]

Level 2 (code-level wiring)
  ├── N6: Wire harness_research to route_task()  [3-4h]
  └── N7: Wire rate_limited_proxy to academics   [1-2h]

Level 3 (behavioral — after N6 for context)
  ├── N8: Add cluster affinity decay factor      [2h]
  └── N9: Cold-start campaign for cold resources [2-3h]

Level 4 (verification)
  └── N10: Restart processes + smoke test        [0.5h]

Level 5 (ongoing)
  └── N11: Track routing accuracy 100 queries    [ongoing]
```

---

## N1: Delete Orphaned SSE Reconnect

**File**: `server/connectivity/sse_reconnect.py`
**Justification**: NEXUS has no SSE transport. This file solves a non-existent problem. Delete it.
**Verification**: `git rm server/connectivity/sse_reconnect.py`, check no imports reference it.

```bash
git rm /home/roshan/nexus/server/connectivity/sse_reconnect.py
grep -r "sse_reconnect" /home/roshan/nexus/server/  # should return nothing
```

---

## N2: Add NEXUS Env Vars to Supervisor Config

**File**: `/home/roshan/nexus/supervisord.conf`
**Change**: Add `NEXUS_CLUSTER_ROUTING`, `NEXUS_REFLECTION`, `NEXUS_DB_PATH` to the `environment=` block for programs that need them.

**Rationale**: These env vars are in `.bashrc` but NOT in supervisor, so supervisor-spawned processes (HTTP daemon, MCP server, context harness) don't get them.

### Changes to `[program:nexus-mcp-server]` (line 69):
```
environment=PYTHONPATH="/home/roshan/nexus",NEXUS_CLUSTER_ROUTING="true",NEXUS_REFLECTION="true",NEXUS_DB_PATH="/home/roshan/nexus/db/nexus.db"
```

### Changes to `[program:nexus-http-daemon]`:
Add after `stopasgroup=true`:
```
environment=NEXUS_CLUSTER_ROUTING="true",NEXUS_DB_PATH="/home/roshan/nexus/db/nexus.db"
```

### Changes to `[program:nexus-context-harness]` (line 99):
```
environment=PYTHONPATH="/home/roshan/nexus",NEXUS_DB_PATH="/home/roshan/nexus/db/nexus.db"
```

### Verification:
```bash
grep "NEXUS_" /home/roshan/nexus/supervisord.conf
```

---

## N3: Migrate Legacy 'partial' → 'routed'

**DB**: `/home/roshan/nexus/db/nexus.db`
**Change**: UPDATE routing_log SET outcome='routed' WHERE outcome='partial'

**Rationale**: After the feedback collapse fix (`_evaluate_outcome` now returns `'routed'`), 1,915 legacy `'partial'` entries remain. Both are neutral in Thompson Sampling (no alpha/beta update), but `'routed'` is the honest label. Migration cleans the history so accuracy percentages reflect real signal.

```sql
UPDATE routing_log SET outcome = 'routed' WHERE outcome = 'partial';
```

**Verification**: Check that `partial` count drops to 0 and `routed` count rises by the same amount.

---

## N4: Purge 301 Inactive MCP Resources

**DB**: `/home/roshan/nexus/db/nexus.db`
**Change**: Archive all MCP resources where status is not 'active'.

**Rationale**: 327 MCP resources exist, only 26 are active. 301 are from decommissioned MCP servers (old ruflo instances, disconnected servers). They add noise to routing without contributing signal.

```sql
UPDATE resources SET status = 'archived', updated_at = datetime('now')
WHERE resource_type = 'mcp' AND status != 'active';
```

**Verification**: Confirm only active MCP resources remain:
```sql
SELECT status, COUNT(*) FROM resources WHERE resource_type = 'mcp' GROUP BY status;
```

---

## N5: VACUUM DB + Prune Old Embeddings

**DB**: `/home/roshan/nexus/db/nexus.db`
**Change**: VACUUM after N3 + N4. Optionally prune chunk_embeddings > 90 days old.

```sql
-- Prune embeddings cached > 90 days ago (if cache_created_at exists)
DELETE FROM chunk_embed_cache WHERE created_at < datetime('now', '-90 days');
DELETE FROM chunk_embeddings WHERE created_at < datetime('now', '-90 days');
VACUUM;
```

**Verification**: Check DB size before and after (`ls -lh`).

---

## N6: Wire harness_research to route_task()

**File**: `/home/roshan/nexus/server/router.py`
**Goal**: When a query contains research/academic keywords, call `harness_research()` and inject results into the routing pipeline before fallback.

### Approach: Add between Steps 3.5 and 6 in `route_task()`

1. Detect research intent via keyword matching (academic_patterns):
   ```python
   RESEARCH_KEYWORDS = ["research", "paper", "arxiv", "study", "academic", 
                         "semantic scholar", "paperswithcode", "github repo",
                         "scientific", "journal", "conference", "citation",
                         "literature review", "survey", "state of the art"]
   
   ACADEMIC_PATTERNS = re.compile(
       r'\b(?:research|paper|arxiv|study|survey|literature|academic|'
       r'semantic.scholar|papers.with.code|github.repo|'
       r'scientific|journal|conference|citation)\b', re.IGNORECASE)
   ```

2. If matched, call harness_research via the context harness MCP tool:
   ```python
   if ACADEMIC_PATTERNS.search(task) and HARNESS_RESEARCH_ENABLED:
       try:
           from server.integrations.rate_limited_proxy import rate_limited_call
           research_results = _call_harness_research(task, top_k=5)
           # Fuse with existing candidates — append as research_skills
           iso_candidates.extend(research_results)
           pipeline_steps.append("harness_research")
       except Exception as e:
           logger.debug(f"harness_research unavailable: {e}")
   ```

3. The call goes through the rate_limited_proxy (N7 wires this).

4. Add `HARNESS_RESEARCH_ENABLED` feature flag (default `true`).

### Verification:
```bash
python3 -c "from server.router import route_task; r = route_task('find papers on Thompson sampling for multi-armed bandits'); print(r.get('pipeline'))"
# Should include 'harness_research' in pipeline steps
```

---

## N7: Wire Rate Limited Proxy to Academic Channels

**File**: `server/integrations/rate_limited_proxy.py` (currently orphaned)
**Goal**: Wrap all external academic channel API calls through TokenBucket rate limiter.

### Approach:
1. Create a proxy wrapper in `context_harness/channels/` that routes all external HTTP calls through `rate_limited_proxy.TokenBucket`:
   ```python
   from server.integrations.rate_limited_proxy import TokenBucket
   
   _bucket = TokenBucket(rate=5, capacity=10)  # 5 req/s, burst 10
   
   def rate_limited_fetch(url, params=None):
       if not _bucket.consume():
           time.sleep(1.0 / _bucket.rate)
       return requests.get(url, params=params, timeout=10)
   ```

2. Refactor `arxiv_search.py`, `semantic_scholar.py`, `paperswithcode_search.py`, `github_search.py` to use `rate_limited_fetch()` instead of raw `requests.get()`.

### Verification: Check that API calls don't exceed configured rate.

---

## N8: Add Cluster Affinity Decay Factor

**File**: `server/learning.py`
**Problem**: All 8 clusters sit at 0.75-1.0 affinity. Zero differentiation means affinity cannot guide routing. Clusters with no recent activity should lose affinity over time.

### Approach: Add decay in `_update_cluster_affinity()`

Instead of only incrementing on outcome, also apply a small time-decay penalty to UNUSED clusters. The existing `cluster_affinity_history` table tracks per-cluster affinity with timestamps.

Add a periodic decay function called from `record_outcome()`:
```python
def _apply_affinity_decay(conn, decay_rate=0.001):
    """
    Apply exponential decay to cluster affinities.
    Clusters not updated in >24h lose 0.1% of their affinity per call.
    This forces unused clusters to drift toward 0.5 (neutral), creating
    differentiation between active vs stale clusters.
    """
    clusters = conn.execute("SELECT name FROM cluster_definitions").fetchall()
    for (cluster_name,) in clusters:
        latest = conn.execute("""
            SELECT new_affinity, created_at FROM cluster_affinity_history
            WHERE cluster_name = ?
            ORDER BY id DESC LIMIT 1
        """, (cluster_name,)).fetchone()
        if not latest:
            continue
        hours_since = (datetime.utcnow() - datetime.fromisoformat(latest["created_at"])).total_seconds() / 3600
        if hours_since > 24:
            # Decay toward neutral (0.5)
            current = latest["new_affinity"]
            new = current - decay_rate * (current - 0.5)  # pull toward 0.5
            conn.execute("""
                INSERT INTO cluster_affinity_history(cluster_name, trigger_query, old_affinity, new_affinity, reason)
                VALUES(?, ?, ?, ?, ?)
            """, (cluster_name, 'decay', current, round(new, 4),
                  f'decay: {hours_since:.0f}h inactive, rate={decay_rate}'))
```

Call this from `record_outcome()` before the affinity update:
```python
_decay_hour_counter = 0
# In record_outcome():
nonlocal _decay_hour_counter
_decay_hour_counter += 1
if _decay_hour_counter >= 50:  # Apply decay every ~50 outcomes
    _apply_affinity_decay(conn)
    _decay_hour_counter = 0
```

---

## N9: Cold-Start Campaign for Never-Triggered Resources

**Problem**: 827 active resources (43.1%) never triggered. They sit cold, never explored, never learned. No exploration budget reaches them because Thompson Sampling exploitation dominates.

### Approach: Force-explore in route_task()

Add to `route_task()` after Step 6 (Thompson), before fallback:
```python
# Cold-start exploration: with 15% probability, inject never-triggered
# resources near the top so they get tried and can accumulate outcome data.
if random.random() < 0.15:  # COLD_START_RATE
    cold_resources = _get_cold_resources(conn, top_k=3)
    if cold_resources:
        iso_candidates = cold_resources + iso_candidates
        pipeline_steps.append("cold_start")
```

Implement `_get_cold_resources()`:
```python
def _get_cold_resources(conn, top_k=3):
    """Return top never-triggered resources sorted by content quality (section count proxy)."""
    return conn.execute("""
        SELECT r.id, r.name, r.resource_type, COUNT(rs.id) as section_count
        FROM resources r
        JOIN resource_sections rs ON r.id = rs.resource_id
        WHERE r.times_triggered = 0 AND r.status = 'active'
        GROUP BY r.id
        ORDER BY section_count DESC
        LIMIT ?
    """, (top_k,)).fetchall()
```

---

## N10: Restart Processes + Smoke Test

**Prerequisites**: N2 (env vars), N4 (MCP purge), N6 (harness_research wiring)

### Steps:
```bash
# 1. Stop supervisor-managed processes
/home/roshan/nexus/venv/bin/supervisorctl -c /home/roshan/nexus/supervisord.conf stop all

# 2. Kill stray MCP processes
pkill -f "nexus-mcp-enhanced.py"
pkill -f "context_harness/server.py"

# 3. Start supervisor with new config
/home/roshan/nexus/venv/bin/supervisord -c /home/roshan/nexus/supervisord.conf

# 4. Verify env vars loaded
/home/roshan/nexus/venv/bin/supervisorctl -c /home/roshan/nexus/supervisord.conf status
```

### Smoke Tests:
1. **Cluster routing**: Query "deploy this app" → should activate `devops_infra` cluster
2. **Reflection**: Force a wrong outcome → should create reflection_fixes entry + reflection_log entry
3. **harness_research**: Query "find research papers on X" → `route_task()` pipeline should include `harness_research`
4. **SQL sanity**: No broken counters, no negative values, `routed` outcomes appearing

```bash
python3 -c "
from server.router import route_task
# Test 1: routing
r1 = route_task('deploy to production')
print('Cluster:', r1.get('cluster'))
print('Pipeline:', r1.get('pipeline_steps'))

# Test 2: harness (if wired)
r2 = route_task('find papers on Thompson sampling')
print('Pipeline:', r2.get('pipeline_steps'))
"
```

---

## N11: Track Routing Accuracy — Monitor Phase

After N1-N10 deployed, track over the next 100 routing queries:

| Metric | Before | Target | Check |
|--------|--------|--------|-------|
| Broken counters | 0 | 0 | Daily |
| Partial/Routed ratio | 1915/1 | Routed > Partial | After 100 queries |
| Cluster routing active | Env vars missing | `cluster_route` in pipeline | Per query |
| Reflection log entries | 0 | >0 | After first wrong outcome |
| harness_research calls | 0 (not wired) | Academic queries get harness step | Per academic query |
| MCP resource count | 327 (26 active) | 26 | After N4 |
| DB size | 492 MB | <400 MB | After N5 |
| Cluster affinity range | 0.75-1.0 | 0.3-0.9 (differentiated) | After ~200 outcomes with decay |

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Supervisor restart kills live MCP | Medium | High — breaks opencode session | Do out-of-session. Stop supervisor first, then kill strays, then restart. |
| harness_research adds latency to route_task | Medium | Medium | Call async, timeout 3s. Fallback gracefully if harness times out. |
| Affinity decay too aggressive | Low | Medium | Start with 0.001 decay rate, monitor for 50 outcomes before adjusting. |
| Cold-start exploration degrades routing accuracy | Medium | Medium | Start at 15% rate, drop to 5% if accuracy drops >2%. Auto-disable after 100 triggers each. |
| Rate limited proxy breaks academic channels | Low | High | Test each channel individually after wiring. Fallback to raw requests on proxy error. |

---

## Expected Outcomes

After this plan:
- **50% reduction in orphaned code** (delete SSE, wire 3 modules)
- **Cluster routing + reflection LIVE** (env vars in supervisor)
- **Academic research integrated** into routing pipeline
- **600+ cold resources explored** within 500 queries (15% cold-start rate)
- **Cluster affinity differentiated** (0.3-0.9 range vs current 0.75-1.0)
- **DB size reduced** by ~25% (VACUUM + MCP purge + embedding prune)
