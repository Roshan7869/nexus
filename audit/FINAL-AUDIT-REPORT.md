# NEXUS Resource Audit — Final Consolidated Report

**Generated:** 2026-06-28
**Agents:** cluster-auditor, domain-alignment, stale-cleaner, report-synthesizer
**Scope:** Full audit of NEXUS v4 resource index — 2,842 total resources across 8 domain clusters and 10 resource types

---

## 1. Executive Summary

- **49% of indexed resources (1,068) have never been triggered** — nearly half the index is dead weight, inflating FAISS search space and degrading routing quality
- **general cluster is critically broken**: 87.3% stale rate, 51.6% success rate — functions as an orphan dumping ground for 104 MCP stubs that should either be redistributed or purged
- **241 MCP auto-registration stubs with ~0 tokens** are polluting the index — these are empty shell entries from `nexus_sync_mcp_tools` with no routing value
- **resources.cluster vs cluster_membership tables are desynchronized on 859 resources** — the routing system and cluster definition counts disagree, creating an invisible correctness gap
- **Domain alignment is healthy overall (97/100)** — 0 empty bodies, 0 missing embeddings, all shell scripts correctly typed — the structural foundation is sound; the problem is bloat and orphan management

---

## 2. Resource Health Dashboard

| Metric | Value |
|---|---|
| Total active (non-archived) | 2,181 |
| Archived | 661 |
| Stale (zero triggers) | 1,068 (49.0%) |
| Missing embeddings | 0 |
| Missing clusters | 0 |
| Empty bodies | 0 |
| Stub bodies (<20 chars) | 41 (26 hooks + 15 commands) |
| Domain alignment score | 97/100 |
| Resource types | 10 (skill, mcp, command, doc, agent, design_system, cli_tool, spec, hook, craft) |
| Cluster definitions | 8 |

---

## 3. Cluster Health

| Cluster | Total | Stale | Stale% | Success Rate | Top Issues |
|---|---|---|---|---|---|
| general | 150 | 131 | **87.3%** | **51.6%** | Orphan dumping ground; 104 MCPs (90 stale); 25/32 skills never triggered |
| knowledge_wiki | 276 | 165 | 59.8% | 84.1% | High stale rate; 16 stale MCPs; 64 keywords misaligned to 'build' |
| analyzer_planner | 557 | 279 | 50.1% | 89.6% | 72 stale MCPs; 156 command resources (24% of cluster) |
| backend_api | 147 | 73 | 49.7% | 93.1% | 14 stale MCPs; 80 resources tagged 'ui' are misaligned |
| architect | 299 | 127 | 42.5% | 87.8% | 25 stale MCPs; 42.5% stale is moderate but above average |
| frontend_ui | 458 | 193 | 42.1% | 90.7% | 162 design_system resources (33% of cluster); 9 stale MCPs |
| quality_security | 120 | 41 | 34.2% | 91.6% | Best routing success rate; lowest stale % after devops_infra |
| devops_infra | 174 | 59 | 33.9% | 88.4% | Best stale rate; 77% skill-concentrated (362/468 via resources.cluster) |

**Note on counts:** Cluster totals vary between reports because the `resources.cluster` column (2,842 total, used by routing) and `cluster_membership` table (2,001 unique assignments, used for definitions) are desynchronized. The table above uses stale-cleaner's deduplicated counts (2,181 total).

---

## 4. Critical Issues

### CRITICAL: resources.cluster vs cluster_membership Table Desync
- **859 resources disagree** between the two systems
- 893 resources have a `resources.cluster` value but **zero entries** in `cluster_membership`
- The routing system reads `resources.cluster` at query time; cluster definition counts come from `cluster_membership`
- This means cluster resource counts in definitions are unreliable — `analyzer_planner` shows 165 members but has 642 routed resources
- **Impact:** Cluster coverage analysis, routing statistics, and capacity planning are all affected

### HIGH: general Cluster — 87.3% Stale, 51.6% Success Rate
- Nearly 2x the stale rate of the next-worst cluster (knowledge_wiki at 59.8%)
- 90 of 104 MCPs in general are stale stubs with ~0 tokens
- 25 of 32 skills are never triggered
- Functions as a catch-all for orphaned resources that failed classification
- **Impact:** Every routing cycle wastes resources searching this mostly-dead cluster

### HIGH: 1,068 Stale Resources — 49% Index Bloat
- Nearly half the index has never contributed a routing decision
- 235 of these have zero token count (incomplete ingestion)
- Every stale resource in FAISS increases search noise and degrades recall for valid resources
- **Impact:** Routing quality degrades proportionally to stale ratio

### HIGH: 241 MCP Auto-Registration Stubs
- 98.8% (238/241) have ≤5 tokens — effectively empty
- Average token count for stale MCP: **0.2**
- Created by `nexus_sync_mcp_tools` as placeholder entries with no body content
- Worst offenders: general (90), analyzer_planner (72), architect (25)
- **Impact:** Pollute FAISS with zero-information vectors; increase search space by ~11%

### MEDIUM: 41 Stub Bodies (26 Hooks + 15 Commands)
- 26/77 hooks (34%) have bodies like "V3 helper", "Hook: Stop -", "Memory helper"
- 15 command entries are "{name} command" stubs (e.g., "eval command", "pm2 command")
- 4 agent entries are also minimal stubs ("Go-build-resolver agent.", "test-engineer agent")
- **Impact:** No routing value; inflate index with placeholder entries

### MEDIUM: 26 "Walking Dead" Resources
- Resources that ARE being triggered but have zero tokens
- Examples: `session_info` (triggered=2), `browser_snapshot` (triggered=2), `wasm_agent_files` (triggered=2)
- Name matching works but content ingestion clearly failed
- **Impact:** Routed to dead ends — users get empty results for valid queries

### LOW: ~5 Minor Keyword Misalignments
- 2 cli_tool entries potentially mis-typed (id=718 `specialist-stats`, id=737 `settings-hook`)
- Minor ADR doc vs spec boundary question (no change needed per domain alignment audit)
- **Impact:** Negligible on their own, but part of broader cluster hygiene issue

---

## 5. Recommended Actions

| # | Action | Resources Affected | Est. Impact | Priority |
|---|---|---|---|---|
| A | **Archive 241 zero-token MCP stubs** — these are empty auto-registration placeholders | 241 MCPs | Reduces FAISS space ~11%; removes 84% of stale MCPs | Immediate |
| B | **Archive 290 low-token stubs** — commands (104), agents (44), skills (42), hooks (30), cli_tool (1) with ≤10 tokens | 290 resources | Stale rate drops to ~27.3% (from 49.0%) | High |
| C | **Deprioritize 180 tier-1 stale resources** — demote to tier 4 so proven resources route first | 180 skills/agents | Routing quality improves for top queries | High |
| D | **Reconcile cluster vs cluster_membership** — rebuild cluster_membership from canonical resources.cluster | 859 desynced records | Eliminates invisible correctness gap | **Critical** |
| E | **Redistribute general cluster** — move ~97 MCPs to proper clusters (frontend_ui=59, general=36, quality_security=4, etc.); archive the rest | ~97 MCPs + 53 other general resources | general stale rate drops from 87.3% to ~20% | High |
| F | **Consider design_system as its own cluster** — 173 resources warrant independent domain | 173 design_system resources | Cleaner frontend_ui (sheds 33% of its resources) | Medium |
| G | **Re-ingest 26 walking dead** — fix content for resources that match but have zero tokens | 26 resources | Fixes active routing dead ends | Medium |

**Cumulative impact if A+B implemented:** ~531 resources archived, stale rate drops to ~27.3%, FAISS search space reduced by ~24%.

**Cumulative impact if A+B+E implemented:** ~628 resources archived or redistributed, stale rate drops to ~19.4%.

---

## 6. What's Healthy

- **0 resources without embeddings** — every indexed resource has a FAISS vector
- **0 resources without cluster assignment** — full cluster coverage
- **0 empty bodies** — every resource has at least some body text
- **All 10 shell scripts correctly typed** as cli_tool or mcp — no type misassignment for executable resources
- **Top clusters have 88-93% success rates** — backend_api (93.1%), quality_security (91.6%), frontend_ui (90.7%), analyzer_planner (89.6%) all perform well
- **No duplicate resource names across clusters** — naming collision surface is zero
- **Domain alignment score: 97/100** — type assignment (95/100), body completeness (88/100), cluster coherence (90/100)
- **~99% of resources properly typed** — only 2 potential misassignments identified across 2,842 resources
- **spec resources have lowest stale rate** (4.7%) — specification resources are routinely referenced

---

## 7. Swarm Performance

| Aspect | Detail |
|---|---|
| **Swarm ID** | `swarm-1782641795318-mhyxgx` |
| **Topology** | hierarchical |
| **Strategy** | specialized |
| **Agents** | cluster-auditor (analyst), domain-alignment (analyst), stale-cleaner (reviewer), report-synthesizer (documenter) |
| **Parallelism** | All 3 audit subagents launched in parallel via `run_in_background: true` |

### Agent Execution

| Agent | Status | Notes |
|---|---|---|
| **cluster-auditor** | ✅ Completed | Analyzed 2,842 resources across 8 clusters; found cluster desync (859) and general cluster issues |
| **domain-alignment** | ✅ Completed | Checked type assignment, body completeness, cluster coherence across all 10 resource types |
| **stale-cleaner** | ✅ Completed | Identified 1,068 stale resources with tier/type/cluster breakdown and impact estimates |
| **report-synthesizer** | ✅ Completed | Consolidated all findings into this final report |

### Execution Method

The swarm was designed to use `ruflo_agent_spawn` + `ruflo_agent_execute` for sub-agent tasks. However, `ruflo_agent_execute` failed because the configured `ANTHROPIC_API_KEY` was set to `"ollama"` (a local proxy value) rather than a valid Anthropic key, preventing API calls from spawned agents.

**Fallback:** Claude Code native `Task` subagents (with `subagent_type: "explore"` / `"general"`) were used instead. These worked correctly and all three subagents completed their audit targets in parallel with no issues.

**Lesson:** Swarm Agent execution requires a valid `ANTHROPIC_API_KEY` in the environment. When using Ollama or alternative backends, use `Task` subagents instead of `ruflo_agent_execute` for agent task execution.

---

*End of Report — 3 source reports consolidated, 2,842 resources audited, 1,068 stale identified, 8 critical/high issues documented.*
