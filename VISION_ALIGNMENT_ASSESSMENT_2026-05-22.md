# NEXUS Vision Alignment Assessment — 2026-05-22

## Vision Statement
NEXUS should be a **self-learning, graph-connected external brain** that replaces
Obsidian vaults and scales to 5K+ resources — learning from every routing outcome,
auto-correcting failures, absorbing external knowledge, and strengthening connections.

## Your 4 Gaps vs My Investigation Findings

### Your Gaps (Confirmed)

| # | Gap | Severity | Status |
|---|-----|----------|--------|
| 1 | Cold-start exploration (49.6% resources never triggered) | HIGH | Plan covers with UCB1 + epsilon decay, BUT has math bug (div/0 for n=0) |
| 2 | Ingestion pipeline disconnected | HIGH | Plan covers: nexus-ingest.py → ingestion_chunks. Correct approach. |
| 3 | Reflection passive (partial outcomes don't trigger it) | MEDIUM | Plan covers: partial triggers reflection with lower threshold. Good. |
| 4 | 1 test failure (MCP sync DB path) | LOW | Not in plan. Trivial fix. Not aligned to vision. |

### Hidden Gaps (NOT in your plan)

| # | Gap | Severity | Detail |
|---|-----|----------|--------|
| 5 | **Dual cluster system divergence** | HIGH | 90 resources (4.8%) assigned differently in `resources.cluster` vs `cluster_membership`. Router reads `resources.cluster`. The membership table is **orphaned** — written to by nobody, read by nobody in the routing path. |
| 6 | **Edge learning is dead** | HIGH | 6,864 edges exist with proper types and weights. `learning.py` L247-262 has multiplicative update code. But **zero edges have been updated by outcomes**. `record_outcome()` only fires from HTTP `/report_outcome`. Internal routing never calls it. Your graph is STATIC. |
| 7 | **cluster_definitions.resource_count goes stale** | MEDIUM | Fixed manually twice this week. No SQL trigger or daemon cron to keep it in sync. Will diverge again. The plan's recluster_general.py will make this worse. |
| 8 | **469 cold resources have zero Thompson beliefs** | MEDIUM | Even with UCB1, these start at α=1,β=1 (uniform). The routing_log already has 4,528 outcomes for some resources — but those outcomes are NOT backfilled into model_beliefs for the resources that were triggered. |

## Gap-by-Gap: Does the Plan Cover It?

### Gap 1: Cold-start exploration
- **Plan says**: UCB1 scoring + epsilon decay + semantic warm-start
- **Bug found**: UCB1 formula √(ln(N)/n_i) is div/0 for n=0 (469 resources). Plan also proposes warm-start for cos_sim>0.7 which helps but doesn't cover all cold resources.
- **Missing**: Belief backfill from existing routing_log (Gap 8 makes this worse)
- **Verdict**: 70% covered. Fix the div/0 bug, add belief backfill.

### Gap 2: Ingestion pipeline disconnected
- **Plan says**: Modify nexus-ingest.py to write to ingestion_sources + ingestion_chunks, then embed and register in resources.
- **Missing**: No description of HOW chunks connect back to routing. Ingested chunks become resources but have no trigger history, no beliefs, no cluster assignment.
- **Verdict**: 80% covered. Add: auto-cluster + auto-belief-seeding for ingested chunks.

### Gap 3: Reflection passive
- **Plan says**: Allow partial outcomes with low success score to trigger reflection. IDE feedback integration.
- **Missing**: The plan doesn't address THAT record_outcome() is only called via HTTP `/report_outcome`. The router's `find_matching_skills()` never calls `record_outcome()`. The internal routing path (route_task → find_matching_skills → return) has NO feedback loop. Even if you make partial trigger reflection, nobody calls record_outcome internally.
- **Verdict**: 40% covered. The REAL fix: hook record_outcome into the routing pipeline so internal routes generate learning.

### Gap 4: Test failure
- **Not in plan**. Trivial. Skip.

### Gap 5: Dual cluster divergence (HIDDEN)
- **Not in plan at all.**
- **Impact**: The recluster_general.py script writes to `cluster_membership`. The router reads from `resources.cluster`. They will DIVERGE more after reclustering.
- **Fix**: Either make router read from `cluster_membership` (1 source of truth), or have recluster_general.py update BOTH tables. Better: drop `resources.cluster`, use `cluster_membership` everywhere.

### Gap 6: Edge learning dead (HIDDEN)
- **Plan says**: "Dynamic edge weight updates will modify relationships based on routing outcomes."
- **Reality**: The code exists (learning.py L247-262) but is NEVER called from the internal routing path. It only fires from HTTP `/report_outcome` which external tools must call explicitly.
- **Impact**: Without this, your "self-learning graph" is a **static graph**. The entire vision of NEXUS as a learning brain depends on edges updating from outcomes.
- **Fix**: After every internal `route_task()` call, check if the routed resource was used (via IDE callback or explicit outcome), and call `record_outcome()`. Or: add a background process that watches routing_log for new entries and triggers learning.

### Gap 7: resource_count stale (HIDDEN)
- **Not in plan.** The plan proposes recluster_general.py which will CHANGE cluster assignments but not update resource_count.
- **Fix**: Add a SQL trigger: `AFTER INSERT/DELETE ON cluster_membership UPDATE cluster_definitions.resource_count`.

### Gap 8: 469 cold resources with zero beliefs (HIDDEN)
- **Not in plan.** UCB1 requires belief data; these have none.
- **Fix**: Backfill model_beliefs from existing routing_log for resources that have outcomes but no beliefs entry. Then seed cold resources with nearest-neighbor belief priors.

## Priority Ranking for Vision Alignment

| Priority | Gap | Blocks Vision? | Effort |
|----------|-----|-----------------|--------|
| P0 | **Gap 6: Edge learning dead** | YES — graph is static, not learning | 2h |
| P0 | **Gap 5: Dual cluster source-of-truth** | YES — recluster will diverge | 1h |
| P1 | **Gap 1/Fix: UCB1 div/0 + belief backfill** | YES — cold resources unrouteable | 2h |
| P1 | **Gap 2/Fix: Auto-cluster+beliefs for ingested** | Partially — ingested resources invisible | 1h |
| P1 | **Gap 3/Fix: Hook record_outcome into routing** | YES — no feedback loop | 2h |
| P2 | **Gap 7: resource_count auto-sync** | Degradation — will go stale again | 30min |
| P2 | **Gap 8: Belief backfill from routing_log** | Partially — improves warm-start | 1h |

## Verdict

**Can NEXUS align with the vision after the proposed plan?**

**No — not until Gaps 5 and 6 are fixed.** The plan treats these as solved (edge weight updates in the plan, cluster reassignment in the plan) but both have **wiring bugs** that make them dead code:

1. **Edge weight updates are unreachable from the routing path.** The code exists but no internal caller invokes it. This makes the graph static — the #1 feature of the vision (self-learning graph) is non-functional.

2. **Recluster will write to the WRONG table.** The router reads `resources.cluster`; recluster will write to `cluster_membership`. After executing the plan as written, clusters will be MORE divergent, not less.

3. **UCB1 crashes for 469 resources.** The exploration bonus the plan depends on has a div/0 bug.

Fix these 3 first, THEN execute the plan. The plan's ingestion, reflection, and edge weight features are architecturally sound — they just can't work until the feedback loop (Gap 6) and data model (Gap 5) are correct.

## Recommended Execution Order

1. **Wire `record_outcome()` into `find_matching_skills()`** — After routing, auto-log a "routed" outcome. After IDE callback, upgrade to "correct"/"wrong". This makes the learning loop LIVE.
2. **Merge `resources.cluster` and `cluster_membership`** into single source of truth. Drop `resources.cluster`, make everything use `cluster_membership`.
3. **Fix UCB1 div/0** — Branch for n=0: flat 0.15 bonus.
4. **Backfill beliefs from routing_log** — One-shot script to seed model_beliefs from existing outcome data.
5. **Add SQL trigger for resource_count** — Prevents stale counts.
6. **NOW execute the plan**: Ingestion, reflection, recluster, similarity edges.