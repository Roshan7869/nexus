# NEXUS v3: Adaptive-Imagining-Cat Typed DAG Plan

## Adaptive Execution Plan

### Phase 0: Deep Research (Research-First Policy)
- **Objective**: Analyze 6 papers, map to NEXUS, identify optimizations
- **Resources**: `[[topics/search-first]]`, paper-bundled knowledge
- **Output**: `deep-research-plan.md`, optimization matrix
- **Guardrails**: Search before coding, verify paper claims against benchmarks
- **Status**: ✅ COMPLETE

### Phase 1: Data Hygiene
- **Type**: implement + verify
- **Objective**: 1097 resources have descriptions and file paths, zero NULLs
- **Resources**: `phase1_data_hygiene.py`
- **Output**: DB backup, hygiene report PASS
- **Guardrails**: Dry-run first, DB backup non-negotiable, idempotent
- **Status**: ✅ COMPLETE
- **Verdict**: PASS (0 NULL descriptions, 0 NULL file_paths, 0 short descriptions)
- **Timestamp**: 2026-05-11 02:08 UTC

### Phase 2: Tier Classification
- **Type**: implement + verify
- **Objective**: Classify resources into T1/T2/T3 by token_count + resource_type heuristic
- **Resources**: `phase2_tier_classify.py`
- **Output**: Tier distribution report
- **Guardrails**: Backup before write, preserve manual overrides
- **Status**: ✅ COMPLETE
- **Verdict**: PASS (T1=832, T2=225, T3=40)
- **Timestamp**: 2026-05-11 02:28 UTC

### Phase 3: Install Dependencies + Create IsoEmbedder
- **Type**: implement
- **Objective**: sentence-transformers + faiss-cpu in venv, IsoEmbedder class created
- **Resources**: `iso_embedder.py`
- **Output**: venv with packages, working IsoEmbedder
- **Guardrails**: Use venv, cache model weights offline
- **Status**: ✅ COMPLETE
- **Verdict**: sentence-transformers-5.4.1, faiss-cpu-1.13.2 installed
- **Timestamp**: 2026-05-11 03:12 UTC

### Phase 4: Build FAISS Index
- **Type**: implement + verify
- **Objective**: 1097 embeddings → IndexFlatIP(384), store in DB
- **Resources**: `phase4_build_faiss.py`
- **Output**: `/nexus/db/faiss/resources.index`, 1097 embeddings cached
- **Guardrails**: Normalized vectors, backup DB before write
- **Status**: ✅ COMPLETE
- **Verdict**: PASS (1097 vectors indexed, dim=384, model=all-MiniLM-L6-v2)
- **Timestamp**: 2026-05-11 03:14 UTC

### Phase 5: State-Aware Precondition Gating
- **Type**: implement + verify
- **Objective**: Block tool use when state preconditions unmet (DB, auth, browser, MCP, etc.)
- **Resources**: `precondition.py`, `phase5_precondition_gate.py`
- **Output**: GLOBAL_GATES functional, 7 tools gated
- **Guardrails**: Default all false, explicit enable only
- **Status**: ✅ COMPLETE
- **Verdict**: PASS (7 tools configured, state gate functional)
- **Timestamp**: 2026-05-11 03:15 UTC

### Phase 6: Two-Phase Lazy Loader
- **Type**: implement + integrate
- **Objective**: Summary pool + on-demand body load + schema promotion + LRU cache eviction
- **Resources**: `lazy_loader.py`
- **Inputs**: Phase 4 (FAISS index)
- **Output**: Tool Attention returns summaries; full body on demand at < 20ms
- **Guardrails**: Summary size < 2MB for 1097 resources, LRU eviction at 512 entries
- **Estimated tokens**: 3000
- **Status**: 🔄 IN PROGRESS

### Phase 7: Hallucination Gate
- **Type**: implement
- **Objective**: After_model hook verifying tool existence before execution
- **Resources**: `hallucination.py`
- **Inputs**: Phase 5 (state gates), Phase 6 (lazy loader)
- **Output**: False negative < 2%, false positive < 5%, gate latency < 5ms
- **Guardrails**: Always verify against DB, never cache "not exists" as permanent
- **Estimated tokens**: 2000
- **Status**: ⏳ PENDING

### Phase 8: CRON Sync (ScaleMCP Pattern)
- **Type**: implement
- **Objective**: Background thread syncs local DB with MCP server, schema validation
- **Resources**: `mcp_sync.py` extension
- **Inputs**: Phase 4 (DB state), Phase 5 (state gates)
- **Output**: Non-blocking sync, no data loss in concurrent access
- **Guardrails**: Thread-safe DB, max 5 min sync frequency, last-write-wins
- **Estimated tokens**: 3000
- **Status**: ⏳ PENDING

### Phase 9: Cross-Type Routing (ToolkenGPT Pattern)
- **Type**: implement + optimize
- **Objective**: Universal route across all 1097 resources (not just skills), type-aware scoring
- **Resources**: `tool_attention.py` extension
- **Inputs**: Phase 4 (embeddings), Phase 6 (lazy loader)
- **Output**: route_universal(task, types) with > 85% accuracy, < 50ms latency
- **Guardrails**: Default to skills if types not specified, weighted scoring 70% semantic + 30% type
- **Estimated tokens**: 4000
- **Status**: ⏳ PENDING

### Phase 10: Reflection Upgrade (Tool-to-Agent Pattern)
- **Type**: implement
- **Objective**: Structured failure context, historical fix matching, auto-retry
- **Resources**: `self_reflector.py` extension
- **Inputs**: Phase 4 (DB state), Phase 7 (hallucination gate)
- **Output**: times_* columns updated after every invocation, failure patterns clustered
- **Guardrails**: Only suggest historical fix once, never force retry without confirmation
- **Estimated tokens**: 3000
- **Status**: ⏳ PENDING

### Phase 11: Wire into MCP Server
- **Type**: integrate + test
- **Objective**: 4 new MCP tools exposed, all functional with FastMCP
- **Resources**: `nexus-mcp-enhanced.py` (4 new tools)
- **Inputs**: Phase 5-10 (all core components)
- **Output**: tool_attention_route, universal_route, check_preconditions, update_state
- **Guardrails**: Each tool has JSON schema, fallback to v2 on error, never crash server
- **Estimated tokens**: 2000
- **Status**: 🔄 PARTIAL (4 tools added, integration in progress)

### Phase 12: Tests (14 + Benchmarks)
- **Type**: test + verify
- **Objective**: 14 tests across all components + 3 benchmarks
- **Resources**: pytest, `test_*.py`
- **Inputs**: Phase 6-11 (all code)
- **Output**: All tests pass, coverage > 80%, benchmarks documented
- **Guardrails**: TDD for new code, integration tests for MCP tools, memory/perf benchmarks
- **Estimated tokens**: 5000
- **Status**: ⏳ PENDING

### Phase 13: Documentation
- **Type**: document
- **Objective**: Update CLAUDE.md, AGENTS.md, skill docs with v3 commands and architecture
- **Resources**: Document tools
- **Inputs**: Phase 11-12 (verified code)
- **Output**: Updated docs, architecture diagrams, usage examples
- **Guardrails**: Don't delete v2 docs, append v3 sections, include migration guide
- **Estimated tokens**: 3000
- **Status**: ⏳ PENDING

### Phase 14: Final Audit
- **Type**: audit
- **Objective**: Compare plan vs actual, verify all metrics, generate audit report
- **Resources**: Audit checklist
- **Inputs**: All prior phases
- **Output**: audit-report.md, before/after metrics, finops impact
- **Guardrails**: Independent verification, immutable artifacts, token usage logged
- **Estimated tokens**: 2000
- **Status**: ⏳ PENDING

## Execution Notes
- Phases 1-5 (Infrastructure) are COMPLETE
- Phases 6-10 (Core v3 Logic) are the highest priority for next session
- Phase 11 (Integration) requires Phase 6-10 to be complete first
- Phases 12-14 (Verification & Documentation) should follow integration

## Resource Mapping Summary
| Phase | Skill/Agent | Command |
|-------|------------|----------|
| 1-5 | `[[topics/careful]]`, `[[topics/planner]]` | `/autopilot` |
| 6-10 | `[[topics/tdd-workflow]]`, `[[topics/architect]]` | `/autopilot` |
| 11 | `[[topics/careful]]`, `[[topics/qa]]` | `/qa` |
| 12 | `[[topics/e2e-testing]]`, `[[topics/qa]]` | `/qa` |
| 13 | `[[topics/document-release]]` | `/ship` |
| 14 | `[[topics/skill-stocktake]]`, `[[topics/verification-loop]]` | `/retro` |

## Next Action
Phase 6 implementation: complete lazy_loader.py with LRU cache and integrate into tool_attention.find_resources()