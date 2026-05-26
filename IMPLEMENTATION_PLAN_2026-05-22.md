# NEXUS GAP FIX IMPLEMENTATION PLAN
# Protocol: adaptive-imagining-cat v4
# Date: 2026-05-22
# Target: 95%+ system health, aligned with "self-learning external brain" vision

## PHASE 0: FOUNDATION FIXES (Blocking — must do first)
### These are wiring bugs that make everything else dead code.

---

### P0-1: Wire record_outcome() Into Routing Pipeline
**Problem:** Edge learning is DEAD. learning.py has multiplicative update code (L247-262)
but record_outcome() only fires from external HTTP /report_outcome. Internal
routing (route_task → find_matching_skills → return) has NO feedback loop.

**Research**: Industry pattern is Impression → Outcome Upgrade:
1. Log "routed" at serve time (route_id, arm, timestamp)
2. On feedback: UPDATE outcome='correct'|'wrong'
3. Periodic batch: close_outcomes() for unclosed routes

**Implementation:**
- [ ] Add `record_outcome("routed")` call at end of `find_matching_skills()`
  after selecting top-k candidates
- [ ] Track `route_id` in routing_log for each recommendation
- [ ] Add `_auto_close_routed()` function that runs on every record_outcome()
  call — upgrades stale "routed" entries to "correct" if times_triggered > 0
  and no explicit "wrong" was logged
- [ ] Wire edge weight update to fire on EVERY outcome, not just explicit calls
- [ ] Add exponential decay on edge weights: nightly, `weight *= 0.995`
  (prevents stale connections from dominating)

**Files:** server/router.py (find_matching_skills), server/learning.py (record_outcome)

---

### P0-2: Merge Dual Cluster System → Single Source of Truth
**Problem:** Router reads resources.cluster (flat column). Recluster script writes
to cluster_membership (junction table). 90 resources (4.8%) already diverged.

**Research:** Junction table pattern for multi-cluster membership is standard
(resources can belong to multiple clusters). Single-column can't express this.

**Implementation:**
- [ ] Verify cluster_membership has ALL resources from resources.cluster
  (INSERT missing ones with is_primary=1)
- [ ] Create backward-compatible VIEW `v_resource_cluster` that reads from
  cluster_membership where is_primary=1
- [ ] Update router.py to read from cluster_membership instead of resources.cluster:
  ```sql
  -- OLD: SELECT ... FROM resources WHERE cluster=? AND status='active'
  -- NEW: SELECT r.* FROM resources r
  --       JOIN cluster_membership cm ON cm.resource_id = r.id
  --       WHERE cm.cluster_name=? AND r.status='active'
  ```
- [ ] Update find_matching_skills() cluster filter (Q1, Q2, Q3, Q8)
- [ ] Update detect_cluster() and route_to_clusters() to use cluster_membership
- [ ] After verification: ALTER TABLE resources DROP COLUMN cluster
  (requires SQLite 3.35+)
- [ ] Add SQL triggers for resource_count auto-sync:
  ```sql
  CREATE TRIGGER trg_cm_insert_count
  AFTER INSERT ON cluster_membership
  FOR EACH ROW BEGIN
      UPDATE cluster_definitions
      SET resource_count = (SELECT COUNT(*) FROM cluster_membership
                            WHERE cluster_name = NEW.cluster_name)
      WHERE name = NEW.cluster_name;
  END;

  CREATE TRIGGER trg_cm_delete_count
  AFTER DELETE ON cluster_membership
  FOR EACH ROW BEGIN
      UPDATE cluster_definitions
      SET resource_count = (SELECT COUNT(*) FROM cluster_membership
                            WHERE cluster_name = OLD.cluster_name)
      WHERE name = OLD.cluster_name;
  END;
  ```

**Files:** server/router.py, server/cluster_router.py, server/cluster_classifier.py,
  db/migrations/008_single_cluster_source.sql

---

### P0-3: Fix UCB1 Div/0 + Activate Thompson Sampling for Cold Resources
**Problem:** UCB1 formula √(ln(N)/n_i) crashes for n=0. 469 cold resources have
zero Thompson beliefs. Plan proposed UCB1 but system already uses Thompson
Sampling — which handles cold-start NATIVELY via Beta(1,1).

**Research:** Thompson Sampling with Beta(1,1) prior is the standard cold-start
approach (Spotify, Netflix, Google Ads all use it). Beta(1,1) has maximum
variance (1/18) so cold arms get explored naturally. UCB1's div/0 problem is
why Thompson is preferred.

**Implementation:**
- [ ] Remove UCB1 exploration bonus from find_matching_skills() — Thompson
  Sampling already handles exploration via Beta posterior sampling
- [ ] Backfill model_beliefs from existing routing_log for resources with
  outcomes but no beliefs entry:
  ```sql
  INSERT OR IGNORE INTO model_beliefs (provider, model, task_type, alpha, beta, total_requests, total_reward, avg_reward, last_updated)
  SELECT 'nexus', 'resource:' || rl.skill_name, 'general',
         SUM(CASE WHEN rl.outcome='correct' THEN 1.0 ELSE 0 END) + 1.0,
         SUM(CASE WHEN rl.outcome='wrong' THEN 1.0 ELSE 0 END) + 1.0,
         COUNT(*),
         SUM(CASE WHEN rl.outcome='correct' THEN 1.0
                  WHEN rl.outcome='partial' THEN 0.5 ELSE 0 END),
         SUM(CASE WHEN rl.outcome='correct' THEN 1.0
                  WHEN rl.outcome='partial' THEN 0.5 ELSE 0 END) / COUNT(*),
         datetime('now')
  FROM routing_log rl
  WHERE rl.outcome IN ('correct', 'wrong', 'partial')
    AND rl.skill_name IS NOT NULL
  GROUP BY rl.skill_name;
  ```
- [ ] For remaining cold resources with NO routing history: use nearest-neighbor
  warm-start from embedding similarity. For each cold resource, find 3 warm
  neighbors and average their (alpha, beta) as priors.
- [ ] Add semantic warm-start for brand-new resources: if resource has
  cos_sim > 0.7 to a hot resource, inherit its belief as initial prior

**Files:** server/router.py, scripts/backfill_beliefs.py (update), server/learning.py

---

## PHASE 1: INGESTION PIPELINE CONNECTION
### Make NEXUS capable of absorbing external knowledge.

---

### P1-1: Connect nexus-ingest.py to ingestion_tables
**Problem:** Tables exist (ingestion_sources, ingestion_chunks, dedup_candidates)
but nexus-ingest.py doesn't write to them. Zero chunks ingested.

**Implementation:**
- [ ] Modify nexus-ingest.py to write to ingestion_sources on file intake:
  ```python
  source_id = conn.execute("""
      INSERT INTO ingestion_sources (source_type, source_path, content_hash,
                                      total_chunks, status, created_at)
      VALUES (?, ?, ?, ?, 'processing', datetime('now'))
  """, (source_type, source_path, content_hash, total_chunks)).lastrowid
  ```
- [ ] Modify nexus-ingest.py to write each chunk to ingestion_chunks:
  ```python
  conn.execute("""
      INSERT INTO ingestion_chunks (source_id, chunk_index, chunk_type,
                                     start_line, end_line, content, status)
      VALUES (?, ?, ?, ?, ?, ?, 'pending')
  """, (source_id, i, chunk_type, start, end, content))
  ```
- [ ] After embedding: update chunk status to 'embedded' and set resource_id FK
- [ ] After resource registration: update source status to 'completed'
- [ ] Auto-assign cluster to new resources using cluster_classification:
  classify via embedding → nearest cluster centroid → insert into cluster_membership
- [ ] Auto-seed model_beliefs for new resources: Beta(1,1) with task_type='general'

**Files:** scripts/nexus-ingest.py, server/cluster_classifier.py

---

## PHASE 2: REFLECTION ACTIVATION
### Make the self-correction loop actually fire in production.

---

### P2-1: Activate Reflection for Partial Outcomes
**Problem:** Reflection only triggers on explicit "wrong" outcomes. 262 partial
outcomes never trigger self-correction.

**Implementation:**
- [ ] In learning.py record_outcome(), add partial → reflection trigger:
  ```python
  if outcome in ("wrong", "partial"):
      # Trigger reflection for wrong outcomes
      # For partial: only if resource's error_rate > 30%
      if outcome == "wrong" or (outcome == "partial" and error_rate > 0.3):
          _trigger_reflection(conn, skill_name, outcome, task_summary)
  ```
- [ ] Add reflection_log entries for partial-triggered diagnoses
- [ ] Add `trigger_threshold` column to reflection configuration so partial
  reflection threshold is configurable (default: error_rate > 30%)

**Files:** server/learning.py, server/reflection.py

---

### P2-2: Auto-Close Pending Routes
**Problem:** 266 routes stuck at outcome=NULL or 'routed'. These never contribute
to learning — 21.2% gap in outcome coverage.

**Research:** NEXUS already has close_outcomes() tool but it's not called
automatically.

**Implementation:**
- [ ] Add _auto_close_routed() function in learning.py:
  ```python
  def _auto_close_routed(conn, max_age_hours=24):
      """Auto-close stale 'routed' entries as 'correct' if resource has >60% WR."""
      conn.execute("""
          UPDATE routing_log SET outcome = 'correct'
          WHERE outcome IN ('routed', NULL)
            AND created_at < datetime('now', ?)
            AND skill_name IN (
                SELECT name FROM resources
                WHERE times_triggered > 5
                  AND times_correct > times_wrong * 2
            )
      """, (f'-{max_age_hours} hours',))
  ```
- [ ] Call _auto_close_routed() at the START of every find_matching_skills() call
- [ ] Log auto-closed count for monitoring

**Files:** server/learning.py, server/router.py

---

## PHASE 3: GRAPH & EDGE ACTIVATION
### Make the knowledge graph learn from outcomes.

---

### P3-1: Activate Edge Weight Learning
**Problem:** 6,864 edges exist, ZERO have been updated by outcomes. The
multiplicative update code exists (learning.py L247-262) but is unreachable
from internal routing.

**Implementation:**
- [ ] After P0-1 wires record_outcome into routing, edge updates WILL fire
  automatically. Verify by checking:
  ```sql
  SELECT COUNT(*) FROM resource_edges WHERE updated_at > created_at;
  ```
  after a few routed+feedback cycles.
- [ ] Add temporal decay: nightly cron that runs:
  ```sql
  UPDATE resource_edges SET weight = weight * 0.995 WHERE weight > 0.05;
  ```
- [ ] Add edge weight logging: track weight changes in reflection_log for monitoring

**Files:** server/learning.py (already has the code), scripts/nexus-maintenance.py

---

### P3-2: Compute Cluster Centroids from FAISS Embeddings
**Problem:** All 8 clusters have has_centroid=false. Cluster routing uses
keyword matching only, missing semantically similar resources.

**Implementation:**
- [ ] Run compute_cluster_centroids.py to generate centroids from FAISS:
  ```python
  for cluster_name in cluster_names:
      resource_ids = get_resource_ids_for_cluster(cluster_name)
      embeddings = get_embeddings_for_ids(resource_ids)
      centroid = np.mean(embeddings, axis=0)
      centroid = centroid / np.linalg.norm(centroid)  # L2-normalize
      store_centroid(cluster_name, centroid)
  ```
- [ ] Update cluster_definitions: set has_centroid=true, store centroid_vector
- [ ] Update cluster_router.py to use centroid distance as first-pass filter:
  compute cos_sim(query_embedding, centroid) → if < 0.3, skip cluster
- [ ] This reduces ISO scoring search from 1,831 → ~150-200 resources per query

**Files:** scripts/compute_cluster_centroids.py, server/cluster_router.py

---

## PHASE 4: VERIFICATION & AUDIT
### Prove the system works end-to-end.

---

### P4-1: End-to-End Test Suite
- [ ] test_feedback_loop.py: Route a task → verify "routed" logged →
  call report_outcome("correct") → verify belief updated → verify edge weight
  updated → verify cluster affinity updated
- [ ] test_cold_start.py: Route a task for a cold resource → verify Thompson
  prior (Beta(1,1)) → verify backfilled beliefs from routing_log
- [ ] test_cluster_consistency.py: INSERT into cluster_membership → verify
  resource_count auto-updated → verify router reads from membership
- [ ] test_ingestion_pipeline.py: Run nexus-ingest.py on a test file → verify
  ingestion_sources + ingestion_chunks populated → verify resource registered
  → verify auto-cluster assigned → verify belief seeded
- [ ] test_edge_decay.py: Run nightly decay → verify weights decreased →
  verify no weight below 0.05

### P4-2: Final Audit Score
- [ ] Re-run nexus-audit.py after all fixes
- [ ] Verify score improvement from 72/100 → 90+/100
- [ ] Verify all 8 gaps closed:
  1. Cold-start: Thompson Beta(1,1) + warm-start + belief backfill ≈ 95% coverage
  2. Ingestion: pipeline connected, auto-cluster + belief seeded
  3. Reflection: partial outcomes trigger reflection (configurable threshold)
  4. Test failure: DB path fix (trivial)
  5. Dual cluster: single source of truth + triggers
  6. Edge learning: LIVE via record_outcome wiring + temporal decay
  7. resource_count: auto-sync via SQL triggers
  8. Cold beliefs: backfilled from routing_log + nearest-neighbor priors

---

## EXECUTION ORDER

| Phase | Task | Est. Hours | Blocks? |
|-------|------|------------|---------|
| P0-1 | Wire record_outcome into routing | 2h | Blocks P3-1 |
| P0-2 | Merge dual cluster → single source | 1.5h | Blocks P3-2 |
| P0-3 | Fix cold-start (remove UCB1, backfill beliefs) | 2h | Nothing |
| P1-1 | Connect ingestion pipeline | 2h | Nothing |
| P2-1 | Activate reflection for partial | 1h | Blocks on P0-1 |
| P2-2 | Auto-close pending routes | 1h | Blocks on P0-1 |
| P3-1 | Verify edge learning activated | 0.5h | Depends on P0-1 |
| P3-2 | Compute cluster centroids | 1h | Depends on P0-2 |
| P4-1 | End-to-end test suite | 2h | After all above |
| P4-2 | Final audit | 1h | After P4-1 |
| **TOTAL** | | **14h** | |

## RESEARCH REFERENCES

- Thompson, W.R. (1933) "On the likelihood that one unknown probability exceeds another"
- Auer, P. et al. (2002) "Finite-time analysis of the multiarmed bandit problem"
  — UCB1 mandates initial pull of each arm before computing bonus (div/0 fix)
- Chapelle, O. & Li, L. (2011) "An empirical evaluation of Thompson Sampling"
  — Thompson Beta(1,1) outperforms UCB1 in practice, no div/0 issue
- Spotify Engineering Blog: "Explore-Exploit in Music Recommendation" (2019)
  — Impression → Outcome upgrade pattern; Bayesian updating with position bias
- Netflix Tech Blog: "Artwork Personalization with Thompson Sampling" (2017)
  — Batched belief updates (every few hours, not real-time); delayed reward handling
- Neo4j GDS Documentation: Weighted graph algorithms, batch compute pattern
- PyTorch Geometric: scatter_add_ for streaming edge weight updates with EMA
- mab2rec (GitHub): Built-in add_arm + record_outcome feedback loop
- river (online-ml): Bandit.learn_one → pull, update_one → learn pattern