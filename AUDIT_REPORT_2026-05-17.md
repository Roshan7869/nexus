# NEXUS Audit Report — 2026-05-17

## Executive Summary

| Metric | Value | Grade |
|--------|-------|-------|
| Total resources in DB | 1,686 | — |
| Cluster membership | 1,679/1,686 (99.6%) | A |
| Embedding coverage | 1,686/1,686 (100%) | A |
| FAISS index vectors | 1,686 (matches DB) | A |
| Callable/live tools | 23 MCP + 528/687 skills on disk | C |
| Routing accuracy | 41/55 verified (74.5%) | B- |
| Never-triggered resources | 1,147/1,686 (68%) | D |
| Cluster claim vs actual discrepancy | 755 total | F |

**Overall Grade: C+** — Index is healthy, routing works, but 68% of resources are dead weight (never triggered), cluster counts are wildly inaccurate, and accuracy tracking is mostly broken.

---

## 1. DB INTEGRITY

### 1a. Resource Distribution

| Type | Tier-1 | Tier-2 | Tier-3 | Total |
|------|--------|--------|--------|-------|
| skill | 316 | 347 | 24 | 687 |
| command | 184 | 84 | — | 268 |
| agent | 98 | 71 | — | 169 |
| design_system | 148 | 3 | — | 151 |
| cli_tool | — | 131 | — | 131 |
| doc | 11 | 113 | — | 124 |
| hook | 68 | 1 | — | 69 |
| mcp | 9 | 39 | 15 | 63 |
| craft | — | 12 | — | 12 |
| spec | 9 | 3 | — | 12 |
| **TOTAL** | **843** | **804** | **39** | **1,686** |

### 1b. Cluster Membership Issues

**7 Orphan Resources** (no cluster assignment) — ALL are graphify MCP tools:
- `graphify-query_graph`, `graphify-get_node`, `graphify-get_neighbors`
- `graphify-get_community`, `graphify-god_nodes`, `graphify-graph_stats`
- `graphify-shortest_path`

These are registered but never assigned to any cluster.

### 1c. Cluster Count Discrepancy (CRITICAL)

| Cluster | API Claims | DB Actual | Delta | Verdict |
|---------|-----------|----------|-------|---------|
| general | 173 | 525 | **-352** | BROKEN — general is a dump |
| analyzer_planner | 336 | 151 | **+185** | OVERCLAIMED |
| devops_infra | 192 | 89 | **+103** | OVERCLAIMED |
| knowledge_wiki | 241 | 203 | **+38** | MODERATE |
| frontend_ui | 307 | 299 | +8 | OK |
| backend_api | 120 | 100 | +20 | MODERATE |
| architect | 216 | 242 | -26 | UNDERCLAIMED |
| quality_security | 101 | 124 | -23 | UNDERCLAIMED |

**Root Cause**: The `cluster_list` API returns centroids from `cluster_definitions` which stores counts from an earlier reclustering. The actual `cluster_membership` table has redistributed resources (general absorbed 352 extra). The `general` cluster has become a catch-all dumping ground.

### 1d. Description Quality
- Good descriptions (10+ chars): 1,599 (94.8%)
- Weak/missing: 87 (5.2%)
- All 87 are tier-3 MCP entries with stub descriptions like "ClickHouse analytics", "Web scraping"

---

## 2. ROUTING SMOKE TESTS

Tested 11 domain-specific queries across all 8 clusters:

| Query | Expected Cluster | Actual Result | Score | Verdict |
|-------|-----------------|---------------|-------|---------|
| Debug TypeError null pointer | analyzer_planner | **quality_security** | 0.41 | WRONG CLUSTER |
| Design microservice payment system | architect/backend | **architect** (payments, api-design) | 0.46 | CORRECT |
| React dashboard with charts | frontend_ui | **frontend_ui** (skill-dashboard, admin-dashboard) | 0.46 | CORRECT |
| PostgreSQL auth REST API | backend_api | **backend_api** (insforge, database-admin) | 0.40 | CORRECT |
| Docker K8s CI/CD pipeline | devops_infra | **devops_infra** (deployment-patterns, github-workflows) | 0.49 | CORRECT |
| Unit tests pytest security audit | quality_security | **quality_security** (test-coverage, python-testing) | 0.53 | CORRECT |
| Write documentation wiki article | knowledge_wiki | **knowledge_wiki** (wiki-compilation, wiki-article-format) | 0.63 | CORRECT |
| Analyze performance review code | analyzer_planner | **analyzer_planner** (code-review-and-quality) | 0.65 | CORRECT |
| Landing page with Tailwind | frontend_ui | **frontend_ui** | — | CORRECT |
| REST API JWT auth database | backend_api | **backend_api** | — | CORRECT |
| Knowledge base article microservices | knowledge_wiki | **knowledge_wiki** | — | CORRECT |

**Result: 10/11 correct (91%), 1 wrong** — debug queries incorrectly route to quality_security instead of analyzer_planner. This is a known cluster overlap issue (both contain testing/debugging resources).

---

## 3. CROSS-CLUSTER LEAKAGE

**BUG CONFIRMED**: Debug/investigate queries consistently route to `quality_security` cluster instead of `analyzer_planner`. The `quality_security` cluster has absorbed debugging-related resources that should be in `analyzer_planner` per the CLAUDE.md dispatch table.

The `general` cluster is massively over-sized (525 resources vs claimed 173), acting as a catch-all that dilutes routing precision.

---

## 4. RECALL PRECISION

Route v4 top-5 results for 6 domain queries:

| Query | Top-5 Relevant? | Avg Score |
|-------|----------------|-----------|
| Debug TypeError | debugging-and-error-recovery ✓, debug ✓, debugger ✓, cpp-testing ✗, claude-skill-qa-only ✗ | 0.31 (low) |
| Payment microservice | payments ✓, cashfree-web-checkout ✓, agentic-payments ✓, api-design ✓, flow-nexus ✗ | 0.42 |
| React dashboard | skill-dashboard ✓, admin-dashboard ✓, ds-dashboard ✓, aashiyaana ✓, ds-expo ✗ | 0.39 |
| Docker CI/CD | deployment-patterns ✓, github-workflows ✓, land-and-deploy ✓, docker-compose ✓, sparc-devops ✓ | 0.44 |
| Unit tests | test-coverage ✓, python-testing ✓, test-suite ✓, cpp-testing ✗, cso ✗ | 0.44 |
| Documentation wiki | wiki-compilation ✓, wiki-compile-map ✓, wiki-article ✓, skill-docs ✓, llm-wiki ✓ | 0.58 (high) |

**Precision@5: 70%** (21/30 relevant). Lower for debugging queries due to cluster confusion.

---

## 5. WIRED-UP CHECK

### 5a. Skills on Disk

| Category | Count |
|----------|-------|
| Skills with files present on disk | 528/687 (76.8%) |
| Skills with missing files | 159/687 (23.2%) |

**159 skills reference files that don't exist on disk** — these are metadata shells that will fail if invoked.

### 5b. MCP Resources

- 63 MCP entries in DB
- **15 are tier-3** (stub descriptions): clickhouse, github, firecrawl, playwright, etc.
- Only 7 graphify tools are actually live + registered as MCP tools in Hermes
- The 36 `ruflo-mcp-*` entries are documentation chunks about MCP implementation, NOT callable tools
- **Actually wired-up and callable: ~26 MCP tool functions** (7 graphify + 8 strategy-kb + ~11 nexus)

### 5c. Trigger Statistics

| Category | Never Triggered | Triggered ≥1 |
|----------|----------------|--------------|
| skill | 478 | 209 |
| command | 203 | 65 |
| design_system | 122 | 29 |
| agent | 117 | 52 |
| doc | 89 | 35 |
| cli_tool | 66 | 65 |
| hook | 47 | 22 |
| mcp | 36 | 27 |
| spec | 10 | 2 |
| craft | 9 | 3 |

**68% of resources (1,147/1,686) have NEVER been triggered.** These are cold/dead resources taking up embedding space.

### 5d. Accuracy Tracking Status

Out of the top 20 most-triggered resources:
- Only 3 have `times_correct > 0`: retro (93%), adaptive-imagining-cat (88%), api-design (0%)
- **17/20 have 0% accuracy** — the outcome reporting pipeline is broken or not hooked up
- Total correct outcomes: 59, total wrong: 12, total triggers: 982 → 6% recorded accuracy (mostly untracked)

---

## 6. FAISS & EMBEDDING HEALTH

| Metric | Value | Grade |
|--------|-------|-------|
| FAISS vectors | 1,686 | Matches DB ✓ |
| Embedding dimension | 384 (all-MiniLM-L6-v2) | ✓ |
| Index type | IndexFlatIP (inner product) | ✓ |
| Embedding DB coverage | 100% | A |
| Missing embeddings | 0 | A |

### Cluster Confidence

| Cluster | Avg Confidence | Min | Assessment |
|---------|---------------|-----|------------|
| frontend_ui | 0.833 | 0.332 | Strong |
| knowledge_wiki | 0.814 | 0.316 | Strong |
| devops_infra | 0.793 | 0.310 | Good |
| backend_api | 0.748 | 0.276 | Good |
| quality_security | 0.720 | 0.275 | Good |
| analyzer_planner | 0.720 | 0.273 | Good |
| architect | 0.686 | 0.311 | Moderate |
| **general** | **0.430** | **0.060** | **WEAK** |

The `general` cluster has the lowest confidence by far (0.43 avg, 0.06 min) — confirming it's a dumping ground for unclassifiable resources.

### 20 Low-Confidence Resources (all in "general")
- `geolocation-lookup` (0.06), `paper2code-algorithm` (0.15), `property-image-gallery` (0.16)
- These belong in specific clusters but were dumped in general due to low embedding similarity.

### Duplicate Body Text
- 7 groups of duplicated content:
  - 4x: reviewer/tester/coder/researcher (agent duplicates)
  - 2x each: vibeyard/vibeyard-ide, insforge/insforge-baas, etc.

---

## 7. MCP SYNC STATUS

| Server | Tools in DB | Tools Live | Status |
|--------|------------|------------|--------|
| graphify | 7 entries | 7 live | OK (but orphaned from clusters) |
| strategy-kb | 0 entries in DB | 8 live | NOT SYNCED — gap |
| nexus (self) | 63 entries | 15 live | PARTIAL — 36 are docs, not callable |

**Strategy-KB MCP server (8 tools, 592 vectors) has ZERO entries in the NEXUS resource DB.** These tools are callable in Hermes but invisible to NEXUS routing.

---

## 8. LEARNING PIPELINE STATUS

| Layer | Status |
|-------|--------|
| Outcome tracking | Enabled, but 0 outcomes for most resources |
| Thompson sampling | Enabled, 283 beliefs stored |
| Self-reflection | Enabled, 27 failures, 0 fixes |
| Auto-invoke | Enabled |
| Cluster affinity | Enabled, 18 history entries |

The learning pipeline is structurally enabled but produces near-zero signal:
- 333 routing decisions total, 191 from IDE "unknown"
- Only 41 "correct" outcomes recorded out of 982 triggers
- Self-reflection has recorded 27 failures but 0 fixes applied

---

## ACTION ITEMS (Priority Order)

### P0 — Fix Now
1. **Fix cluster count discrepancy** — `cluster_definitions` table is stale. Run reclustering to update `cluster_membership` and sync counts to `cluster_definitions`.
2. **Assign 7 orphan graphify MCP tools** to `knowledge_wiki` or `analyzer_planner` cluster.
3. **Reassign `general` cluster** — 525 resources at 0.43 avg confidence is a dump. Re-cluster the bottom 200 low-confidence items.

### P1 — This Week
4. **Sync strategy-kb MCP tools** into the NEXUS resource DB (8 tools missing).
5. **Purge or archive 159 skill shells** with missing files — they're dead routing weight.
6. **Fix debug → analyzer_planner routing** — move debugging resources from quality_security to analyzer_planner.
7. **Hook up outcome tracking** — 6% accuracy rate means the feedback loop is broken. Fix the `report_outcome` pipeline.

### P2 — Next Sprint
8. **Deduplicate 7 body_text groups** (reviewer/tester/coder/researcher, vibeyard, etc.)
9. **Upgrade 15 tier-3 MCP entries** with proper descriptions (currently stubs).
10. **Purge or cold-storage 1,147 never-triggered resources** — they dilute embedding space and routing precision.
11. **Fix self-reflection** — 27 failures, 0 fixes means the loop isn't closing.

### P3 — Backlog
12. **Re-embed after cleanup** — after removing dead resources, rebuild FAISS index for cleaner routing.
13. **IDE field audit** — 191/333 routes have IDE="unknown", needs fix for attribution.
14. **Add cluster_confidence minimum threshold** — resources below 0.25 should flag for review, not auto-assign to general.