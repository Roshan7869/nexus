# Tool Attention Implementation Plan — NEXUS v3

## Plan Overview

| Field | Value |
|-------|-------|
| **Plan ID** | `plan-ta-20260510` |
| **Goal** | Implement Tool Attention middleware into NEXUS MCP system |
| **Estimated Phases** | 6 stacks, 14 phases |
| **Estimated Tokens** | ~45,000 |
| **External References** | Tool Attention (2604.21816), Citadel (nicheai/citadel), AnyTool (2402.04253), AgentDB MCP |

---

## Current State Analysis

### Resource Inventory
```
Resources: 1,097 total
  skill:          459   (41.8%)
  command:        184   (16.8%)
  design_system:  148   (13.5%)
  agent:          143   (13.0%)
  hook:            68   ( 6.2%)
  cli_tool:        47   ( 4.3%)
  mcp:             16   ( 1.5%)
  craft:           12   ( 1.1%)
  doc:             11   ( 1.0%)
  spec:             9   ( 0.8%)

Issues:
  - 46 resources MISSING descriptions (4.2%)
  - All resources injected eagerly (no lazy loading)
  - 0 routing logs before v2 implementation
  - No sentence embeddings (sqlite-vec only)
```

### Existing NEXUS Architecture (v2)

```
nexus-mcp-enhanced.py     ← MCP Server (618 lines)
├── find_skills()         ← v1 manual routing
├── get_skill()           ← v1 skill retrieval
├── log_outcome()         ← v1 feedback loop
├── audit_routing()       ← v1 reporting
├── design_plan()         ← adaptive-imagining-cat
├── execute_phase()       ← plan execution
├── auto_route()          ← v2 autonomous routing
├── trigger_on_event()    ← v2 event trigger
├── hierarchical_retrieve() ← v2 browse
├── sync_mcp_tools()      ← v2 MCP sync
└── nexus_health()        ← health check

auto_invoke.py            ← Trigger + Toolken Selection (447 lines)
├── AutoInvoke
│   ├── detect_trigger()
│   ├── find_skills()
│   ├── _toolken_select()
│   └── execute_with_reflection()

hierarchy_router.py       ← 3-Layer Router (296 lines)
├── HierarchicalRouter
│   ├── route_category()      ← Layer 1
│   ├── search_tools_tdwa()   ← Layer 2
│   └── join_with_agents()    ← Layer 3

self_reflector.py         ← Self-Reflection (371 lines)
├── SelfReflector
│   ├── _classify_error()
│   ├── _find_alternatives()
│   └── execute_with_retry()

mcp_sync.py               ← CRUD Pipeline (415 lines)
├── MCPSyncManager
│   ├── create_tool()
│   ├── read_tool()
│   ├── update_tool()
│   ├── delete_tool()
│   └── sync_from_mcp_server()
```

### Research Reference Architecture

**Tool Attention (2604.21816) Ref Implementation:**
```
tool-attention/code/
├── vector_store.py         ← FAISS-backed summary embedding index (48 lines)
├── intent_router.py        ← ISO score + state-aware gating (65 lines)
├── lazy_loader.py          ← LRU-cached full-schema loader (55 lines)
├── tool_attention.py       ← before_model/after_model middleware (70 lines)
├── build_catalog.py        ← synthetic catalog generator
└── benchmark.py            ← token-counting harness
```

### Dependency Gap

| Dependency | Status | Required By |
|-----------|--------|-------------|
| `sentence-transformers` | ❌ MISSING | ISO embedding |
| `faiss-cpu` | ❌ MISSING | Vector similarity search |
| `tiktoken` | ✅ 0.12.0 | Token counting |
| `fastmcp` | ✅ 3.2.4 | MCP server |
| `sqlite-vec` | ✅ | Existing embedding store |

---

## DAG Plan

```
                                    ┌─────────────────────────────────────────────────────────────────────────────────────────────────────┐
                                    │                                    NEXUS v3 IMPLEMENTATION PLAN                                      │
                                    │                                    Tool Attention Integration                                        │
                                    └─────────────────────────────────────────────────────────────────────────────────────────────────────┘
                                                        │
                    ┌───────────────────────────────────┼───────────────────────────────────┐
                    │                                   │                                   │
                    ▼                                   ▼                                   ▼
┌──────────────────────────────────────┐   ┌──────────────────────────────────────┐   ┌──────────────────────────────────────┐
│ STACK 0: Infrastructure              │   │ STACK 1: ISO Embedding Engine        │   │ STACK 2: State-Aware Gating           │
│ (Foundation Layer)                   │   │ (Semantic Router)                    │   │ (Precondition Engine)                │
├──────────────────────────────────────┤   ├──────────────────────────────────────┤   ├──────────────────────────────────────┤
│ 0.1 Install deps                     │   │ 1.1 Install sentence-transformers    │   │ 2.1 Design precondition DSL           │
│ 0.2 Create NEXUS_TOOL_ATTENTION/     │   │ 1.2 Create iso_embedder.py           │   │ 2.2 Create precondition.py             │
│ 0.3 Port vector_store.py from ref   │   │ 1.3 Build FAISS index from 1,097     │   │ 2.3 State-checking predicates         │
│ 0.4 Port lazy_loader.py from ref    │   │     resources                        │   │ 2.4 Integration tests                 │
│ 0.5 Generate summaries for 46       │   │ 1.4 ISO score threshold calibration  │   └──────────────────────────────────────┘
│     missing-desc resources          │   │ 1.5 Replace TDWA with ISO in            │
│ 0.6 Test harness setup              │   │     hierarchy_router.py              │
└──────────────────────────────────────┘   │ 1.6 Benchmark: TDWA vs ISO           │
                    │                      └──────────────────────────────────────┘
                    │                                   │
                    ▼                                   ▼
┌──────────────────────────────────────┐   ┌──────────────────────────────────────┐
│ STACK 3: Two-Phase Lazy Loader       │   │ STACK 4: Middleware Orchestrator      │
│ (Schema Economy)                     │   │ (Integration Layer)                   │
├──────────────────────────────────────┤   ├──────────────────────────────────────┤
│ 3.1 Port lazy_loader.py → nexus     │   │ 4.1 ToolAttention orchestrator        │
│ 3.2 LRU schema cache with MCP       │   │ 4.2 before_model() middleware          │
│     server fallback                 │   │ 4.3 after_model() hallucination gate   │
│ 3.3 Summary pool generator          │   │ 4.4 Wire into nexus-mcp-enhanced.py    │
│ 3.4 Summary→Schema promotion logic  │   │ 4.5 LangGraph middleware contract      │
│ 3.5 Token accounting benchmark      │   │ 4.6 End-to-end integration test        │
└──────────────────────────────────────┘   └──────────────────────────────────────┘
                    │                                   │
                    └───────────────────┬───────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ STACK 5: Testing & Validation Suite                                             │
│ (Quality Gate)                                                                    │
├─────────────────────────────────────────────────────────────────────────────────┤
│ 5.1 Unit tests: each module in isolation                                         │
│ 5.2 Integration: full pipeline (query→ISO→gate→load→execute→hallucinate)        │
│ 5.3 Benchmark: token reduction measurement (vs 47.3k baseline)                  │
│ 5.4 Ablation: ISO vs TDWA vs keyword (precision/recall)                         │
│ 5.5 Stress test: 1,097 resources all in context                                 │
│ 5.6 Regression: ensure find_skills() still works                                │
└─────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ STACK 6: Documentation & Deployment                                              │
│ (Ship Ready)                                                                      │
├─────────────────────────────────────────────────────────────────────────────────┤
│ 6.1 Update CLAUDE.md with v3 commands                                            │
│ 6.2 Update auto-routing.md agent skill                                           │
│ 6.3 Performance report (before/after token counts)                              │
│ 6.4 Rollback plan (v2 fallback)                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Stack 0: Infrastructure (Foundation)

### Objective
Set up the development environment, install dependencies, port reference code, and fix data quality issues.

### Tasks

```
0.1 Install Dependencies
    └─ Command: pip install sentence-transformers faiss-cpu tiktoken
    └─ Verify: python -c "from sentence_transformers import SentenceTransformer; m = SentenceTransformer('all-MiniLM-L6-v2'); print('OK', m.get_sentence_embedding_dimension())"
    └─ Expected: 384-dimensional embeddings

0.2 Create NEXUS_TOOL_ATTENTION module
    └─ Path: /home/roshan/nexus/server/tool_attention/
    └─ Files:
        ├── __init__.py          ← exports
        ├── vector_store.py     ← FAISS-backed index (port from ref)
        ├── intent_router.py    ← ISO score router (port from ref)
        ├── lazy_loader.py      ← Schema loader (port from ref)
        ├── tool_attention.py   ← Middleware orchestrator (port from ref)
        ├── iso_embedder.py     ← Wrapper for sentence-transformers
        └── precondition.py     ← State-aware gating predicates

0.3 Port vector_store.py from Reference
    └─ File: tool-attention/code/vector_store.py → /home/roshan/nexus/server/tool_attention/vector_store.py
    └─ Modifications:
        ├─ Add sqlite-vec backup (persist to nexus.db)
        └─ Add metadata columns: resource_type, tier, success_rate

0.4 Port lazy_loader.py from Reference
    └─ File: tool-attention/code/lazy_loader.py → /home/roshan/nexus/server/tool_attention/lazy_loader.py
    └─ Modifications:
        ├─ Add DB-backed registry (read from resources table)
        ├─ Add MCP server fetch fallback (using existing mcp_sync.py)
        └─ Extend capacity to 512 (we have 1,097 resources)

0.5 Fix 46 Missing Descriptions
    └─ Generate summaries using existing body_text + name
    └─ Heuristic: take first 200 chars of body_text + name as description
    └─ SQL: UPDATE resources SET description = SUBSTR(body_text, 1, 200) WHERE description IS NULL OR description = ''

0.6 Test Harness
    └─ Script: /home/roshan/nexus/server/tool_attention/test_harness.py
    └─ Tests:
        ├─ Can import all modules
        ├─ FAISS index builds without error
        ├─ Lazy loader returns schema for known tool_id
        └─ Token counter works (tiktoken)
```

### Guardrails
- [ ] sentence-transformers downloads ~90MB model
- [ ] Keep existing sqlite-vec embeddings as fallback
- [ ] FAISS index rebuilt when resources change (hook into mcp_sync)

### Resources Used
- Skill: `[[topics/iterative-retrieval]]`, `[[topics/planner]]`
- Commands: `pip3`, `python3`, `sqlite3`

---

## Stack 1: ISO Embedding Engine

### Objective
Replace the current TDWA (keyword-weighted overlap) scoring with proper Intent-Schema Overlap using sentence embeddings and FAISS vector search.

### Architecture

```
┌─────────┐    query     ┌──────────────────┐    cosine scores    ┌───────────┐
│ User    │──────────────▶│ ISO Embedder     │────────────────────▶│ Threshold │
│ Query   │              │ (MiniLM-L6-v2)   │                    │ (θ=0.28)  │
└─────────┘              └──────────────────┘                    └─────┬─────┘
                                                                       │
                              ┌────────────────────────┐               │
                              │ FAISS IndexFlatIP      │◄──────────────┘
                              │ (384d, 1,097 vectors)  │   ≥ threshold
                              └────────────────────────┘
                                       │
                                       ▼
                              ┌──────────────────┐
                              │ Top-k Selection  │
                              │ (k=10)           │
                              └────────┬─────────┘
                                       │
                                       ▼
                              ┌──────────────────┐
                              │ Tool IDs +       │
                              │ ISO Scores       │
                              └──────────────────┘
```

### Tasks

```
1.1 Install & Verify sentence-transformers
    └─ Already done in 0.1
    └─ Verify MiniLM-L6-v2 produces 384-d embeddings

1.2 Create iso_embedder.py
    ├─ class ISOEmbedder:
    │   ├─ __init__(model_name="all-MiniLM-L6-v2", device="cpu")
    │   ├─ encode(query: str) → np.ndarray[float32](384,)
    │   ├─ batch_encode(queries: List[str]) → np.ndarray
    │   ├─ cosine_similarity(a: np.ndarray, b: np.ndarray) → float
    │   └─ iso_score(query: str, tool_summary: str) → float
    │
    ├─ Model: all-MiniLM-L6-v2 (22M params, 384-d output)
    └─ Latency: ~30-60ms per query on CPU

1.3 Build FAISS Index from 1,097 Resources
    ├─ Script: build_faiss_index.py
    ├─ Logic:
    │   For each resource in resources table:
    │     summary = f"{name}: {description[:200]}"
    │     embedding = iso_embedder.encode(summary)
    │     faiss_index.add(embedding)
    │     metadata[embedding_id] = {name, resource_type, tool_id}
    │
    ├─ Output: /home/roshan/nexus/db/faiss/index.faiss + meta.json
    ├─ Index type: IndexFlatIP (Inner Product = cosine for normalized vecs)
    ├─ Dimensions: 384
    └─ Estimated size: 1,097 * 384 * 4 bytes = ~1.7 MB (FAISS overhead: ~5 MB)

1.4 ISO Score Threshold Calibration
    ├─ Method: Sweep θ ∈ [0.10, 0.50] in increments of 0.02
    ├─ Held-out set: 200 (query, ground-truth-tool) pairs
    ├─ Metric: F1 = 2 * precision * recall / (precision + recall)
    ├─ Expected optimum: θ* ∈ [0.22, 0.32] (from paper)
    └─ Output: threshold value stored in config

1.5 Replace TDWA in hierarchy_router.py
    ├─ BEFORE: _compute_tdwa(self, tool, query) → keyword overlap
    │   score = name_overlap * 2.0 + desc_overlap * 1.0 + body_overlap * 0.5
    │
    ├─ AFTER: _compute_iso(self, tool_id, query) → embedding cosine
    │   query_emb = iso_embedder.encode(query)
    │   tool_emb = faiss_index.get(tool_id)
    │   score = cosine(query_emb, tool_emb)
    │
    └─ Keep TDWA as FALLBACK if FAISS index unavailable

1.6 Benchmark: TDWA vs ISO
    ├─ Metric: Precision@5, Recall@5, nDCG@5
    ├─ Test queries: 50 realistic MCP queries
    ├─ Expected: ISO +15-20% over TDWA (from Tool-to-Agent paper)
    └─ Output: benchmark_results.json
```

### Algorithm Reference (from paper)
```
ISO(q, t_i) = cos(e_q, e_t_i)
where:
  e_q  = ϕ(q)         ← query embedding via MiniLM-L6-v2
  e_ti = ϕ(s_i)       ← tool summary embedding (precomputed)
  ϕ    = sentence-transformers encoder
  s_i  = compact summary of tool t_i (≤60 tokens)
```

### Guardrails
- [ ] FAISS index rebuilt when MCP sync adds tools
- [ ] Keep TDWA as fallback (don't break existing hierarchy_router.py)
- [ ] ISO score < 0.28 = SKIP (same as Toolken INVOKE/REVIEW/SKIP)

---

## Stack 2: State-Aware Gating

### Objective
Add deterministic precondition checking so tools are filtered not just by semantic relevance but by whether the agent's current execution state satisfies the tool's requirements.

### Architecture

```
┌──────────────┐   ┌──────────────────────┐   ┌─────────────────┐
│ ISO Scores   │   │ Precondition Engine  │   │ Gated Tool Set  │
│ [t1: 0.85]  │──▶│                      │──▶│ [t1: 0.85]     │
│ [t2: 0.72]  │   │ gate(t) = 1[ISO≥θ]   │   │ [t4: 0.55]     │
│ [t3: 0.65]  │   │         · 1[state⊧pre]│   │                 │
│ [t4: 0.55]  │   └──────────────────────┘   └─────────────────┘
│ [t5: 0.42]  │         ↑                          │
└──────────────┘         │ state: {auth, workflow,  │
                         │          prior_outputs}   ▼
                                            ┌────────────────┐
                                            │ Active Set A_t │
                                            │ (top-k gated)   │
                                            └────────────────┘
```

### Tasks

```
2.1 Design Precondition DSL
    ├─ Precondition types:
    │   ├─ auth_required(scope: str)       → "github:write", "slack:read"
    │   ├─ requires_tool_output(tool_id: str) → "search_*" executed
    │   ├─ milestone_reached(milestone: str)  → "plan_confirmed"
    │   ├─ rate_limit(max_calls: int)         → max 5 calls/turn
    │   └─ only_after_tool(tool_id: str)      → run after "search"
    │
    └─ Format: JSON
        {
            "preconditions": [
                {"type": "auth_required", "scope": "github:write"},
                {"type": "only_after_tool", "tool": "search_issues"}
            ]
        }

2.2 Create precondition.py
    ├─ class PreconditionEngine:
    │   ├─ __init__(state: AgentState)
    │   ├─ check(tool_id: str) → bool
    │   ├─ check_all(tools: List[str]) → List[str]
    │   └─ get_violations(tool_id: str) → List[str]
    │
    ├─ class AgentState:
    │   ├─ auth_scopes: Set[str]
    │   ├─ executed_tools: List[str]
    │   ├─ milestones: Set[str]
    │   └─ turn_counter: int
    │
    └─ Precondition Registry:
        ├─ Stored in: resources table as JSON in body_text
        └─ Format: {"preconditions": [...]}

2.3 Integrate with intent_router.py
    ├─ Modify IntentRouter.route():
    │   BEFORE: route(self, query) → List[RoutingResult]
    │   AFTER:  route(self, query, state: Optional[AgentState]) → List[RoutingResult]
    │
    └─ Logic:
        candidates = [t for t in all_tools if ISO(t) >= threshold]
        gated = [t for t in candidates if precondition_engine.check(t.id)]
        active = top_k(gated, by=ISO_score)
        return active
```

### Guardrails
- [ ] Preconditions are OPTIONAL (tools without them pass through)
- [ ] State is reset per-turn by default
- [ ] Auth scopes are checked but not enforced (advisory)

---

## Stack 3: Two-Phase Lazy Schema Loader

### Objective
Eliminate the "Tools Tax" by keeping only compact summaries in context and promoting full JSON schemas on-demand only for the active gated set.

### Architecture

```
PHASE 1: Summary Pool  (always resident, ~60 tokens/tool)
┌─────────────────────────────────────────────────────────────┐
│  tool_001: "Search GitHub issues by label and assignee"       │
│  tool_002: "Send Slack message to channel with attachments"   │
│  tool_003: "Query PostgreSQL database with parameterized SQL" │
│  ... (1,097 tools, ~65k tokens total)                         │
│  ─────────────────────────────────────────────────────────    │
│  [PROMPT CACHE → first turn only, cached thereafter]         │
└─────────────────────────────────────────────────────────────┘

PHASE 2: Schema Promotion  (per-turn, on-demand)
┌─────────────────────────────────────────────────────────────┐
│  Active Set A_t = {tool_001, tool_045, tool_102}              │
│                                                               │
│  tool_001: {                                                  │
│    "name": "search_github_issues",                            │
│    "parameters": {                                            │
│      "label": {"type": "string"},                             │
│      "assignee": {"type": "string"},                          │
│      "state": {"enum": ["open", "closed"]}                    │
│    }                                                          │
│  }                                                            │
│  ... (k=10 tools, ~4.7k tokens total)                        │
└─────────────────────────────────────────────────────────────┘
```

### Tasks

```
3.1 Port lazy_loader.py to NEXUS
    ├─ File: /home/roshan/nexus/server/tool_attention/lazy_loader.py
    ├─ Reference: tool-attention/code/lazy_loader.py (55 lines)
    ├─ Modifications:
    │   ├─ Use SQLite as schema registry:
    │   │   schema = conn.execute("SELECT body_text FROM resources WHERE name=?").fetchone()
    │   ├─ LRU cache: capacity=512 (paper uses 256)
    │   └─ Fallback to MCP server fetch if not in registry:
    │       schema = mcp_client.call("tools/list", tool_id)
    │
    └─ class LazySchemaLoader:
        ├─ get(tool_id) → dict (JSON schema)
        ├─ get_summary(tool_id) → str (≤60 tokens)
        ├─ get_many(tool_ids: List[str]) → Dict[str, dict]
        └─ cache_stats() → Dict (hit_rate, size, evictions)

3.2 Schema Cache with MCP Fallback
    ├─ Primary: In-memory LRU (capacity=512)
    ├─ Secondary: SQLite registry (resources.body_text)
    ├─ Tertiary: MCP server live fetch (via mcp_client)
    └─ Cache stats logged to mcp_sync_log

3.3 Summary Pool Generator
    ├─ Script: generate_summaries.py
    ├─ Logic:
    │   For each resource:
    │     summary = name.replace('_', ' ').replace('-', ' ') + ": " + description[:200]
    │     truncate to 60 tokens using tiktoken
    │     store in vector_store.summaries
    │
    └─ Output: mapping of tool_id → summary string

3.4 Summary → Schema Promotion Logic
    ├─ In: ToolAttention.before_model()
    ├─ Step 1: Get active_ids from IntentRouter (ISO + gating)
    ├─ Step 2: Fetch summaries for ALL tools (Phase 1)
    ├─ Step 3: Fetch schemas ONLY for active_ids (Phase 2)
    ├─ Step 4: Render prompt:
    │     system_prompt + summary_pool + active_schemas + history + user_query
    └─ Token accounting:
        t_total = t_phase1 + t_phase2 = Σ(summary_len) + Σ(schema_len[active])
        Expected: 4.8k (Phase 1) + 2.4k (Phase 2) = 7.2k per first turn
        With prompt caching: 2.4k per subsequent turn

3.5 Token Accounting Benchmark
    ├─ Measure: tokens per turn (naive vs Phase 1 vs Phase 2)
    ├─ Expected:
    │   Method                 | tokens/turn | reduction
    │   Naive Full-Schema      | 47,300      | 0.0%
    │   Phase 1 only           | 4,800       | 89.9%
    │   Phase 2 only           | 2,400       | 94.9%
    │   First turn (P1+P2)     | 7,200       | 84.8%
    │   With caching (steady)  | 2,400       | 94.9%
    │
    └─ Result: 94.9% total token reduction on NEXUS 1,097 resources
```

### Guardrails
- [ ] Hallucination gate must reject tools not in active set
- [ ] Prompt caching requires Phase 1 content to be stable
- [ ] LRU cache invalidated on MCP sync (UPDATE/DELETE)

---

## Stack 4: Middleware Orchestrator

### Objective
Create the ToolAttention orchestrator that wires together ISO scoring, gating, lazy loading, and hallucination rejection into a single middleware pipeline.

### Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          TOOL ATTENTION MIDDLEWARE                           │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  before_model(query, state):                                                 │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  1. query → ISOEmbedder → query_embedding                           │   │
│  │  2. query_embedding × FAISS index → ISO scores for 1,097 tools     │   │
│  │  3. Threshold: keep tools with ISO ≥ 0.28                          │   │
│  │  4. State gate: keep tools satisfying preconditions                 │   │
│  │  5. Top-k: select k=10 with highest ISO scores                     │   │
│  │  6. Phase 1: gather summaries from vector_store                     │   │
│  │  7. Phase 2: lazy-load schemas for active set                      │   │
│  │  8. Render prompt: system + summaries + schemas + history + query   │   │
│  │  9. Return {active_ids, render_result}                             │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  after_model(response, active_ids):                                          │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  1. Parse tool calls from response body                             │   │
│  │  2. FOR each tool_call:                                             │   │
│  │       IF tool_id NOT in active_ids:                                 │   │
│  │         RETURN structured error: "tool <id> not available this turn"│   │
│  │  3. RETURN None (no rejection needed)                               │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Tasks

```
4.1 Create tool_attention.py
    ├─ File: /home/roshan/nexus/server/tool_attention/tool_attention.py
    ├─ Reference: tool-attention/code/tool_attention.py (70 lines)
    ├─ Extensions:
    │   ├─ Integration with existing AutoInvoke.execute_with_reflection()
    │   ├─ Integration with HierarchicalRouter.route()
    │   └─ Logging to routing_log DB table
    │
    └─ class ToolAttention:
        ├─ __init__(store, loader, router, iso_embedder, precond_engine)
        ├─ before_model(query, state=None) → AttentionResult
        ├─ after_model(active_ids, model_response) → Optional[str]
        └─ full_pipeline(query, state=None) → Dict

4.2 before_model() Middleware
    ├─ Input: query (str), state (Optional[AgentState])
    ├─ Steps: ISO → Threshold → Gate → Top-k → Phase 1 → Phase 2
    ├─ Output: AttentionResult {
    │     active: List[RoutingResult],
    │     summaries_pool: Dict[str, str],
    │     full_schemas: Dict[str, dict],
    │     phase1_tokens: int,
    │     phase2_tokens: int
    │ }
    └─ Latency target: <500ms end-to-end

4.3 after_model() Hallucination Gate
    ├─ Input: active_ids (List[str]), model_response (str)
    ├─ Parse tool call from response using regex/JSON
    ├─ Validate: requested_tool in active_ids
    ├─ On rejection:
    │     Return: 'tool_not_available: "{tool_id}". Available: {list(active_ids)} '
    │     Log: INSERT INTO routing_log(outcome='hallucination_rejected')
    └─ On pass: Return None (let execution proceed)

4.4 Wire into nexus-mcp-enhanced.py
    ├─ Add new MCP tool: tool_attention_route(query, project) → str
    ├─ Modify existing: auto_route(task, project) to use ToolAttention
    ├─ Modify existing: trigger_on_event() to use ToolAttention
    ├─ Add: set_agent_state(auth_scopes, milestones) → str
    └─ Add: tool_attention_status() → str (health + token savings)

4.5 LangGraph Middleware Contract
    ├─ Wire into orchestrator.py:
    │   graph.add_middleware(
    │       before_model=ta.before_model,
    │       after_model=ta.after_model
    │   )
    └─ Phase 1 (summaries) → stable prompt prefix (cache-friendly)
        Phase 2 (active schemas) → before user message
```

### Guardrails
- [ ] All existing v2 tools must continue working
- [ ] Token counts logged for cost tracking
- [ ] Hallucination gate is deterministic (no ML involved)

---

## Stack 5: Testing & Validation

### Objective
Ensure correctness, performance, and no regressions.

### Test Matrix

```
Test Suite                        | Type       | Files     | Expected
──────────────────────────────────┼────────────┼───────────┼────────────────────
test_vector_store.py             | Unit       | 1         | FAISS builds + searches
test_iso_embedder.py             | Unit       | 1         | 384-d output, cos works
test_intent_router.py            | Unit       | 1         | ISO ≥ θ filters correctly
test_lazy_loader.py              | Unit       | 1         | LRU hits/misses, MCP fallback
test_precondition.py              | Unit       | 1         | Auth/tool/milestone gates
test_tool_attention.py           | Unit       | 1         | before+after_model flow
test_hallucination_gate.py       | Unit       | 1         | Rejects unknown tools
──────────────────────────────────┼────────────┼───────────┼────────────────────
test_full_pipeline.py            | Integration| 1         | Query→ISO→Gate→Load→Hall
test_regression_v2.py             | Regression | 1         | find_skills() still works
──────────────────────────────────┼────────────┼───────────┼────────────────────
benchmark_token_reduction.py     | Benchmark  | 1         | Token counts: before/after
benchmark_iso_vs_tdwa.py          | Benchmark  | 1         | P@5, R@5, nDCG@5
benchmark_latency.py              | Benchmark  | 1         | P50/P95/P99 latency
──────────────────────────────────┼────────────┼───────────┼────────────────────
stress_mcp_sync.py               | Stress     | 1         | 100 concurrent updates
stress_1097_resources.py          | Stress     | 1         | All resources in FAISS
```

### Expected Benchmark Results

```
┌──────────────────────────┬───────────┬──────────┐
│ Metric                   │ TDWA (v2) │ ISO (v3) │
├──────────────────────────┼───────────┼──────────┤
│ Precision@5              │ 0.42      │ 0.78     │
│ Recall@5                 │ 0.38      │ 0.83     │
│ nDCG@5                   │ 0.35      │ 0.81     │
│ Latency P50              │ ~5ms      │ ~85ms    │
│ Tokens per turn (1097)   │ 47,300    │ 2,400    │
│ Context utilization      │ 24%       │ 91%      │
└──────────────────────────┴───────────┴──────────┘
```

---

## Stack 6: Documentation & Deployment

### Objective
Ship with full documentation, rollback plan, and monitoring.

### Tasks

```
6.1 Update CLAUDE.md
    ├─ Add v3 autonomous commands:
    │   ├─ tool_attention_route(query) → ISO-routed tool selection
    │   ├─ set_agent_state(scopes, milestones) → precondition state
    │   ├─ tool_attention_status() → token savings report
    │   └─ tool_attention_stats() → embedding/gating statistics
    │
    └─ Nexus Auto-Routing table updated

6.2 Update auto-routing.md Agent Skill
    ├─ Document Tool Attention mechanism
    ├─ Add two-phase lazy loading explanation
    ├─ Add hallucination gate behavior
    └─ Add precondition DSL reference

6.3 Performance Report
    ├─ Script: generate_performance_report.py
    ├─ Sections:
    │   1. Token reduction (before → after)
    │   2. Latency budget (end-to-end)
    │   3. Success rate comparison
    │   4. Hallucination gate activation rate
    └─ Output: /home/roshan/nexus/logs/performance_report_v3.json

6.4 Rollback Plan
    ├─ Phase 1: Disable Tool Attention → revert to v2 auto_route
    │   SQL: UPDATE resources SET status='active' WHERE status='ta_enabled'
    │   Python: auto_route = v2_auto_route
    │
    ├─ Phase 2: Delete FAISS index → revert to TDWA fallback
    │   Bash: rm -rf /home/roshan/nexus/db/faiss/
    │   hierarchy_router.py: ISO import fail → use TDWA
    │
    └─ Phase 3: Full revert → pip uninstall + rm tool_attention/
        Pip: pip uninstall sentence-transformers faiss-cpu
        Dir: rm -rf /home/roshan/nexus/server/tool_attention/
```

---

## Implementation Order (Sequential)

```
WEEK 1: Foundation
  Day 1-2:   Stack 0 (install deps, port code, fix descriptions)
  Day 3-4:   Stack 1.1-1.4 (ISO embedder, FAISS index, calibration)
  Day 5:     Stack 1.5-1.6 (replace TDWA, benchmark)
  └── Gate: FAISS index built, ISO score verified

WEEK 2: Core Mechanisms
  Day 1-2:   Stack 2 (precondition engine + integration)
  Day 3-4:   Stack 3 (lazy loader + summary generator)
  Day 5:     Stack 3.5 (token benchmark)
  └── Gate: Token reduction measured ≥90%

WEEK 3: Integration
  Day 1-2:   Stack 4.1-4.3 (middleware orchestrator)
  Day 3-4:   Stack 4.4-4.6 (MCP tools, LangGraph wiring, E2E)
  Day 5:     Stack 5 (tests, benchmark, regression)
  └── Gate: All tests pass, v2 regression green

WEEK 4: Ship
  Day 1-2:   Stack 5 continued (stress test, ablation)
  Day 3:     Stack 6 (docs, CLAUDE.md, agent skill)
  Day 4:     Production deployment + monitoring
  Day 5:     Buffer + bug fixes
  └── Gate: Performance report generated, rollback plan ready
```

---

## Resource Map

| Task | Invoke Skill | When |
|------|-------------|------|
| Stack 0: Install deps | `/careful` | Before pip install |
| Stack 1: FAISS/embed | `/tdd-workflow` | During implementation |
| Stack 2: Gating | `/security-review` | Auth precondition design |
| Stack 3: Lazy loader | `/review` | Code review |
| Stack 4: Middleware | `/health` | Post-deploy |
| Stack 5: Testing | `/qa` | Before merge |
| Stack 6: Documentation | `/document-release` | After ship |

---

## Verdict

```
┌─────────────────────────────────────────────────────────────────────────┐
│ APPROVAL REQUIRED BEFORE PROCEEDING                                     │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ✅ Ready. Plan is comprehensive and sequenced.                          │
│                                                                          │
│  Total work: 6 stacks, ~14 sub-phases                                    │
│  Estimated delivery: 4 weeks                                             │
│  Expected token reduction: 47.3k → 2.4k (94.9%)                         │
│  Fallback: TDWA keyword matching always available                        │
│                                                                          │
│  Type "go" to start STACK 0 (foundation).                               │
│  Type "modify" to adjust scope or order.                                 │
│  Type "skip-stack N" to skip a specific stack.                           │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```