# NEXUS Multidimensional Audit Report
**Generated**: 2026-07-01  
**DB**: `/home/roshan/nexus/db/nexus.db` (492 MB, WAL mode, 74 tables)  
**Resources**: 2,849 total (1,920 active, 929 archived)

---

## 1. Core Routing Pipeline

**Status**: Functioning, recently patched

The 7-step pipeline (classify → auto_trigger → cluster_route → iso_scoring → gates → intent_classifier → thompson_sampling) is active. 7,436 cluster activations logged with 728ms average latency.

### Routing Accuracy Trend (per 1000 logs)
| Block | Correct | Wrong | Partial | Routed | Accuracy |
|-------|---------|-------|---------|--------|----------|
| 0 (oldest) | 865 | 16 | 119 | 0 | 86.5% |
| 1 | 480 | 520 | 0 | 0 | 48.0% |
| 2 | 907 | 89 | 4 | 0 | 90.7% |
| 3 | 936 | 30 | 34 | 0 | 93.6% |
| 4 | 651 | 9 | 340 | 0 | 65.1% |
| 5 | 18 | 0 | 982 | 0 | **1.8%** |
| 6 (newest) | 72 | 0 | 436 | 1 | 14.1% |

**Analysis**: Block 5 shows the feedback collapse (93% partial) that was fixed by changing `_evaluate_outcome` to return `'routed'` instead of `'partial'`. The fix has only generated 1 "routed" entry so far — it's too new to measure impact. Block 6's 14.1% accuracy is misleading because 436 of 509 entries are still legacy "partial" from before the code change.

**Overall**: 6,509 logs, 60.4% correct all-time, trending upward after feedback collapse fix.

### Failures
- `general_failure`: 275 (86.5% of reflection failures)
- `Max retries exhausted`: 11
- All others: <10 each

---

## 2. Learning System

### Thompson Sampling
- **5,080 model beliefs** across providers/combinations
- Belief structure: `(provider, model, task_type, alpha, beta, total_requests, total_reward, avg_reward)`
- `adaptive-imagining-cat` is top by triggers (463) with 97.1% success rate
- **0 broken counters** — the counter clamp fix (`times_correct = MIN(times_correct, times_triggered)`) is working perfectly

### Outcome Quality Discrepancy

| Source | Correct | Partial | Wrong | Total |
|--------|---------|---------|-------|-------|
| `routing_log` | 3,929 (60.4%) | 1,915 (29.4%) | 664 (10.2%) | 6,509 |
| `outcome_log` | 1,165 (41.4%) | 1,221 (43.4%) | 429 (15.2%) | 2,815 |

**Gap**: `outcome_log` reports 19 percentage points lower accuracy than `routing_log`. This may indicate:
- `outcome_log` uses harsher truth labels (ground truth verification)
- `routing_log` may over-report success (self-reported)
- Or the two tables serve different populations

### Cluster Affinity
- **174 affinity history entries**
- Values have flatlined in the 0.75–1.0 range
- Most recent entries show `delta=0.0` (routed outcomes produce no change)
- All clusters look equally "good" — affinity cannot differentiate

**Recommendation**: Introduce affinity decay factor. Unused clusters should drift downward over time to force re-exploration.

### Top-Performing Resources (by triggers)
| Resource | Type | Triggers | Success Rate |
|----------|------|----------|-------------|
| adaptive-imagining-cat | skill | 463 | 97.1% |
| careful | skill | 220 | 79.8% |
| investigate | skill | 204 | 98.0% |
| guard | skill | 186 | 83.6% |
| frontend-ui-engineering | skill | 121 | 100.0% |
| qa | skill | 118 | 100.0% |

**Note**: `frontend-ui-engineering` and `qa` both have 100% success rates over 100+ triggers — statistically suspicious. Possible outcome recording bias.

---

## 3. Context Harness + Academic Channels

### Built and Verified
- 9 RRF backends registered (4 scrapling + 5 academic)
- Academic channels: `arxiv_search` (w=1.5), `semantic_scholar` (w=1.3), `github_search` (w=1.0), `paperswithcode_search` (w=1.4), `google_scholar` (w=0.9)
- MCP tools: `harness_arxiv`, `harness_scholar_s2`, `harness_github_search`, `harness_paperswithcode`, `harness_research`
- Live verified: arXiv and GitHub return real results

### Gaps
- **`harness_research` NOT wired to NEXUS routing** — the router has no awareness of academic search capabilities. `route_task()` does not call `harness_research()` even for research queries.
- **S2 API returns 429 (rate-limited)** without `S2_API_KEY` — retry logic exists but won't help
- **Google Scholar parser broken** — HTML structure changed, returns 0 results
- Context harness runs as a separate MCP server (PID 153492), not integrated into the main routing pipeline

---

## 4. DB Health + Performance

| Metric | Value |
|--------|-------|
| DB Size | 492 MB |
| Journal Mode | WAL |
| Page Count | ~31,500 |
| Page Size | 4,096 bytes |
| WAL Size | 15.9 MB |

### Largest Tables
| Table | Rows |
|-------|------|
| `ingestion_chunks` | 88,354 |
| `chunk_embeddings` | 66,495 |
| `chunk_embed_cache` | 57,465 |
| `resource_sections` | 22,013 |
| `sections_fts` | 22,013 |

**Assessment**: Healthy for current scale. The 88K ingestion chunks + 66K embeddings are the primary space consumers. Consider pruning old embeddings (>90 days) and running VACUUM.

### Resource Composition
| Type | Total | Active | Inactive |
|------|-------|--------|----------|
| skill | 1,301 | 686 | 615 |
| mcp | 327 | 26 | 301 |
| command | 276 | 276 | 0 |
| doc | 193 | 191 | 2 |
| agent | 174 | 170 | 4 |
| design_system | 173 | 173 | 0 |
| cli_tool | 160 | 160 | 0 |
| spec | 156 | 149 | 7 |
| hook | 77 | 77 | 0 |
| craft | 12 | 12 | 0 |

**301 MCP resources are inactive** — these are from old/offline MCP servers. Purge them.

---

## 5. Resilience (Circuit Breaker + Ruflo Proxy)

### Circuit Breaker
- **Status**: Wired and working through `ruflo_proxy.py`
- Supports CLOSED → OPEN → HALF_OPEN transitions
- Sync `call_sync()` method available
- Decorator `@breaker_protected` functional

### Ruflo Proxy
- 238 tools proxied from ruflo MCP
- 5-second timeout on tool calls
- 30-second health ping interval
- Error codes: `isError`, `CIRCUIT_OPEN`, `TIMEOUT`
- Health check passes

**Assessment**: This is the **only** Phase 1 fix that is fully wired to runtime. Both async `acall_tool()` and sync `call_ruflo_tool()` use the breaker.

---

## 6. Reflection / Retry Loop

| Metric | Value |
|--------|-------|
| Reflection fixes | 2,476 |
| Reflection failures | 318 |
| Reflection log entries | **0** |
| Never-applied fixes | 2,476 (100%) |

### What's Wrong
- 2,476 fixes exist but **none have been applied** (all `times_applied=0`, `times_successful=0`)
- The reflection log is empty — no live reflection loop is running
- `NEXUS_REFLECTION=true` is in `.bashrc` but **not in running processes**

### Root Causes
| Root Cause | Count |
|------------|-------|
| general_failure | 275 |
| Max retries exhausted | 11 |
| syntax_error | 8 |
| missing_dependency | 8 |
| network_error | 7 |

**Assessment**: Reflection system is built but dormant. Needs `NEXUS_REFLECTION=true` + process restart. Without it, the 2,476 fixes are theoretical — they were generated by batch/manual runs, not by the real-time loop.

---

## 7. Config + Deployment Readiness

### Environment Variables
| Variable | .bashrc | Running Procs |
|----------|---------|---------------|
| `NEXUS_THOMPSON_ROUTING=true` | Yes | Unknown |
| `NEXUS_CLUSTER_ROUTING=true` | Yes | **NO** |
| `NEXUS_REFLECTION=true` | Yes | **NO** |

### Process State
- 3 MCP processes running (PIDs 3839, 55618, 153482) — all started **before** env var changes
- Supervisor (PID 275068) managing dashboard + http daemon, but its config lacks NEXUS env vars
- Context harness (PID 153492) running as separate MCP server

### What's Missing
1. **Supervisor config** doesn't pass NEXUS env vars to managed processes
2. **MCP processes** need restart to pick up cluster routing + reflection
3. No deployment pipeline — no CI/CD, no health check endpoints beyond `/health`

---

## 8. Phase 1 Fix Wiring Status

| Fix | File | Wired? | Status |
|-----|------|--------|--------|
| ARCH-6: Circuit Breaker | `server/resilience/circuit_breaker.py` | **YES** | Wired to `ruflo_proxy.py` |
| INT-1: Ruflo Proxy | `server/ruflo_proxy.py` | **YES** | Sync+async through breaker |
| PERF-2: Resource Validator | `server/maintenance/resource_validator.py` | **NO** | Orphaned — never imported |
| PERF-6: Cache Telemetry | `server/router_v2/cache_telemetry.py` | **PARTIAL** | Partially integrated in chunk_embedder, but LRU-K unused |
| INT-2: Rate Limited Proxy | `server/integrations/rate_limited_proxy.py` | **NO** | Orphaned — never imported by any consumer |
| CONN-4: SSE Reconnect | `server/connectivity/sse_reconnect.py` | **NO** | Solves non-existent problem — NEXUS has no SSE |
| COMM-3: Notifier | `server/communication/notifier.py` | **YES** | Wired to `learning.py:307` degrade path |

**Productivity score**: 2 of 7 fixes are delivering runtime value. SSE reconnect should be deleted (no SSE in architecture). Resource validator and rate limited proxy need wiring. Cache telemetry's LRU-K is over-engineered for the current cache size.

---

## 9. Gap Summary & Prioritized Recommendations

### CRITICAL (do immediately)
| # | Gap | Fix | Effort |
|---|-----|-----|--------|
| G1 | Cluster routing + reflection env vars not active | Add to supervisor config, restart processes | 0.5h |
| G2 | `harness_research` not wired to routing | Wire `harness_research()` call to `route_task()` for research queries | 2-4h |

### HIGH (this week)
| # | Gap | Fix | Effort |
|---|-----|-----|--------|
| G3 | 827 resources (43.1%) never triggered | Run periodic cold-start campaign, auto-archive after 90 days | 2-4h |
| G4 | Outcome log 41.4% vs routing 60.4% accuracy gap | Investigate discrepancy — is routing over-reporting? | 2-4h |
| G5 | 301 inactive MCP resources | Run resource_validator purge or archive via SQL | 0.5h |

### MEDIUM (next week)
| # | Gap | Fix | Effort |
|---|-----|-----|--------|
| G6 | Cluster affinity flatlined (0.75-1.0 range, all equal) | Add decay factor so unused clusters lose affinity | 2h |
| G7 | Warning signs: 2 resources at 100% over 100+ triggers | Audit outcome recording for bias | 1h |
| G8 | Legacy "partial" outcomes (1,915 entries) — fixed but not migrated | Run migration to reclassify old partials as routed | 0.5h |

### LOW (when convenient)
| # | Gap | Fix | Effort |
|---|-----|-----|--------|
| G9 | DB size growing (492 MB), WAL at 15.9 MB | VACUUM, prune old embeddings | 1h |
| G10 | SSE reconnect solves non-existent problem | Delete the file | 0.1h |
| G11 | Rate limited proxy not wired to any consumer | Wire to academic channel external API calls | 1-2h |

---

## 10. Honest Assessment

### What's Working
- **Counter integrity is fixed** — 0 broken counters down from 38. The MIN() clamp is effective.
- **Feedback collapse is fixed** — `_evaluate_outcome` now returns `'routed'` instead of `'partial'`. Too early to measure impact (1 entry so far).
- **Dead resources purged** — 268 zero-token resources archived. Active pool is clean.
- **Circuit breaker + notifier are wired** — these 2 of 7 Phase 1 fixes are delivering real runtime value.
- **Academic channels built and live-verified** — 5 channels working (arXiv, GitHub verified).

### What's Not Working
- **5 of 7 Phase 1 fixes are orphaned** — built but never wired to runtime. SSE reconnect is especially wasteful (solves a problem NEXUS doesn't have).
- **Reflection system is dormant** — 2,476 theoretical fixes, 0 applied. Needs env var activation.
- **harness_research is disconnected** — 5 academic channels running in a separate MCP server that the router doesn't know about.
- **Cluster affinity is non-functional** — all clusters sit at 0.75-1.0 with zero differentiation. Affinity cannot guide routing decisions.
- **Frontend-ui-engineering at 100% SR over 121 triggers** — this is not a real signal, it's an outcome recording bias.

### Bottom Line
The system is incrementally improving — counter bugs and feedback collapse were real problems that are now fixed. But the gap between "built" and "wired" is the biggest remaining issue. 5 academic channels and 4 orphaned library modules exist but deliver zero runtime value. The next 4-6 hours of work (wiring harness_research, activating env vars in supervisor config, fixing affinity) would deliver more real improvement than the last 20 hours of code-writing.
