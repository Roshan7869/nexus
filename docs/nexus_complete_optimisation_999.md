# NEXUS Complete Optimisation 999 — Execution Plan

**Generated:** 2026-07-01  
**Scope:** Reconcile and execute the 35-gap NEXUS remediation plan using Ruflo specialised swarm agents.  
**Files used as input:**
- `/home/roshan/NEXUS_MASTER_REMEDIATION_PLAN.json`
- `/home/roshan/NEXUS_DAG_MANIFEST.md`
- `/home/roshan/NEXUS_BENCHMARK_GATE_VERIFICATION.md`
- `/home/roshan/nexus_architecture_gaps_research.json`
- `/home/roshan/nexus_research/NEXUS_PERFORMANCE_GAPS_RESEARCH_REPORT.json`

---

## Executive Summary

| Item | Value |
|------|-------|
| Total gaps | 35 |
| Already fixed today | 5 (KG edges, KG communities, harness→NEXUS bridge, outcome coverage, supervisord + dashboard) |
| Pass / Conditional / Blocked | 17 / 18 / 0 |
| Total effort | **321 hours (~8 weeks @ 40h/week)** |
| Swarm topology | Hierarchical-mesh with domain-specialised agents |

---

## 0. Critical Findings from Analysis

1. **29/35 gaps are BLOCKED** — `code_change` field is prose, not valid Python.
2. **4 different effort totals** in the source files:
   - 286h (sum of `effort_hours` in JSON)
   - 220h (`summary.total_effort_hours`)
   - ~160h (`meta.estimated_total_effort`)
   - 262h (sum of DAG manifest phases)
3. **3 placeholder arXiv IDs** (`xxxxx`) in ARCH-3, ARCH-4, ARCH-4.
4. **5 fixes already applied today** are not represented in the 35-gap plan.
5. **18/35 gaps are CONDITIONAL** — missing real research citations.
6. **Domain count mismatch** — JSON says INT=8/CONN=4/COMM=4; verification table says INT=6/CONN=5/COMM=5.
7. **Severity count mismatch** — JSON: high=13; summary: high=14.

---

## 1. Execution Layers

```
Layer 0: RECONCILE (4h)  → fix the 5 files themselves
    ↓
Layer 1: RESEARCH (15h)  → paper2code + citation research
    ↓
Layer 2: EXECUTE Phase 1-4 (262h)
    ↓
Layer 3: VERIFY (40h)
    ↓
Layer 4: LIVE TRACK (ongoing)
```

---

## 2. Layer 0 — Reconcile Source Files (4h)

**Goal:** Produce a single source of truth before any code changes.

| Task | Input File | Change | Owner Agent |
|------|------------|--------|-------------|
| 0.1 Fix effort math | `NEXUS_MASTER_REMEDIATION_PLAN.json` | `summary.total_effort_hours` 220 → **286**; update meta to `286h implementation + 40h validation` | `plan-reconciler` |
| 0.2 Fix domain counts | `NEXUS_BENCHMARK_GATE_VERIFICATION.md` | INT=8, CONN=4, COMM=4; recount scores | `plan-reconciler` |
| 0.3 Replace placeholder arXiv IDs | `NEXUS_MASTER_REMEDIATION_PLAN.json` | ARCH-3, ARCH-4×2: replace `xxxxx` with real papers | `research-verifier` |
| 0.4 Convert 29 prose code_change → Python specs | `NEXUS_MASTER_REMEDIATION_PLAN.json` | Output structured `{file, function, change_type, diff}` objects | `code-spec-writer` |
| 0.5 Add 5 ALREADY-FIXED items | `NEXUS_MASTER_REMEDIATION_PLAN.json` | Add `FIXED-1..5` entries with `status: done` | `plan-reconciler` |
| 0.6 Add paper2code prereqs | `NEXUS_MASTER_REMEDIATION_PLAN.json` | Link PERF-1/3/4 to 5 arXiv papers | `plan-reconciler` |
| 0.7 Fix severity counts | `NEXUS_MASTER_REMEDIATION_PLAN.json` | high=13, low=6 | `plan-reconciler` |
| 0.8 Update DAG manifest | `NEXUS_DAG_MANIFEST.md` | STAGE 1 → STAGE 2; add FIXED + paper2code nodes | `dag-updater` |

### 5 Already-Fixed Items to Add

| ID | Fix | Live DB Evidence |
|----|-----|------------------|
| `FIXED-1` | KG edges restored from backup | `resource_edges`: 5 → **2,797** |
| `FIXED-2` | KG communities detected via Louvain | `kg_communities`: 0 → **24**; `kg_community_membership`: 0 → **1,404** |
| `FIXED-3` | Context-harness → NEXUS outcome bridge | `routing_log.skill_name LIKE 'harness:%'`: 0 → **1**; resource counters updated |
| `FIXED-4` | Outcome coverage auto-close | Stale NULL/routed → **0**; coverage **100%** (6,460/6,460) |
| `FIXED-5` | Supervisord + dashboard live | `:8765` listening; supervisord managing 5 programs |

---

## 3. Layer 1 — Research Sprint (15h)

### 3A. Paper2Code Algorithm Extraction (5h)

| # | Paper | arXiv ID | Target Gap | Depth | Output |
|---|-------|----------|------------|-------|--------|
| 1 | Bubeck/Liu — TS regret bounds | 1304.5758 | PERF-1 | **Phase 1-4** (runnable code) | `/research/paper_extractions/01_bubeck_liu_ts/` |
| 2 | BanditMF — MAB + CF cold-start | 2106.10898 | PERF-1, PERF-9 | **Phase 1-4** (runnable code) | `/research/paper_extractions/02_banditmf_coldstart/` |
| 3 | Kutay/Yener — Semantic text compression | 2309.10809 | PERF-3 | Phase 1 only (YAML spec) | `/research/paper_extractions/03_kutay_yener_compression/` |
| 4 | Barz/Denzler — Embedding hierarchy | 1809.09924 | PERF-3, PERF-4 | Phase 1 only (YAML spec) | `/research/paper_extractions/04_barz_denzler_hierarchy/` |
| 5 | Wang — LLM-as-prior | 2411.09065 | PERF-1 | Phase 1 only (YAML spec) | `/research/paper_extractions/05_wang_llm_prior/` |

### 3B. Citation Research for 18 CONDITIONAL Gaps (10h)

| Track | Gaps | Time | Agent |
|-------|------|------|-------|
| INT papers | INT-1, INT-2, INT-3, INT-6 | 4h | `int-researcher` |
| CONN papers | CONN-1, CONN-2, CONN-3, CONN-4 | 3h | `conn-researcher` |
| COMM papers | COMM-1, COMM-2, COMM-3, COMM-4 | 3h | `comm-researcher` |

---

## 4. Layer 2 — Execute 35 Gaps

### Phase 1: Quick Wins (42h, Week 1)

| Gap | Title | Effort | Owner Agent | Prereq |
|-----|-------|--------|-------------|--------|
| ARCH-6 | Circuit breaker (pybreaker + Redis) | 6h | `arch-agent` | None — P0 |
| PERF-2 | Zero-token resource purge | 6h | `perf-agent` | None |
| PERF-6 | Cache telemetry (hit/miss counters) | 4h | `perf-agent` | None |
| INT-1 | MCP streaming (pybreaker + timeout) | 6h | `int-agent` | None |
| INT-2 | Hierarchical routing (token bucket) | 6h | `int-agent` | None |
| CONN-4 | Latency detection (SSE reconnect) | 5h | `conn-agent` | None |
| COMM-3 | WebSocket push (PagerDuty) | 5h | `comm-agent` | None |

*Note: ARCH-8 deferred to Phase 4 because it depends on ARCH-2.*

### Phase 2: Core Upgrades (88h, Week 2-3)

| Gap | Title | Effort | Owner Agent | Prereq |
|-----|-------|--------|-------------|--------|
| ARCH-1 | Event bus (NATS + JetStream) | 8h | `arch-agent` | None |
| ARCH-3 | Distributed cache (Redis 7+) | 8h | `arch-agent` | Layer 0.3 |
| ARCH-4 | Auth/AuthZ (FastAPI OAuth2 + JWT) | 10h | `arch-agent` | Layer 0.3 |
| ARCH-5 | API gateway (Traefik + slowapi) | 8h | `arch-agent` | None |
| PERF-1 | Routing accuracy 60.6% → 85% | 12h | `perf-agent` | Paper2Code 1A+ (Bubeck/Liu + BanditMF) |
| PERF-7 | SQLite WAL + aiosqlite (STOPGAP) | 8h | `perf-agent` | None — replaces ARCH-2 for now |
| PERF-8 | HdrHistogram latency | 6h | `perf-agent` | None — P0 |
| INT-3 | Schema validation (Pydantic V2) | 8h | `int-agent` | None |
| INT-6 | Request-ID tracing (OpenTelemetry) | 10h | `int-agent` | Layer 1B |
| CONN-3 | API quota management | 10h | `conn-agent` | Layer 1C |

### Phase 3: Algorithmic (84h, Week 4-6)

| Gap | Title | Effort | Owner Agent | Prereq |
|-----|-------|--------|-------------|--------|
| ARCH-9 | Dynamic RRF weights | 8h | `arch-agent` | None |
| PERF-3 | Cluster confidence 41% → 70% | 10h | `perf-agent` | Paper2Code 1A (Kutay/Yener + Barz/Denzler) |
| PERF-4 | Architect affinity 0.697 → 0.80 | 8h | `perf-agent` | Paper2Code 1A (Barz/Denzler) |
| PERF-5 | High-error skill degradation | 6h | `perf-agent` | None |
| PERF-9 | Never-triggered 59% → 30% | 8h | `perf-agent` | Paper2Code 1A+ (BanditMF) |
| INT-4 | RRF fusion (max-age caching) | 5h | `int-agent` | None |
| INT-5 | Graphify batch ops | 6h | `int-agent` | None |
| INT-7 | ChromaDB incremental upsert | 5h | `int-agent` | None |
| INT-8 | MCP tool annotations | 5h | `int-agent` | None |
| CONN-1 | WireGuard/mTLS (SPIFFE) | 6h | `conn-agent` | Layer 1C |
| CONN-2 | SSH fallback (Linkerd) | 6h | `conn-agent` | Layer 1C |
| COMM-1 | Dashboard (Prometheus + Grafana) | 5h | `comm-agent` | FIXED-5 already partial |
| COMM-2 | Slack/Discord (CRDT) | 6h | `comm-agent` | Layer 1D |
| COMM-4 | Audit log | 6h | `comm-agent` | Layer 1D |

### Phase 4: Architecture (48h, Week 7-8)

| Gap | Title | Effort | Owner Agent | Prereq |
|-----|-------|--------|-------------|--------|
| ARCH-2 | SQLite → PostgreSQL (asyncpg) | 12h | `arch-agent` | System stable after Phases 1-3 |
| ARCH-7 | Modular monolith (package boundaries) | 18h | `arch-agent` | All Phase 2-3 complete |
| ARCH-10 | Resource versioning (Git + semver) | 18h | `arch-agent` | None |

---

## 5. Layer 3 — Verify (40h)

Every gap must pass its `benchmark_gate`:
- Metric meets threshold
- `/health` endpoint exposes the metric
- Prometheus scrapes it (after COMM-1)

Owner agent: `qa-verifier`

---

## 6. Layer 4 — Live Track (ongoing)

After each fix:
1. Update `NEXUS_DAG_MANIFEST.md` — move gap STAGE 2 → STAGE 3 → STAGE 4 → STAGE 5.
2. Call `nexus_log_outcome(skill_name, outcome)` for Thompson Sampling.
3. Update `/health` endpoint.
4. Graphify live update: `fix_id --merged_in--> commit_sha`.

Owner agent: `integration-tracker`

---

## 7. Ruflo Swarm Design

### Proposed Agent Roster (13 specialised agents)

| Agent | Domain | Responsibility |
|-------|--------|----------------|
| `swarm-coordinator` | Meta | Orchestrates layers, resolves dependencies, reports progress |
| `plan-reconciler` | Plan | Layer 0: fixes inconsistencies in the 5 source files |
| `research-verifier` | Research | Validates and replaces placeholder citations |
| `code-spec-writer` | Plan | Converts 29 prose code_change fields to Python specs |
| `dag-updater` | Plan | Updates DAG manifest as fixes land |
| `paper2code-architect` | Research | Paper2Code Phase 1 for all 5 papers |
| `paper2code-coder` | Code | Paper2Code Phase 1-4 for Bubeck/Liu + BanditMF |
| `arch-agent` | ARCH | All 10 architecture gaps |
| `perf-agent` | PERF | All 9 performance gaps |
| `int-agent` | INT | All 8 integration gaps |
| `conn-agent` | CONN | All 4 connectivity gaps |
| `comm-agent` | COMM | All 4 communication gaps |
| `qa-verifier` | QA | Layer 3 benchmark gates |
| `integration-tracker` | Integration | Layer 4 live tracking + outcome logging |

### Topology

```
                    swarm-coordinator
                           |
      ┌────────────┬───────┴───────┬────────────┐
      │            │               │            │
plan-reconciler  research    paper2code-*    qa-verifier
      │            │               │            │
      └────────────┴───────┬───────┴────────────┘
                           │
            ┌──────────────┼──────────────┐
            │              │              │
       arch-agent    perf-agent    int-agent
       conn-agent    comm-agent    integration-tracker
```

### Communication Protocol

- Named message passing via `SendMessage` (per CLAUDE.md Agent Comms).
- Each agent reports completion to `swarm-coordinator`.
- `swarm-coordinator` gates progression between layers/phases.

---

## 8. Total Effort Reconciliation

| Layer | Hours | Notes |
|-------|-------|-------|
| Layer 0 — Reconcile | 4h | Fix 5 files |
| Layer 1 — Research | 15h | Paper2Code + citations |
| Phase 1 — Quick wins | 42h | 7 gaps |
| Phase 2 — Core upgrades | 88h | 10 gaps (ARCH-2 deferred) |
| Phase 3 — Algorithmic | 84h | 14 gaps |
| Phase 4 — Architecture | 48h | 3 gaps (ARCH-2 included) |
| Layer 3 — Verify | 40h | 35 benchmark gates |
| **Total** | **321h** | ~8 weeks at 40h/week |

---

## 9. First Action

**Layer 0 starts immediately.** The `plan-reconciler` agent edits the 5 source files to produce a single, internally consistent execution spec. No system processes are touched — zero risk.
