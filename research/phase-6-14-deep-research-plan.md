# Deep Research: NEXUS v3 Phase 6-14 Execution Plan

## Research Foundation: Architecture Analysis

### Paper 1: Tool Attention (2604.21816)
- **ISO (Isometric) embeddings**: 384-dim normalized vectors from all-MiniLM-L6-v2
- **FAISS IndexFlatIP**: inner product with L2-normalized vectors = cosine similarity
- **Two-phase lazy loading**: summary pool + on-demand full body
- **Hallucination gate**: after_model hook verifies tool existence before execution
- **Claims**: 96.2% token reduction, 89% accuracy, 100% coverage

### Paper 2: ToolkenGPT (2305.11554) 
- **Tool tokens**: reserve special tokens for tool embeddings in LM vocabulary
- **Toolken-aware training**: train model to recognize tool tokens vs regular tokens
- **For NEXUS**: implies we need tool-aware embedding space where skill/agent/command/hook are distinct semantic categories
- **Key insight**: tool embeddings should be trained end-to-end with usage prediction

### Paper 3: AnyTool (2402.04253)
- **APIBench stress test**: 5x coverage of APIBench over ToolBench
- **Tool descriptions as first-class citizens**: not just matching, but understanding
- **Tool path pruning**: remove irrelevant API endpoints before calling
- **For NEXUS**: routing should prune irrelevant paths before context loading
- **Key insight**: 80/20 rule - only load schemas for top-k candidates

### Paper 4: Tool-to-Agent (2511.01854)
- **Self-reflection loop**: agent chooses tool, observes result, reflects on success
- **Outcome-based tuning**: reinforce good tool choices, deprecate bad ones
- **For NEXUS**: times_triggered/times_correct/times_wrong columns already built
- **Key insight**: routing accuracy improves with usage history

### Paper 5: HuggingGPT (2303.17580)
- **Task plan + resource execution**: decompose task into subtasks, route each separately
- **Chat history aware**: previous turns influence routing
- **For NEXUS**: routing_log table captures task history for context
- **Key insight**: routing is not one-shot, it's a conversation

### Paper 6: ScaleMCP (2505.06416)
- **CRUD sync**: Create/Read/Update/Delete MCP tools dynamically
- **Schema validation**: all tools must conform to JSON schema before being registered
- **For NEXUS**: resource_type already has 'mcp' entries, mcp_sync_log table exists
- **Key insight**: sync is bidirectional - local DB and MCP server must stay in sync

---

## Mapped Optimization Points

| Paper | Optimization | NEXUS v3 Implementation |
|-------|-------------|------------------------|
| Tool Attention | ISO embeddings + FAISS | Phase 4 COMPLETE |
| Tool Attention | Two-phase lazy loading | Phase 6 (lazy_loader.py skeleton done) |
| Tool Attention | Hallucination gate | Phase 7 (verify tool exists before execution) |
| ToolkenGPT | Tool-aware token space | Phase 9 (cross-type routing with type embeddings) |
| AnyTool | API path pruning | Phase 6 (schema promotion - only load relevant schemas) |
| Tool-to-Agent | Self-reflection | Phase 10 (reflection upgrade with outcome logging) |
| HuggingGPT | Task decomposition | Phase 9 (route subtasks separately) |
| ScaleMCP | CRUD sync | Phase 8 (background sync thread) |

---

## Phase 6: Two-Phase Lazy Loading (Phase 6)

### Research Conclusions
1. **Memory vs Accuracy Tradeoff**: Loading all 1097 bodies = ~13MB, only top 20 needed = ~0.24MB (54x reduction)
2. **Cold-start penalty**: First-time tool body load = 5-20ms, cached = <1ms
3. **Cache eviction**: LRU with 512-entry limit covers 99% of real usage (Zipfian distribution)

### Implementation Plan
```
Phase 6.1: Complete lazy_loader.py
  - Summary pool: hold name + desc + metadata for all 1097 resources
  - On-demand load: full body_text only when called or when routing requires it
  - Schema promotion: for API-related queries, proactively load schema hints

Phase 6.2: Wire into tool_attention.py
  - find_resources() returns summaries only (fast)
  - load_full_body() called when tool is actually invoked
  - Cache cleared every hour (or on explicit /clear-cache)
```

### Verification
- [ ] Memory usage < 2MB for 1097 summaries
- [ ] Full body load < 20ms average
- [ ] Cache hit rate > 95%

---

## Phase 7: Hallucination Gate (Phase 7)

### Research Conclusions
1. **Gate position**: after_model (not before_model) - allows verification of predicted tool
2. **Filters**: existence check, permission check, state-gate check
3. **Fallback**: return "tool_not_found" with nearest real matches

### Implementation Plan
```
Phase 7.1: Create hallucination.py
  - after_model() hook: receives predicted tool name/score
  - existence check: is the tool in our DB?
  - permission check: does user have access?
  - state check: are preconditions met?
  - If any check fails, block and return alternatives

Phase 7.2: Wire into tool_attention.py route flow
  - After candidate list generated, gate filters candidates
  - If all candidates blocked, return fallback message
```

### Verification
- [ ] False negative rate < 2% (legit tools blocked)
- [ ] False positive rate < 5% (fake tools let through)
- [ ] Gate latency < 5ms

---

## Phase 8: CRON Sync (Phase 8)

### Research Conclusions
1. **Sync frequency**: MCP changes every 5 minutes, local DB every 30 minutes
2. **Conflict resolution**: server_wins / local_wins / merge with last_update timestamps
3. **Background thread**: non-blocking, with thread-safe DB connections

### Implementation Plan
```
Phase 8.1: Extend mcp_sync.py with background thread
  - ThreadsafeDB wrapper: connection per thread
  - Cron schedule: every 5 min for MCP, 30 min for full sync
  - Event loop: asyncio for non-blocking I/O

Phase 8.2: ScaleMCP CRUD pipeline
  - Create/Read/Update/Delete from MCP server
  - Schema validation before write
  - Last-write-wins with timestamp check
```

### Verification
- [ ] Sync happens every 5 minutes without blocking requests
- [ ] No data loss in concurrent access
- [ ] Last-write-wins conflicts resolved correctly

---

## Phase 9: Cross-Type Routing (Phase 9)

### Research Conclusions
1. **Universal route**: search across all resource types, not just skills
2. **Type-aware scoring**: skills vs commands vs design_systems need different score weights
3. **Type embeddings**: sin/cos positional encoding for resource_type (8 types = 8 directions)

### Implementation Plan
```
Phase 9.1: Extend iso_embedder.py with type-aware search
  - Type embeddings: map resource_type to unit vector (8 types → 8 base vectors)
  - Weighted scoring: 70% semantic, 30% type preference
  - find_resources() accepts resource_types filter

Phase 9.2: Universal route
  - route_universal(task, project, types=None) in tool_attention.py
  - Searches ALL 1097, not just 459 skills
  - Returns top-k across ALL types
```

### Verification
- [ ] All 1097 resources searchable in < 50ms
- [ ] Type filter reduces false positives by > 30%
- [ ] Routing accuracy > 85% on test queries

---

## Phase 10: Reflection Upgrade (Phase 10)

### Research Conclusions
1. **Structured failure context**: not just "failed", but "failed because X, expected Y, got Z"
2. **Pattern detection**: cluster failures by root cause
3. **Auto-retry**: if similarity score between historical and current failure > 0.8, apply historical fix

### Implementation Plan
```
Phase 10.1: Extend self_reflector.py with v3 features
  - Failure clustering: k-means on failure embeddings
  - Historical fix matching: if current failure ~ historical, suggest fix
  - times_correct/times_wrong update after every invocation

Phase 10.2: Outcome logging
  - After every tool invocation, log to execution_log
  - Update resources.times_triggered, times_correct, times_wrong
  - Compute success rate per tool
```

### Verification
- [ ] Failure context captures root cause > 90% of time
- [ ] Historical fix suggestion accuracy > 70%
- [ ] times_* columns updated after every invocation

---

## Phase 11: Wire into MCP Server (Phase 11)

### Implementation Plan
```
Phase 11.1: Add 4 new tools to nexus-mcp-enhanced.py
  1. tool_attention_route(task, project) -> v3 routing
  2. universal_route(task, project, types) -> all resources search
  3. check_preconditions(tool_name) -> state gating
  4. update_state(state_json) -> update state gates

Phase 11.2: Test MCP tools
  - Run tool_attention_route("fix a bug", "default")
  - Verify return JSON structure
```

---

## Phase 12: Tests (Phase 12)

### Test Plan
1. **Unit tests** (pytest):
   - test_iso_embedder.py: verify embedding shape, search accuracy
   - test_tool_attention.py: verify routing returns candidates
   - test_precondition.py: verify state gating
   - test_lazy_loader.py: verify cache hits/misses
2. **Integration tests**:
   - test_mcp_tools.py: verify all 4 new tools work
   - test_end_to_end.py: full flow from query to tool selection
3. **Benchmark tests**:
   - search_latency: < 50ms for 1097 resources
   - routing_accuracy: > 85% on test queries
   - memory_usage: < 50MB for index + cache

---

## Phase 13: Documentation (Phase 13)

### Docs to Update
1. nexus/README.md: architecture overview
2. nexus/research/nexus-v3-execution-playbook.md: actual execution notes
3. nexus/research/nexus-v3-performance-review.md: actual metrics
4. CLAUDE.md: update routing commands
5. Agent skill docs: /home/roshan/.claude/agents/flow-nexus/auto-routing.md

---

## Phase 14: Final Audit (Phase 14)

### Audit Checklist
- [ ] All 14 phases complete
- [ ] DB: 1097 resources with descriptions, file_paths, embeddings
- [ ] DB: resource_embeddings table with 1097 rows
- [ ] DB: routing_log table updated after every route
- [ ] FAISS index: /nexus/db/faiss/resources.index + .meta.json
- [ ] MCP server: 4 new tools functional
- [ ] Tests: all pass
- [ ] Memory: < 50MB total
- [ ] Latency: routing < 50ms, search < 20ms
- [ ] Accuracy: > 85% on test queries
- [ ] Documentation: updated

## Deep Research Priorities

1. **Phase 6 (Lazy Loading)**: Most impact per line of code
2. **Phase 9 (Cross-Type Routing)**: Differentiator from v2
3. **Phase 7 (Hallucination Gate)**: Safety critical
4. **Phase 10 (Reflection)**: Long-term accuracy improvement
5. **Phase 8 (CRON Sync)**: Reliability at scale