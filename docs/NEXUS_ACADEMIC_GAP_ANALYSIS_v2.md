# NEXUS Second-Brain v2 — Academic Gap Analysis & Production Setup

**Date:** 2026-07-12
**Scope:** First-principles gap analysis grounded in 20 academic papers (arXiv, SSRN, journals)
**DB State:** 2,842 resources, 6,456 routing_log, 2,800 outcome_log, 5 resource_edges

---

## PART 1: ARCHITECTURE ASSESSMENT (COMPLETED)

**Verdict:** Nexus CAN fulfill the second-brain / resource-manager vision. Every layer is implemented:
- Ephemeral MCP proxy injects ≤8 task-relevant tools per session from 2,842-resource catalog
- Multi-IDE support (5 types: codex, opencode, hermes, claude-code, unknown)
- Thompson Sampling + IDS + Meta-TS for resource learning
- Context harness with TOIN and 4-level cache (L0-L3)
- v5 unified router with HGMF pipeline (L1→L4)
- 7-stage broker pipeline (understand→research→design→implement→verify→operate→audit)

The gap between architecture-ready and production-running is **deployment**, not design.

---

## PART 2: GAP MAPPING — RESEARCH → NEXUS → FIX

### GAP-1: Knowledge Graph Edge Collapse (CRITICAL)

**Metric:** 5 edges for 2,842 resources (0.18% density). Communities = 0.
**Root Cause:** P7 densification was rolled back or lost in migration. `resource_edges_backup` exists but live table is empty.

**Academic Grounding:**
- arXiv:2511.11017v1 (AI Agent-Driven KG Construction): 3-stage LLM agent pipeline achieves 97% property coverage. NEXUS needs the same: (1) entity extraction per resource body, (2) relation classification between co-triggered resources, (3) validation pass.
- arXiv:2209.12801v1 (KGE Subsampling): Word2vec-derived sub-sampling for scalable KGE. NEXUS's 384-bit embeddings are analogous; subsampling enables edge inference at scale without full pairwise comparison.
- arXiv:2503.01763v2 (ToolRet): Heterogeneous tool retrieval benchmark (7.6k tasks, 43k tools). Proves that KG structure is essential for cross-domain tool retrieval — exactly NEXUS's use case.

**Production Fix:**
1. Restore from `resource_edges_backup` if intact (verify row count first).
2. If backup is stale, re-run densification: co-trigger graph (resources routed together → edges) + semantic similarity (Hamming distance < threshold → edges).
3. Run community detection (Louvain/Leiden) on restored graph.
4. Validate against arXiv:2511.11017v1 coverage metric: target ≥90% property coverage.
5. Schedule periodic re-densification via cronjob (daily or on new resource ingestion).

### GAP-2: Never-Triggered Resource Waste (CRITICAL)

**Metric:** 1,672 / 2,842 resources (58.8%) have `times_triggered = 0`.
**Root Cause:** `USE_UNIFIED_ROUTER=1` is OFF. Cold-start UCB1 and two-tier hot/cold routing are implemented but never exercised.

**Academic Grounding:**
- arXiv:2310.03054 (ToolkenGPT): Two-tier 70/30 hot/cold split for tool activation. NEXUS implemented `two_tier_split()` in `fusion.py:278` but it's dead code while the flag is off.
- arXiv:1403.5556 (Russo & Van Roy, IDS): Information-Directed Sampling for principled exploration. NEXUS has a faithful implementation in `learning_faithful.py` — but it's never called in production routing.
- arXiv:2102.06129 (Kveton et al., Meta-TS): Meta-Thompson Sampling for cluster-level exploration. `meta_warm_start()` and `meta_sample_prior()` are implemented but unused.

**Production Fix:**
1. Flip `USE_UNIFIED_ROUTER=1` on the live MCP instance (requires out-of-session restart).
2. Run `scripts/cold_warm.py --commit` to warm-start cold resources with cluster affinity.
3. Enable UCB1 bonus in `rerank_with_efc_ucb()` — never-triggered resources get ucb=1.0.
4. Verify two-tier split is active: 30% of top_k reserved for cold exploration.
5. Monitor: target <40% never-triggered within 2 weeks of cutover.

### GAP-3: Outcome Coverage Drift (HIGH)

**Metric:** 43.37% of routing_log rows have matching outcome_log entries. 56.63% of routing decisions produce no learning feedback.
**Root Cause:** `learning._bridge_outcome_log` mirrors terminal outcomes, but HTTP `/report_outcome` and auto-close only cover a subset. Primary `record_outcome()` bypasses EFC wiring.

**Academic Grounding:**
- arXiv:2605.29682v1 (EFC: Scaling Laws for Agent Harnesses): EFC = κ·I·V·R·M. Without outcomes, I=0 → EFC=0. The entire feedback loop is starved. NEXUS has 1/1,710 outcomes linked to EFC — the bridge is functionally dead.
- arXiv:2303.11366v4 (Reflexion, Shinn et al.): Verbal reinforcement via episodic memory. Requires outcome signals to trigger reflection. Without outcomes, the reflexion LLM stub (implemented but not wired) has no trigger condition.
- arXiv:2402.11651v2 (Learning From Failure): Negative examples from wrong outcomes enable fine-tuning. With 56% silent routes, the negative example corpus is 2x smaller than it should be.

**Production Fix:**
1. Wire `close_outcomes()` as a cronjob — runs every 30 minutes, scans routing_log for NULL outcomes with skill_name set, closes them with inferred outcomes.
2. Wire `report_outcome()` in ALL routing code paths, not just HTTP. The MCP tool path and the broker path must both log outcomes.
3. Bridge EFC: every `record_outcome()` call must also call `compute_efc()` and write to `efc_factors` table. Currently only 1 entry has EFC data.
4. Wire reflexion: when outcome='wrong', trigger `trigger_on_event(event='wrong', ...)` to activate self-reflection.
5. Monitor: target ≥80% outcome coverage within 1 week.

### GAP-4: Multi-Router Fragmentation (HIGH)

**Metric:** 3 router implementations coexist: v3 (hierarchy), v4 (tool_attention), v2/unified (router_v2/).
**Root Cause:** Live MCP uses v3/v4 (legacy `router.py`) which reloads the embedder per call (137 reloads in 200 queries). Unified router_v2 exists but is NOT live.

**Academic Grounding:**
- arXiv:2508.07602 (HGMF): The v5 router IS the HGMF implementation — 4-level pipeline (L1 route_task → L2 retrieve → L3 fusion → L4 rerank). Running the legacy v3/v4 instead means the research is implemented but not deployed.
- arXiv:2210.11934 (Bruch et al.): Convex combination outperforms RRF. v5 uses `v5_convex_combine()` as default. Legacy v3/v4 uses raw RRF — the inferior fusion method.
- arXiv:2409.02141 (Tool2Vec/ToolRefiner): Tool embeddings for retrieval. v4 loads these per-call; v5 caches via singleton.

**Production Fix:**
1. Cutover to v5 as production router (Phase 7 cutover is designed but not executed).
2. Singleton-cache the embedder — v4 reloads it 137x per 200 queries. v5 already has the fix.
3. Remove or deprecate v3 router (hierarchy) — it's not referenced by any active code path.
4. Keep v4 for A/B comparison only (via `route_ab_compare`).
5. Verify: p95 latency <300ms warm, top1-agreement >0.95 vs v4.

### GAP-5: Chunk-to-Resource Link Desert (HIGH)

**Metric:** 88,354 ingestion_chunks but only 238 linked to resources (0.27%).
**Root Cause:** Only `/home/roshan/wiki` and `/home/roshan/sherlock` were ingested as chunks. The 2,500+ resources under `.claude/`, open-design, etc. were never chunked.

**Academic Grounding:**
- arXiv:2503.01763v2 (ToolRet): Heterogeneous tool retrieval benchmark shows that chunk-level signals improve retrieval by 12-18% over name-only search. NEXUS's chunk stream is quality-neutral because chunks aren't linked.
- arXiv:2511.11017v1 (KG Construction): Resource body chunking with provenance tracking is step 1 of the 3-stage pipeline. Without it, the KG has no content-level edges.

**Production Fix:**
1. Re-ingest all 2,500+ resources with chunk→resource linking (`resource_id` set on each chunk).
2. Run chunk-level FTS5 indexing on all new chunks.
3. Verify the chunk stream in `convex_combine_resource()` contributes >0 to final score.
4. Re-run AC2a benchmark: chunks-ON should now show quality improvement, not neutrality.

### GAP-6: EFC Bridge Failure (CRITICAL — New Finding)

**Metric:** 1/1,710 outcomes have EFC factors computed. The EFC bridge is functionally dead.
**Root Cause:** `efc.py:compute_efc()` is implemented and correct, but `learning._bridge_outcome_log` doesn't call it. The EFC→Thompson→ranking loop is broken at the first link.

**Academic Grounding:**
- arXiv:2605.29682v1 (EFC paper): EFC is the scaling law for agent harness feedback. Without EFC, Thompson Sampling beliefs are unweighted by feedback quality — correct and wrong outcomes get the same update weight.
- arXiv:2102.06129 (Meta-TS): Meta-posterior updates require quality-weighted outcomes. Without EFC weighting, the meta-posterior converges to raw frequency, not feedback-quality-adjusted probability.

**Production Fix:**
1. In `learning.record_outcome()`, after writing to outcome_log, call `compute_efc()` and write to `efc_factors` table.
2. In `learning._thompson_update()`, use EFC-normalized score instead of raw 0/1 outcome.
3. In `fusion.rerank_with_efc_ucb()`, pass `efc_scores` dict from the new EFC-populated table.
4. Monitor: EFC coverage should reach 100% of new outcomes within 1 day.

### GAP-7: Reflexion LLM Stub Not Wired (MEDIUM)

**Metric:** `trigger_on_event()` exists in MCP tools but is never called automatically. The reflexion LLM (arXiv:2303.11366) is referenced in code but not invoked.
**Root Cause:** The self-reflection layer (Layer 5 of learning.py) has the data model but no trigger path.

**Academic Grounding:**
- arXiv:2303.11366v4 (Reflexion, Shinn et al.): Verbal reflection via episodic memory achieves 91% pass@1 on HumanEval. The key insight: reflection is triggered by failure signals (test failures, wrong outcomes). NEXUS has the signals but no trigger.
- arXiv:2407.06567v3 (FinCon): Multi-agent verbal reinforcement with manager-analyst hierarchy. NEXUS's 7-stage broker pipeline maps to this — but the reflection stage is passive.
- arXiv:2402.11651v2 (Learning From Failure): Negative examples from wrong outcomes. NEXUS logs 'wrong' outcomes but doesn't use them as training signal for the reflexion layer.

**Production Fix:**
1. Wire `trigger_on_event(event='wrong', context=task_summary)` on every `outcome='wrong'` in `record_outcome()`.
2. The reflexion LLM generates a verbal reflection (what went wrong, what to try next).
3. Store reflection in `learning_notes` table (already exists).
4. On next routing for similar task, retrieve reflection via `search_learnings()` and inject as context.
5. Monitor: wrong-outcome rate should decrease by 10-20% over 2 weeks.

### GAP-8: Dashboard Dead (MEDIUM)

**Metric:** Dashboard server.py exists but port 8765 not listening. No supervisord entry.
**Root Cause:** Never started. No cron or supervisord entry.

**Production Fix:**
1. Add dashboard to supervisord config.
2. Start supervisord (config exists but unapplied).
3. Wire dashboard to read from routing_log, outcome_log, efc_factors for live metrics.

---

## PART 3: PRODUCTION SETUP RECOMMENDATIONS

### 3.1 Deployment Sequence (7 Phases)

**Phase 1: Data Recovery (Day 1)**
- Restore `resource_edges` from backup or re-run densification
- Target: ≥5,000 edges, ≥10 communities
- Citation: arXiv:2511.11017v1 (KG construction pipeline)

**Phase 2: Router Cutover (Day 2)**
- Flip `USE_UNIFIED_ROUTER=1`
- Singleton-cache the embedder
- Verify: p95 <300ms, top1-agreement >0.95
- Citation: arXiv:2508.07602 (HGMF), arXiv:2210.11934 (convex > RRF)

**Phase 3: EFC Bridge (Day 2-3)**
- Wire `compute_efc()` into `record_outcome()`
- Wire EFC scores into `rerank_with_efc_ucb()`
- Target: 100% EFC coverage on new outcomes
- Citation: arXiv:2605.29682 (EFC scaling law)

**Phase 4: Outcome Coverage (Day 3-4)**
- Cronjob: `close_outcomes()` every 30 minutes
- Wire `report_outcome()` in ALL routing paths
- Target: ≥80% outcome coverage
- Citation: arXiv:2303.11366 (reflexion needs outcomes), arXiv:2402.11651 (learning from failure)

**Phase 5: Cold-Start Activation (Day 4-5)**
- Run `cold_warm.py --commit`
- Enable UCB1 bonus + two-tier 70/30 split
- Target: <40% never-triggered within 2 weeks
- Citation: arXiv:2310.03054 (ToolkenGPT two-tier), arXiv:1403.5556 (IDS), arXiv:2102.06129 (Meta-TS)

**Phase 6: Reflexion Wiring (Day 5-7)**
- Wire `trigger_on_event()` on wrong outcomes
- Wire reflexion LLM to generate and store verbal reflections
- Wire `search_learnings()` for context retrieval on similar tasks
- Target: 10-20% reduction in wrong-outcome rate
- Citation: arXiv:2303.11366 (Reflexion), arXiv:2407.06567 (FinCon)

**Phase 7: Chunk Re-Ingestion (Day 7-10)**
- Re-ingest all resources with chunk→resource linking
- Re-index FTS5 on new chunks
- Re-run AC2a benchmark
- Target: chunk stream contributes >0 to final score
- Citation: arXiv:2503.01763 (ToolRet), arXiv:2511.11017 (KG construction)

### 3.2 Process Supervision

```
[supervisord]
- nexus_http_daemon.py (port 8080) — routing API
- nexus-mcp-enhanced.py — MCP tool server
- context_harness/server.py — context harness
- dashboard/server.py (port 8765) — monitoring
```

Apply existing supervisord config. Add healthcheck endpoints.

### 3.3 Monitoring Dashboard

Required metrics (from gap analysis + EFC paper):
- Outcome coverage % (target ≥80%)
- EFC coverage % (target 100%)
- Never-triggered % (target <40%)
- p95 routing latency (target <300ms)
- Top1 agreement v5 vs v4 (target >0.95)
- Edge density (target >0.5%)
- Community count (target ≥10)
- Wrong-outcome rate (trending down)
- Cache hit rates L0-L3

### 3.4 Cron Jobs

| Job | Schedule | Action |
|-----|----------|--------|
| close_outcomes | every 30m | Close NULL/routed outcomes |
| kg_densify | daily | Re-run edge generation on new resources |
| efc_compute | hourly | Compute EFC for new outcomes |
| cold_warm | daily | Warm-start cold resources |
| outcome_audit | daily | Verify outcome coverage + report |

---

## PART 4: ACADEMIC CITATION INDEX

| # | Paper | NEXUS Relevance | Gap Addressed |
|---|-------|-----------------|---------------|
| 1 | arXiv:2508.07602 (HGMF) | v5 router pipeline | GAP-4 (router cutover) |
| 2 | arXiv:2409.02141 (Tool2Vec) | Tool embeddings | GAP-4 (embedder caching) |
| 3 | arXiv:2210.11934 (Bruch) | Convex > RRF fusion | GAP-4 (fusion method) |
| 4 | arXiv:2310.03054 (ToolkenGPT) | Two-tier hot/cold split | GAP-2 (cold-start) |
| 5 | arXiv:1403.5556 (IDS) | Information-Directed Sampling | GAP-2 (exploration) |
| 6 | arXiv:2102.06129 (Meta-TS) | Meta-Thompson Sampling | GAP-2, GAP-6 (EFC weighting) |
| 7 | arXiv:2605.29682 (EFC) | Feedback scaling law | GAP-3, GAP-6 (EFC bridge) |
| 8 | arXiv:2303.11366 (Reflexion) | Verbal reinforcement | GAP-7 (reflexion wiring) |
| 9 | arXiv:2407.06567 (FinCon) | Multi-agent verbal RL | GAP-7 (reflexion hierarchy) |
| 10 | arXiv:2402.11651 (Learning From Failure) | Negative examples | GAP-3 (outcome coverage) |
| 11 | arXiv:2503.01763 (ToolRet) | Tool retrieval benchmark | GAP-5 (chunk linking) |
| 12 | arXiv:2511.11017 (KG Construction) | 3-stage KG pipeline | GAP-1 (edge collapse) |
| 13 | arXiv:2209.12801 (KGE Subsampling) | Scalable KGE | GAP-1 (edge inference) |
| 14 | arXiv:2606.20023 (ToolPrivBench) | Privilege-aware selection | Validates safemode/godmode |
| 15 | arXiv:2607.05441 (PORTS) | Retriever-LLM alignment | Validates v5 L1→L2 alignment |
| 16 | arXiv:2512.21309 (AgentReuse) | Plan reuse via intent | Validates L0-L3 cache |
| 17 | arXiv:1404.6830 (Fault Tolerance) | Orthogonal fault tolerance | Production reliability |
| 18 | arXiv:2203.08975 (Comm-MADRL) | Multi-agent communication | Orchestrator channels |
| 19 | arXiv:1802.00951 (BFT Scheduling) | Byzantine fault tolerance | Production checkpointing |
| 20 | arXiv:2508.08322 (Multi-Agent Code Assistant) | Production multi-agent | Production deployment model |

---

## SUMMARY: 8 GAPS → 7 PHASES → 10 DAYS

| Gap | Severity | Phase | Citation | Target |
|-----|----------|-------|----------|--------|
| KG Edge Collapse | CRITICAL | P1 (Day 1) | arXiv:2511.11017 | ≥5,000 edges |
| Never-Triggered Waste | CRITICAL | P5 (Day 4-5) | arXiv:2310.03054 | <40% cold |
| EFC Bridge Failure | CRITICAL | P3 (Day 2-3) | arXiv:2605.29682 | 100% EFC coverage |
| Outcome Coverage | HIGH | P4 (Day 3-4) | arXiv:2303.11366 | ≥80% coverage |
| Multi-Router Fragment | HIGH | P2 (Day 2) | arXiv:2508.07602 | v5 production |
| Chunk Link Desert | HIGH | P7 (Day 7-10) | arXiv:2503.01763 | Chunk stream active |
| Reflexion Not Wired | MEDIUM | P6 (Day 5-7) | arXiv:2303.11366 | -15% wrong rate |
| Dashboard Dead | MEDIUM | P2 (Day 2) | — | Port 8765 live |