# Plan Review: Gap Resolution to 95%+

**Reviewer**: Ground-truth audit against live DB  
**Date**: 2026-05-22  
**Verdict**: PARTIALLY COVERS ISSUES. 4 critical gaps missed. 3 proposals need correction before implementation.

---

## Scorecard: Does the Plan Cover All Audit Findings?

| Audit Gap | Plan Addresses? | Assessment |
|-----------|-----------------|------------|
| resource_edges table | YES (migration 007) | Already exists from fix session. Plan's edge types MISMATCH (see below) |
| cluster resource_count stale | NO | Not mentioned. resource_count was wrong TWICE — needs a trigger, not a one-shot UPDATE |
| 909 cold resources (49.6%) | PARTIALLY | UCB1 and semantic warm-start help, but don't address why 93.3% of Thompson beliefs are still α=1,β=1 |
| General cluster bloated (533) | YES | recluster_general.py is good |
| Ingestion pipeline disconnected | YES | nexus-ingest.py modification is correct |
| Reflection pipeline passive | PARTIALLY | IDE feedback function is good, but plan ignores the 93.3% cold-belief problem |
| Static edge weights | YES | Dynamic weight updates in record_outcome |
| Exploration decay | YES | But formula is WRONG (see Critical Bugs) |

---

## CRITICAL BUGS IN THE PLAN

### Bug 1: Epsilon Decay Formula Kills Exploration Immediately

**Proposed**: `ε = max(0.02, 0.15 · e^(-0.0005 · N))`

**Problem**: At current N=4,573 routing_log entries, this evaluates to:
```
ε = max(0.02, 0.15 · e^(-0.0005 · 4573))
  = max(0.02, 0.15 · e^(-2.287))
  = max(0.02, 0.15 · 0.1016)
  = max(0.02, 0.0152)
  = 0.02
```

**Exploration is ALREADY DEAD at current traffic levels.** The rate drops to 2% before the system has learned anything. This would lock in the 909 cold resources forever.

**Fix**: Use a much slower decay rate, or tie it to Thompson Sampling convergence rather than raw count:
```
ε = max(0.05, 0.15 · e^(-0.00003 · N))  
```
At N=4,573: ε = 0.132 (still exploring)  
At N=50,000: ε = 0.05 (converged)

Or: **Don't decay epsilon at all.** Thompson Sampling already handles exploitation (high-α resources get sampled more). Epsilon should stay at 10-15% permanently for cold-start discovery. The UCB1 bonus addresses the same problem differently.

### Bug 2: Migration 007 Edge Types vs Production DB Mismatch

**Migration 007 proposes**: `dependency`, `alternative`, `documentation_for`  
**Production DB has**: `co_triggered` (4,062), `related_to` (588), `similar_to` (1,100), `dependency` (7)

If you run this migration as-is, it will NOT create the table (IF NOT EXISTS skips it since the table already exists from our fix session). But it also won't add the 3 missing types or update the schema. The migration silently does nothing.

**Fix**: Split into:
- `007_a_resource_edges.sql` — CREATE TABLE IF NOT EXISTS (with current schema including co_trigger_count, updated_at)
- `007_b_ingestion_tables.sql` — CREATE ingestion_sources + ingestion_chunks
- `007_c_dedup_candidates.sql` — CREATE dedup_candidates  
- `007_d_indexes.sql` — Add any missing indexes

### Bug 3: UCB1 Formula Doesn't Handle Zero-Trigger Resources

**Proposed**: `ucb_bonus = c · √(ln(N_total) / n_i)` where n_i is trigger count.

**Problem**: For n_i = 0 (909 cold resources), this produces **division by zero**. The formula requires n_i ≥ 1.

**Fix**: Add an explicit branch:
```python
if n_i == 0:
    # Cold start: semantic warm-start score instead
    ucb_bonus = 0.15  # flat exploration bonus
elif n_i <= 10:
    ucb_bonus = c * math.sqrt(math.log(N_total) / n_i)
else:
    ucb_bonus = 0  # Thompson Sampling is sufficient
```

### Bug 4: Edge Weight Update Formula Has No Decay Cap Logic

**Proposed**: +0.05 on correct, -0.10 on wrong, capped at 1.0, floored at 0.0.

**Problem**: With 4,573 routing_log entries, a resource that was correct 100 times would have weight 1.0 (capped). A resource wrong 10 times would drop to floor. There's no consideration for:
- **Baseline weight preservation**: A `co_triggered` edge with weight 0.41 shouldn't jump to 0.91 from 10 correct outcomes
- **Per-edge-type scale**: `similar_to` edges start at 0.2, `related_to` at 0.3, `dependency` at 0.8. A flat ±0.05/0.10 adjustment doesn't scale properly
- **Recency bias**: All historical outcomes count equally

**Fix**: Use multiplicative updates instead of additive:
```python
# On correct outcome:
weight = min(1.0, weight * 1.05)  # 5% increase, preserves relative ordering

# On wrong outcome:
weight = max(0.05, weight * 0.85)  # 15% decrease, can't go below 0.05
```

This preserves edge type baselines and avoids rapid weight inflation.

---

## GAPS IN THE PLAN (Not Covered)

### Gap 1: Thompson Sampling Cold Belief Problem (93.3% Cold)

**Data**: 2,174 out of 2,329 Thompson beliefs (93.3%) are still at α=1, β=1 (uniform prior). Only 155 resources have any observations. This means Thompson Sampling is essentially random for 93% of resources.

**Problem**: The plan adds UCB1 and epsilon decay but never addresses why Thompson Sampling beliefs aren't being populated. The routing_log has 4,573 entries, but only 155 resources have accumulated belief updates.

**Root cause**: `record_outcome` only updates beliefs when explicitly called. Most routing_log entries have `outcome=NULL` (49 entries) or the caller never calls `record_outcome`. The belief update path is disconnected from the routing log.

**Missing item**: A background reconciliation job that backfills Thompson beliefs from existing routing_log outcomes:
```python
# For each resource with routing_log entries but α=1,β=1:
# Count correct/wrong outcomes, set α = 1 + correct_count, β = 1 + wrong_count
```

### Gap 2: cluster_definitions.resource_count Keeps Going Stale

**Already fixed TWICE in 2 sessions.** The plan doesn't address WHY it keeps going stale. There's no trigger to update it when cluster_membership changes.

**Missing item**: Add a SQLite trigger or an application-level hook:
```sql
CREATE TRIGGER update_cluster_count_after_insert
AFTER INSERT ON cluster_membership
BEGIN
    UPDATE cluster_definitions 
    SET resource_count = (SELECT COUNT(*) FROM cluster_membership WHERE cluster_name = NEW.cluster_name)
    WHERE name = NEW.cluster_name;
END;
```

### Gap 3: resources.cluster Column vs cluster_membership Table Divergence

**Data**: The `resources` table has a `cluster` column (VARCHAR), but actual cluster assignment is via `cluster_membership` table using `cluster_name`. These are NOT synchronized. In the current DB:
- `resources.cluster` has values like 'analyzer_planner' (414 resources)
- `cluster_membership` maps 533 resources to 'general'

These are **different cluster assignments** in two different places. The router reads from `cluster_membership` but the `resources.cluster` column is stale.

**Missing item**: Synchronize these two sources. Either:
- Drop the `resources.cluster` column and always use `cluster_membership`, OR  
- Add a trigger to keep `resources.cluster` in sync with the primary membership in `cluster_membership`

### Gap 4: No Verification for Ingestion Pipeline End-to-End

**Plan says**: "For each ingested file/URL, verify/insert a row in ingestion_sources...insert into ingestion_chunks...compute embedding, register in the resources table."

**Missing**: How do you verify the whole pipeline works? No integration test. The plan mentions `test_gaps_resolved.py` but doesn't specify assertions for:
- Chunk deduplication (same file ingested twice)
- Embedding quality (empty/zero vectors)
- Resource registration (chunk → resource_id mapping)
- Error handling (malformed markdown, empty files)

---

## CORRECTED IMPLEMENTATION PLAN

### Phase 1: Schema Migrations (007_a through 007_d)

Split the monolithic migration into separate, idempotent DDL statements. DO NOT recreate resource_edges (already exists). Focus on:

1. `007_a_resource_edges_indexes.sql` — Add indexes on edge_type, weight, updated_at
2. `007_b_ingestion_tables.sql` — ingestion_sources + ingestion_chunks (already created in fix session, but add to migration for new deployments)
3. `007_c_dedup_candidates.sql` — Same, already exists
4. `007_d_cluster_count_trigger.sql` — Trigger to auto-update cluster_definitions.resource_count
5. `007_e_sync_cluster_columns.sql` — Synchronize resources.cluster with cluster_membership

### Phase 2: Router Exploration (Corrected)

```python
# Epsilon: NO DECAY (keep at 15% permanently)
# Thompson Sampling is the exploitation mechanism; epsilon preserves cold-start discovery
EPSILON = 0.15  # constant, not decaying

# UCB1: for low-trigger candidates (n_i between 1 and 10)
if n_i == 0:
    ucb_bonus = 0.15  # flat exploration bonus for truly cold resources
elif n_i <= 10:
    ucb_bonus = 0.15 * math.sqrt(math.log(N_total) / max(n_i, 1))
else:
    ucb_bonus = 0  # Thompson Sampling sufficient

# Semantic warm-start for cold resources
if n_i == 0 and cosine_similarity(query_embedding, resource_embedding) > 0.70:
    warm_start_boost = 0.15
else:
    warm_start_boost = 0.0
```

### Phase 3: Thompson Sampling Backfill

Before implementing new features, backfill existing routing_log outcomes into model_beliefs:

```sql
-- For each resource with outcomes but still at α=1,β=1:
UPDATE model_beliefs SET
    alpha = 1 + (SELECT COUNT(*) FROM routing_log WHERE skill_name LIKE '%' || model_beliefs.resource_name || '%' AND outcome = 'correct'),
    beta = 1 + (SELECT COUNT(*) FROM routing_log WHERE skill_name LIKE '%' || model_beliefs.resource_name || '%' AND outcome = 'wrong')
WHERE alpha = 1 AND beta = 1
AND resource_name IN (SELECT DISTINCT skill_name FROM routing_log WHERE outcome IS NOT NULL);
```

### Phase 4: Ingestion Pipeline Connection

Modify nexus-ingest.py to:
1. Write file metadata → `ingestion_sources` (path, hash, timestamp)
2. Chunk content → `ingestion_chunks` (source_id FK, chunk_index, content, line_start, line_end)
3. For each chunk: compute embedding → insert into `resource_embeddings` → register in `resources` with type='ingested' → update `ingestion_chunks.resource_id`
4. Auto-assign cluster via centroid cosine similarity

### Phase 5: Edge Weight Learning (Corrected)

```python
# record_outcome edge weight update (multiplicative, not additive)
if outcome == 'correct':
    weight = min(1.0, weight * 1.05)
elif outcome == 'wrong':
    weight = max(0.05, weight * 0.85)
elif outcome == 'partial':
    weight = weight  # no change for partial outcomes

# Only update edges for resources that were actually considered (in routing_decisions)
# Don't update ALL edges connected to a resource — only the ones in the current decision
```

### Phase 6: Sub-clustering + Similarity Edges

1. `scripts/recluster_general.py` — K-means (k=4) on general cluster embeddings, reassign to nearest named cluster centroid
2. `scripts/build_similarity_edges.py` — FAISS cosine similarity > 0.82 → similar_to edges (with dedup against existing edges)
3. `scripts/scan_duplicates.py` — cosine similarity > 0.90 → dedup_candidates (with manual review flag)

### Phase 7: Reconciliation + Monitoring

1. **Thompson belief backfill job** (one-time, then incrementally maintained)
2. **cluster_membership ↔ resources.cluster sync trigger**
3. **Stale resource_count auto-fix trigger**
4. **Health dashboard endpoint**: `/nexus/health` returning edge counts, cold resource %, belief coverage, ingestion status

---

## Items the Plan Correctly Addresses (No Changes Needed)

1. **Ingestion pipeline connection** — Good approach, just needs the sync/backfill additions above
2. **recluster_general.py** — Correct, but needs to use `cluster_membership` table, not `resources.cluster` column
3. **build_similarity_edges.py** — Good, but use FAISS for batch similarity (not per-pair cosine computation)
4. **scan_duplicates.py** — Good dedup detection threshold (0.90 cosine)
5. **IDE feedback integration** — Good design for `record_implicit_feedback`
6. **Partial outcome triggering reflection** — Correct and needed

---

## Summary: What to Do Before Implementation

1. **Fix the epsilon formula** — remove decay entirely, or use `e^(-0.00003 · N)` minimum 5%
2. **Add UCB1 zero-division guard** — branch for n_i = 0
3. **Replace additive edge weight updates with multiplicative** — preserves baselines
4. **Add Thompson Sampling backfill** — reconcile 2,174 cold beliefs from existing routing_log
5. **Add cluster_count trigger** — prevent resource_count from going stale again
6. **Synchronize resources.cluster with cluster_membership** — one source of truth
7. **Split migration 007** — into separate files for edges indexes, ingestion, dedup, triggers
8. **Don't recreate resource_edges** — table already exists with 5,757 rows
9. **Add end-to-end ingestion test** — chunk → embed → register → verify queryable

**Estimated effort**: 3-4 hours for corrected implementation. The plan is 70% ready; the 4 bugs and 4 gaps above need to be addressed first.