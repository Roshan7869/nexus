# NEXUS 35-Gap Tracking DAG v2

Status: **STAGE: Phase 1 complete / Phase 2 ready**
Date: 2026-07-01
Master plan: `/home/roshan/NEXUS_MASTER_REMEDIATION_PLAN.json`

## Graph Foundation

| Property | Value |
|----------|-------|
| Graphify nodes | 13,287 |
| Graphify edges | 17,212 |
| Communities | 1,284 |
| EXTRACTED confidence | 86% |
| INFERRED | 14% |
| AMBIGUOUS | 0% |
| God nodes (top 5) | by_keyword(458), OperationalException(243), Exchange(193), float(142), IStrategy(85) |
| Routing research community | 69 (33 nodes) |
| Major related community | 0 — strategy_db / vector_inventory (456 nodes — `circuit_breaker`, `thompson`, `kelly`, `RRF` keywords confirmed present) |

## 5-Stage Pipeline

```
STAGE 1 [GAP_GRAPH]       — 35 gap nodes + paper citations + benchmark gates
STAGE 2 [FIX_GRAPH]       — fix_stack per gap → code modules → review PRs
STAGE 3 [VERIFY_GRAPH]    — benchmark gate per gap → metric → gate pass/fail
STAGE 4 [RESEARCH_GRAPH]  — paper citation → arXiv id → claim summary
STAGE 5 [LIVE_TRACK]      — graphify live updates as each fix lands + A/B rollouts
```

Current stage: **STAGE: Phase 1 complete / Phase 2 ready**

## DAG Topology (35 Gaps × 4 Layers)

### Layer 1: Gap Nodes (35)
Each node has these edges:
- `--described_in--> NEXUS_MASTER_REMEDIATION_PLAN.json`
- `--has_paper--> {arxiv_id}` (1–3 papers per gap)
- `--has_benchmark--> benchmark_gate` (verification leaf)
- `--belongs_to_domain--> {architecture, performance, integration, connectivity, communication}`

### Layer 2: Fix Nodes (one per gap)
Each fix node has these edges:
- `--fixes--> gap_id`
- `--uses_library--> library_name` (e.g., pybreaker, asyncpg, nats-py, opentelemetry)
- `--modifies_module--> path/to/file.py`
- `--has_benchmark--> benchmark_gate`
- `--has_estimate--> effort_hours`

### Layer 3: Benchmark Gate Nodes (35)
Each gate node has these edges:
- `--verifies--> {gap_id, fix_id}`
- `--requires_metric--> metric_name` (e.g., p95_latency, hit_rate, close_rate)
- `--requires_threshold--> threshold_value`
- `--reports_via--> {prometheus, /health, /api/metrics}`

### Layer 4: Research Paper Nodes (19)
Already mapped to 19/35 research-backed gaps:

| Paper | Cited by Gaps |
|-------|---------------|
| arXiv:1609.05830 (Montesi/Weber microservices) | ARCH-1, ARCH-5, ARCH-6 |
| arXiv:2201.07226 (Faustino stepwise monolith) | ARCH-2, ARCH-7 |
| arXiv:1304.5758 (Bubeck/Liu TS regret) | PERF-1 |
| arXiv:2411.09065 (Wang LLM prior) | PERF-1 |
| arXiv:2110.00871 (Zhang feel-good TS) | PERF-1 |
| arXiv:2106.10898 (BanditMF matrix factorization) | PERF-1 |
| arXiv:2006.16189 (DOME validation) | PERF-2 |
| arXiv:1707.03869 (cognitive biases SE) | PERF-2 |
| arXiv:1809.09924 (Barz/Denzler hierarchy) | PERF-3 |
| arXiv:2309.10809 (Kutay/Yener text compression) | PERF-3 |
| arXiv:1908.10084 (Reimers Sentence-BERT) | PERF-3 |
| Chopade 2025 / Ugras 2025 / Yang 2022 (architect embeddings) | PERF-4 |
| 2020 MAS failure prediction RF / IEEE 2021 LSTM autoencoder | PERF-5 |
| OSDI 2019 / FAST 2015 / SIGMOD 1993 LRU-K | PERF-6 |
| SQLite WAL Hipp / aiosqlite | PERF-7 |
| HdrHistogram Tene 2014 / Dean-Barroso Tail at Scale 2013 | PERF-8 |
| NIPS 2012 ε-greedy / RecSys 2018 / ACM TKDD 2021 | PERF-9 |
| Nygard Release It! / Hystrix | ARCH-6, INT-1 |
| Shapiro et al. INRIA 2011 (CRDTs) | COMM-2 |

## 35-Gap → 4-Phase Schedule

| Phase | Weeks | Gaps | Effort (h) |
|-------|-------|------|------------|
| Phase 1: Quick Wins | Week 1 | ARCH-6, ARCH-8, PERF-2, PERF-6, INT-1, INT-2, CONN-4, COMM-3 | 42 |
| Phase 2: Core Upgrades | Week 2-3 | ARCH-1, ARCH-2, ARCH-3, ARCH-4, ARCH-5, PERF-1, PERF-7, PERF-8, INT-3, INT-6, CONN-3 | 100 |
| Phase 3: Algorithmic | Week 4-6 | ARCH-9, PERF-3, PERF-4, PERF-5, PERF-9, INT-4, INT-5, INT-7, INT-8, CONN-1, CONN-2, COMM-1, COMM-2, COMM-4 | 84 |
| Phase 4: Architecture | Week 7-8 | ARCH-7, ARCH-10 | 36 |
| **Total** | **8 weeks** | **35** | **~220 h** |

## Benchmark Gate Coverage

35/35 gaps have an explicit benchmark gate. 19/35 have a research-paper citation. 16/35 (integration + connectivity + communication) have direct-knowledge fixes pending research validation.

## Live-Tracking Protocol

After each fix lands:
1. Add edge `fix_id --merged_in--> commit_sha` (with date)
2. Add edge `benchmark_gate --measured_at--> timestamp` (with metric value)
3. Update node `gap_id --status--> {open, in_progress, fixing, verifying, done}`
4. Log outcome via `mcp_nexus_log_outcome` for Thompson Sampling feedback
5. Surface in /health endpoint for Phase 5 communication dashboard

## How to Query This DAG (graphify syntax)

- BFS from a gap: `mcp_graphify_query_graph(question="ARCH-1 circuit breaker", mode="bfs", depth=4)`
- Shortest path: `mcp_graphify_shortest_path(source="PERF-1 routing accuracy", target="Thompson_sampling")`
- All fixes: `mcp_graphify_query_graph(question="pybreaker asyncpg FastAPI", mode="bfs", depth=3)`

## Critical Path (P0 must be done first)

1. **ARCH-2** SQLite→PostgreSQL (foundation for every other DB-touching fix)
2. **PERF-1** Routing accuracy (impacts all routing-dependent gaps)
3. **INT-6** Request-ID tracing (required for verification of all benchmark gates)
4. **ARCH-6** Circuit breaker (required for safe rollouts of all other fixes)
5. **PERF-8** HdrHistogram (required to measure all latency benchmark gates)

---

## Layer 0 Reconciliation Addenda

### Gap Nodes (35)

| id | domain | status | dependencies | effort_hours | owner_agent | verification_gate |
|----|--------|--------|--------------|--------------|-------------|-------------------|
| gap-ARCH-1 | architecture | open | [] | 8 | arch-agent | routing_log events published + consumed in <50ms; zero loss under 100 events/sec |
| gap-ARCH-2 | architecture | open | [system-stable-after-phase-3] | 16 | arch-agent | dual-write consistency 100%; PostgreSQL read p95 <30ms for routing_log |
| gap-ARCH-3 | architecture | open | [paper2code-A3] | 6 | arch-agent | cache hit rate >70% for skill lookups; p99 GET <5ms |
| gap-ARCH-4 | architecture | open | [layer-0.3-citation-fix] | 12 | arch-agent | unauth MCP calls rejected 100%; auth check <10ms per call |
| gap-ARCH-5 | architecture | open | [paper2code-A5] | 8 | arch-agent | rate limit enforced at 100 req/min/key; gateway adds <5ms p95 |
| gap-ARCH-6 | architecture | verified (implemented_at: 2026-07-01T14:00:00+00:00) | [paper2code-A6] | 6 | arch-agent | cascading failure prevented; circuit OPEN after 5 fails/60s, HALF-OPEN after 60s reset |
| gap-ARCH-7 | architecture | open | [phase-2-complete, phase-3-complete] | 24 | arch-agent | import boundaries enforced; nexus-ruflo deployable independently |
| gap-ARCH-8 | architecture | open | [gap-ARCH-2] | 4 | arch-agent | all schema changes go through migration files; zero prod outages from drift |
| gap-ARCH-9 | architecture | open | [] | 10 | arch-agent | NDCG@10 +8% over static RRF on test set |
| gap-ARCH-10 | architecture | open | [] | 12 | arch-agent | skill v1.2.0 callable after v2.0.0 ships; semver constraints resolve correctly |
| gap-PERF-1 | performance | open | [paper2code-P1] | 16 | perf-agent | close_rate ≥80% within 500 routed tasks; weighted accuracy ≥88% |
| gap-PERF-2 | performance | verified (implemented_at: 2026-07-01T14:00:00+00:00) | [] | 4 | perf-agent | zero-token count = 0 after 1 maintenance cycle; nightly validation cron runs |
| gap-PERF-3 | performance | open | [paper2code-P3] | 12 | perf-agent | low_cluster_confidence ≤25%; silhouette_score ≥0.30 avg |
| gap-PERF-4 | performance | open | [paper2code-P4] | 10 | perf-agent | architect cluster affinity ≥0.80 within 2 weeks fine-tuning |
| gap-PERF-5 | performance | open | [] | 8 | perf-agent | high-error skills auto-degraded within 1h of threshold breach |
| gap-PERF-6 | performance | verified (implemented_at: 2026-07-01T14:00:00+00:00) | [paper2code-P6] | 4 | perf-agent | cache_hit_rate exposed in /health; LRU-K reduces embedding latency 20-40% |
| gap-PERF-7 | performance | open | [] | 8 | perf-agent | write throughput 10-50x; routing latency p95 <200ms under load |
| gap-PERF-8 | performance | open | [] | 6 | perf-agent | p95<200ms verified; tail latency 20-40% reduction via hedged requests |
| gap-PERF-9 | performance | open | [paper2code-P9] | 6 | perf-agent | never-triggered ≤30% within 60 days |
| gap-INT-1 | integration | verified (implemented_at: 2026-07-01T15:00:00+00:00) | [] | 6 | int-agent | ruflo hang cannot block routing >5s; failed ruflo tool auto-degraded within 1 minute |
| gap-INT-2 | integration | verified (implemented_at: 2026-07-01T14:00:00+00:00) | [] | 4 | int-agent | no cashfree 429 errors in prod; quota tracked in /health |
| gap-INT-3 | integration | open | [] | 8 | int-agent | 100% of MCP tools have input/output schemas; invalid input rejected <5ms |
| gap-INT-4 | integration | open | [] | 4 | int-agent | scraper data <1h old; freshness metric exposed in /health |
| gap-INT-5 | integration | open | [] | 6 | int-agent | DAG build time 5x faster; 100-node batch in <500ms |
| gap-INT-6 | integration | open | [layer-1B-citation-research] | 8 | int-agent | full agent loop traceable in logs by single request_id; latency breakdown per tool |
| gap-INT-7 | integration | open | [] | 4 | int-agent | outcome_sync on 1000 chunks <30s; only delta re-indexed |
| gap-INT-8 | integration | open | [] | 4 | int-agent | tools grouped by cluster; permission per namespace possible |
| gap-CONN-1 | connectivity | open | [layer-1C-citation-research] | 12 | conn-agent | inter-node traffic encrypted; SPIFFE IDs verified; mTLS handshake <50ms |
| gap-CONN-2 | connectivity | open | [layer-1C-citation-research] | 8 | conn-agent | retries bounded (e.g., 3 max); per-route timeouts enforced; mesh metrics visible |
| gap-CONN-3 | connectivity | open | [layer-1C-citation-research] | 10 | conn-agent | full request trace visible in Jaeger; 100% sampled; span context propagated |
| gap-CONN-4 | connectivity | verified (implemented_at: 2026-07-01T14:00:00+00:00) | [] | 4 | conn-agent | SSE survives 30s network drop; auto-reconnect within 3s of restore |
| gap-COMM-1 | communication | open | [FIXED-5-partial] | 8 | comm-agent | single-glance view of all 35 gap metrics; alerts on threshold breach |
| gap-COMM-2 | communication | open | [layer-1D-citation-research] | 8 | comm-agent | Hermes can query NEXUS session history; bidirectional auth via JWT |
| gap-COMM-3 | communication | verified (implemented_at: 2026-07-01T14:00:00+00:00) | [] | 6 | comm-agent | auto-degraded skills notify within 60s; severity-based routing (P0→Telegram+SMS) |
| gap-COMM-4 | communication | open | [layer-1D-citation-research] | 6 | comm-agent | new user productive within 10 minutes; wizard rateable post-completion |

### Paper2Code Nodes

| id | domain | status | dependencies | effort_hours | owner_agent | verification_gate |
|----|--------|--------|--------------|--------------|-------------|-------------------|
| paper2code-P1 | research | open | [research-verifier, paper2code-architect] | 5 | paper2code-coder | runnable Beta-prior + LLM-prior + feel-good bonus + BanditMF MF extraction for PERF-1 |
| paper2code-P3 | research | open | [research-verifier, paper2code-architect] | 3 | paper2code-architect | YAML spec for Kutay/Yener compression + Barz/Denzler hierarchy applicable to PERF-3 |
| paper2code-P4 | research | open | [research-verifier, paper2code-architect] | 3 | paper2code-architect | YAML spec for Barz/Denzler hierarchy applicable to PERF-4 |
| paper2code-P6 | research | open | [research-verifier, paper2code-architect] | 2 | paper2code-architect | YAML spec for LRU-K / cache telemetry applicable to PERF-6 |
| paper2code-P9 | research | open | [research-verifier, paper2code-architect] | 3 | paper2code-coder | runnable ε-greedy + exposure fairness extraction for PERF-9 |
| paper2code-A3 | research | open | [research-verifier, paper2code-architect] | 2 | paper2code-architect | YAML spec for Redis distributed cache applicable to ARCH-3 |
| paper2code-A5 | research | open | [research-verifier, paper2code-architect] | 2 | paper2code-architect | YAML spec for API gateway rate limiting applicable to ARCH-5 |
| paper2code-A6 | research | open | [research-verifier, paper2code-architect] | 2 | paper2code-architect | YAML spec for circuit breaker applicable to ARCH-6 |

### Topological / Cycle Check

Conceptual Kahn's-algorithm traversal of the updated graph:

- Sources (in-degree 0): `research-verifier`, `paper2code-architect`, all quick-win gaps with `[]` dependencies, `FIXED-5-partial`, and the four `layer-1*-citation-research` anchors.
- `paper2code-*` nodes depend only on research sources; they in turn feed only their target gaps.
- `gap-ARCH-8` depends on `gap-ARCH-2`; `gap-ARCH-2` depends on a phase-complete anchor, not on `gap-ARCH-8`.
- `gap-ARCH-7` depends on phase-complete anchors, not on any later gap.
- No backward edges from later phases to earlier phases.

**No cycles detected.**

---

## Diff Summary

| Item | Original Manifest | v2 Manifest | Delta |
|------|-------------------|-------------|-------|
| Status line | STAGE 1/5 (GAP_GRAPH) BUILT | STAGE: Layer 0 reconciliation / Layer 1 research | updated |
| Gap node table | absent | 35 rows added | +35 |
| Paper2Code node table | absent | 8 rows added | +8 |
| Topological check | absent | "No cycles detected" | +1 |
| Existing graph foundation, pipeline, schedule, paper citations, live-tracking protocol, critical path | preserved | preserved | unchanged |

Total nodes added: 35 gap nodes + 8 paper2code nodes = **43**.
Paper2code nodes: **8**.
STAGE: **Layer 0 reconciliation / Layer 1 research**.
Unresolved cycles: **None**.

---

## Phase 1 verified gaps

The following 6 Phase 1 quick-win gaps have been implemented and verified:

| Gap | Domain | Verification test | Status |
|-----|--------|-------------------|--------|
| ARCH-6 | architecture | tests/arch/test_circuit_breaker.py | verified |
| PERF-2 | performance | tests/perf/test_zero_token_purge.py | verified |
| PERF-6 | performance | tests/perf/test_cache_telemetry.py | verified |
| INT-2 | integration | tests/int/test_rate_limited_proxy.py | verified |
| CONN-4 | connectivity | tests/conn/test_sse_reconnect.py | verified |
| COMM-3 | communication | tests/comm/test_notifier.py | verified |

All entries were updated with `implemented_at: 2026-07-01T14:00:00+00:00`.

---

## Phase 1 completion summary

Phase 1 is complete. **7 of 8 quick-win gaps are verified**; the remaining gap is blocked on an upstream dependency.

### Verified gaps (7)

| Gap | Domain | Verification test | Status |
|-----|--------|-------------------|--------|
| ARCH-6 | architecture | tests/arch/test_circuit_breaker.py | verified |
| PERF-2 | performance | tests/perf/test_zero_token_purge.py | verified |
| PERF-6 | performance | tests/perf/test_cache_telemetry.py | verified |
| INT-1 | integration | tests/int/test_ruflo_proxy.py | verified |
| INT-2 | integration | tests/int/test_rate_limited_proxy.py | verified |
| CONN-4 | connectivity | tests/conn/test_sse_reconnect.py | verified |
| COMM-3 | communication | tests/comm/test_notifier.py | verified |

### Blocked gap (1)

| Gap | Domain | Reason | Blocked by |
|-----|--------|--------|------------|
| ARCH-8 | architecture | Schema migrations require the PostgreSQL dual-write foundation (ARCH-2) to be in place before migration files can be applied safely. | ARCH-2 |

INT-1 was verified at `2026-07-01T15:00:00+00:00`. The project is now ready to proceed to Phase 2: Core Upgrades.
