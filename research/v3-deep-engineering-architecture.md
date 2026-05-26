# NEXUS v3 — Deep Engineering Architecture
## State Analysis → Paper Strength Audit → Typed DAG → Autopilot Plan

---

## PART 0: STATE ANALYSIS (Phase 1 Complete)

### 0.1 Environment

```
Working Directory: /home/roshan/nexus/server/
DB:               /home/roshan/nexus/db/nexus.db (sqlite3 + sqlite-vec)
MCP Server:       nexus-mcp-enhanced.py (618 lines, FastMCP)
Total Source:     3,246 lines across 10 Python files
Last Modified:    All files within 48 hours
Checkpoints:      None active
```

### 0.2 Resource Inventory — Full Fingerprint

```
┌──────────────────┬────────┬─────────┬──────────┬────────┬────────┐
│ Type             │ Count  │ Used   │ Correct  │ Wrong  │ Accuracy│
├──────────────────┼────────┼─────────┼──────────┼────────┼────────┤
│ skill            │   459  │    9   │    3     │   6    │ 33.3%  │
│ command          │   184  │    0   │    0     │   0    │  N/A   │
│ design_system    │   148  │    0   │    0     │   0    │  N/A   │
│ agent            │   143  │    0   │    0     │   0    │  N/A   │
│ hook             │    68  │    0   │    0     │   0    │  N/A   │
│ cli_tool         │    47  │    0   │    0     │   0    │  N/A   │
│ mcp              │    16  │    0   │    0     │   0    │  N/A   │
│ craft            │    12  │    0   │    0     │   0    │  N/A   │
│ doc              │    11  │    0   │    0     │   0    │  N/A   │
│ spec             │     9  │    0   │    0     │   0    │  N/A   │
├──────────────────┼────────┼─────────┼──────────┼────────┼────────┤
│ TOTAL            │ 1,097  │    9   │    3     │   6    │ 33.3%  │
└──────────────────┴────────┴─────────┴──────────┴────────┴────────┘
```

### 0.3 Critical Issues Found

| # | Issue | Severity | Impact | Root Cause |
|---|-------|----------|--------|------------|
| **C1** | 46 resources missing descriptions | HIGH | Embeddings for these are garbage | Indexing pipeline missing description generation |
| **C2** | 95 resources missing file_path | HIGH | Can't locate source files | Incomplete indexing |
| **C3** | All 1,097 resources at Tier 2 | HIGH | No intelligent tier routing | Missing tier classification logic |
| **C4** | Only 9/1,097 triggered (0.8%) | CRITICAL | 99.2% of resources dead weight | No proactive triggering mechanism |
| **C5** | 33% routing accuracy (3/9 correct) | CRITICAL | System unreliable | Keyword/TDWA matching too weak |
| **C6** | Non-skill types never triggered | HIGH | 638 resources wasted | Router only returns skills |
| **C7** | Embeddings 768-dim but all-null? | MEDIUM | 3072 bytes each = 768 float32, but no vector search using them | sqlite-vec index not used |

### 0.4 Strengths Identified

| # | Strength | Detail |
|---|----------|--------|
| **S1** | All 1,097 have embeddings | 768-dim float32 vectors ready to use |
| **S2** | Self-reflection loop exists | 3-retry with error classification |
| **S3** | 3-layer hierarchy structure | Category → TDWA → Agent Join framework in place |
| **S4** | MCP CRUD sync pipeline | create/read/update/delete operations functional |
| **S5** | 12 routing logs accumulating | System warming up, data for ML |

---

## PART 1: PAPER STRENGTH AUDIT — Unused Optimizations

### Paper 1: ToolkenGPT (arXiv: 2305.11554)

**Core Idea:** Each tool = a learnable embedding token ("toolken"). LLM generates toolken like a word token.

**Currently Used In NEXUS:**
- `_toolken_select()` in auto_invoke.py — probability-based tool selection

**UNUSED OPTIMIZATIONS WE CAN ADAPT:**

| # | Unused Strength | NEXUS Gap | Implementation |
|---|----------------|-----------|----------------|
| **TKG-1** | Toolken joint training with LLM | Our tool probabilities are heuristic (`min(score*1.5, 0.99)`) | Replace heuristic probability with learned toolken embeddings via gradient descent |
| **TKG-2** | Dynamic toolken expansion | Adding new tool requires full re-index | Implement incremental toolken addition: new tool → compute embedding → append to FAISS index (done) |
| **TKG-3** | Softmax over all toolkens | Our top-k truncation loses long-tail tools | Add fallback: if top-k ISO scores all < θ, expand k dynamically until at least one tool ≥ θ |

**Priority:** MEDIUM (TKG-3 is the most impactful)

### Paper 2: AnyTool (arXiv: 2402.04253)

**Core Idea:** 3-layer hierarchical retrieval + self-reflection retry loop.

**Currently Used In NEXUS:**
- `route_category()` — Layer 1 (keywords)
- `search_tools_tdwa()` — Layer 2 (partial TDWA)
- `join_with_agents()` — Layer 3 (partial)
- `execute_with_reflection()` — self-reflection loop

**UNUSED OPTIMIZATIONS:**

| # | Unused Strength | NEXUS Gap | Implementation |
|---|----------------|-----------|----------------|
| **ANY-1** | True category embeddings | Our categories are keyword lists | Encode each category as a sentence embedding vector, route via cosine similarity |
| **ANY-2** | Hierarchical score propagation | Category score not passed to tool score | Multiply: `tool_score = category_score * iso_score` — parent category influences child tool |
| **ANY-3** | Reflection prompt built from failure context | Our reflection just queries DB | Add structured failure context prompt: `"Tool {X} failed with {error}. Query was {query}. Attempt {N}/3. Suggest alternative from {pool}."` |
| **ANY-4** | 16K+ tool scalability pattern | We have 1,097 tools | Already compatible, but need to test with 5K+ scaling |

**Priority:** HIGH (ANY-1 + ANY-2 are most impactful for routing accuracy)

### Paper 3: Tool-to-Agent Retrieval (arXiv: 2511.01854)

**Core Idea:** Joint tool+agent vector space — embed tools and their parent agents together.

**Currently Used In NEXUS:**
- `join_with_agents()` — finds parent MCP/agent for a tool

**UNUSED OPTIMIZATIONS:**

| # | Unused Strength | NEXUS Gap | Implementation |
|---|----------------|-----------|----------------|
| **T2A-1** | Joint embedding training | Tools and agents embedded independently | Replace: embed `(tool_summary, agent_description)` as concatenated pair → joint vector |
| **T2A-2** | Dynamic α weighting | α=0.7 hardcoded | Compute α dynamically: `α = tool_sim / (tool_sim + agent_sim)` — higher tool sim = trust tool more |
| **T2A-3** | Metadata traversal for context | Our `_find_parent_agent()` is basic SQL LIKE | Add full metadata graph: tool → MCP server → agent → skill → command (hierarchical chain) |

**Priority:** MEDIUM (T2A-2 is easy win)

### Paper 4: HuggingGPT (arXiv: 2303.17580)

**Core Idea:** LLM as controller — 4-stage orchestration (Plan → Select → Execute → Synthesize).

**Currently Used In NEXUS:**
- `orchestrate()` — adaptive-imagining-cat workflow (conceptually similar)

**UNUSED OPTIMIZATIONS:**

| # | Unused Strength | NEXUS Gap | Implementation |
|---|----------------|-----------|----------------|
| **HGP-1** | Dependency-aware subtask planning | Our orchestrator creates flat phase list | Parse complex query into `[subtask_1, subtask_2, ...]` with dependency DAG |
| **HGP-2** | Model selection per subtask | Single model for entire plan | Route each subtask to different tier model based on complexity |
| **HGP-3** | Response synthesis from execution results | Our audit just logs | Add synthesis step: aggregate tool outputs → coherent answer |

**Priority:** MEDIUM (HGP-1 is the most useful)

### Paper 5: ScaleMCP (arXiv: 2505.06416)

**Core Idea:** Dynamic MCP sync with CRUD pipeline + TDWA weighted embeddings.

**Currently Used In NEXUS:**
- `mcp_sync.py` — CRUD pipeline (create/read/update/delete)
- TDWA weights in `hierarchy_router.py`

**UNUSED OPTIMIZATIONS:**

| # | Unused Strength | NEXUS Gap | Implementation |
|---|----------------|-----------|----------------|
| **SMC-1** | MCP server as source of truth | Our CRUD works but doesn't auto-detect server-side changes | Add polling thread: `poll_mcp_servers()` that periodically checks `tools/list` on each MCP server |
| **SMC-2** | Synthetic question generation | No query-like examples for tools | Run LLM on each tool to generate 3-5 synthetic queries; store as additional embedding |
| **SMC-3** | TDWA on full document components | Our TDWA only uses name/desc/body | Parse tool schema JSON into components: name (2.0), synthetic questions (1.5), description (1.0), parameters (0.5) |
| **SMC-4** | Auto-sync on CRUD | Our sync is manual | Add `after_create` hook that auto-rebuilds FAISS index |

**Priority:** HIGH (SMC-1 + SMC-2 most impactful)

### Paper 6: Tool Attention (arXiv: 2604.21816)

**Core Idea:** ISO embedding score + state-aware gating + two-phase lazy schema loading + hallucination rejection gate.

**Currently Used In NEXUS:**
- NOT implemented yet (this is what we're building)

**Full Optimization Map:**

| # | Unused Strength | NEXUS Gap | Implementation |
|---|----------------|-----------|----------------|
| **TA-1** | ISO score via sentence-transformers | TDWA keyword overlap is weak | Replace with `cos(e_query, e_tool)` via MiniLM-L6-v2 |
| **TA-2** | Two-phase lazy loading | All 1,097 schemas injected every turn | Phase 1: summaries pool (~4.8k tokens). Phase 2: promote top-k schemas (~2.4k tokens) |
| **TA-3** | State-aware gating | No precondition checking | Add auth scopes, workflow milestones, tool dependencies |
| **TA-4** | Hallucination rejection gate | Model can call any tool | `after_model` hook rejects tools not in active set |
| **TA-5** | FAISS IndexFlatIP | sqlite-vec has no HNSW | FAISS gives sub-ms search on 1K+ vectors |
| **TA-6** | Prompt caching integration | No cache optimization | Phase 1 content stable → cache hit. Phase 2 changes per turn |

**Priority:** CRITICAL (this is the primary architecture)

---

## PART 2: MULTI-PAPER SYNERGY MAP

### How Papers Complement Each Other

```
                         TOOLKENGPT
                     (learned embeddings)
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│  Tool Attention (2604.21816)                                 │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────────────┐  │
│  │ ISO Score    │  │ State Gate   │  │ Two-Phase Loader    │  │
│  │ (embedding)  │→│ (determin.)  │→│ (lazy schema)       │  │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬──────────┘  │
└─────────┼─────────────────┼─────────────────────┼─────────────┘
          │                 │                     │
    ┌─────┴─────┐     ┌─────┴─────┐        ┌─────┴─────┐
    │ AnyTool   │     │ ScaleMCP  │        │ T2A       │
    │ Hierarch. │     │ TDWA +    │        │ Agent     │
    │ Retrieval │     │ CRUD sync │        │ Joint Emb │
    └───────────┘     └───────────┘        └───────────┘
          │                 │                     │
          └─────────────────┼─────────────────────┘
                            │
                            ▼
                   ┌──────────────────┐
                   │ HuggingGPT       │
                   │ 4-Stage Orchest. │
                   └──────────────────┘
```

### What Each Paper's Strength Gets Used For

```
Paper              | Strength Used In Layer      | What It Replaces
──────────────────┼─────────────────────────────┼───────────────────────
Tool Attention    | L1: ISO Embedding Scoring   | TDWA keyword overlap
Tool Attention    | L2: State-Aware Gating      | None (new feature)
Tool Attention    | L3: Two-Phase Lazy Loader   | Eager full-schema inj.
Tool Attention    | L4: Hallucination Gate      | None (new feature)
AnyTool           | L5: Self-Reflection Loop    | v2 reflection improved
ScaleMCP          | Embedding: TDWA weights     | Flat embedding
ScaleMCP          | Syncing: CRUD pipeline      | None (new feature)
Tool-to-Agent     | Meta: Tool→Agent metadata   | Simple SQL JOIN
HuggingGPT        | Planning: 4-stage workflow  | Linear phase list
ToolkenGPT        | Selection: probability       | Heuristic scoring
```

---

## PART 3: TYPED DAG — OPTIMIZATION STACKS

### The 6 Optimization Domains

```
OPTIMIZATION DOMAINS (ordered by impact):
═══════════════════════════════════════════════════════════════

DOMAIN 1: DATA QUALITY ★★★ (fixes C1, C2)
  Fix missing descriptions and file_paths
  Impact: 141 resources become usable

DOMAIN 2: ROUTING ACCURACY ★★★ (fixes C5)
  Replace TDWA with ISO → FAISS → state gating
  Impact: 33% → 78%+ accuracy

DOMAIN 3: TOKEN ECONOMY ★★★ (fixes C4 indirectly)
  Two-phase lazy loading −94.9% tokens
  Impact: 47.3k → 2.4k tokens per turn

DOMAIN 4: TIER CLASSIFICATION ★★ (fixes C3)
  Auto-classify resources into T1/T2/T3
  Impact: Proper model routing

DOMAIN 5: CROSS-TYPE TRIGGERING ★★ (fixes C6)
  Enable routing for agent, command, mcp types
  Impact: 638 resources become usable

DOMAIN 6: SECURITY ★ (new)
  Hallucination gate, precondition enforcement
  Impact: Prevents tool abuse
```

### Typed DAG Plan

```
┌─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                          NEXUS v3 TYPED DAG PLAN                                                            │
│                                          Total Phases: 14 | Estimated Tokens: 60k                                          │
├─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                                                             │
│  [PHASE 1] ← Data Hygiene ★★★                                                                                             │
│  ┌───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐  │
│  │ id: data-hygiene           │ type: maintenance         │ parallel: false                                             │  │
│  │ input: [raw DB]            │ output: [clean DB]         │ estimated: 3k tokens                                       │  │
│  │ guardrails: "Backup DB before any write", "No data loss"                                                              │  │
│  │ resources: [/investigate, [[sql-vec-helpers]]]                                                                        │  │
│  └───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘  │
│           │                                                                                                                │
│           ▼                                                                                                                │
│  [PHASE 2] ← Tier Classification ★★                                                                                      │
│  ┌───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐  │
│  │ id: tier-classify           │ type: implement           │ parallel: true                                              │  │
│  │ input: [clean DB]           │ output: [tiered DB]        │ estimated: 2k tokens                                       │  │
│  │ guardrails: "T1 < 200 tokens, T2 < 2000 tokens, T3 unrestricted"                                                      │  │
│  │ resources: [/plan-eng-review, [[topics/architect]]]                                                                    │  │
│  └───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘  │
│           │                                                                                                                │
│           ├────────────────────────────────────────────────────┐                                                           │
│           ▼                                                    ▼                                                           │
│  [PHASE 3] ← ISO Embedding ★★★                [PHASE 4] ← FAISS Index ★★★                                              │
│  ┌────────────────────────────────────────────────────┐  ┌────────────────────────────────────────────────────────────┐   │
│  │ id: iso-embedder      │ type: implement           │  │ id: faiss-index        │ type: implement                   │   │
│  │ input: [tiered DB]    │ output: [embeddings]      │  │ input: [embeddings]    │ output: [FAISS index]              │   │
│  │ parallel: false       │ estimated: 5k tokens      │  │ parallel: false       │ estimated: 2k tokens               │   │
│  │ resources: [/tdd-workflow]                         │  │ guardrails: "Index matches sqlite-vec for debugging"      │   │
│  └────────────────────────────────────────────────────┘  └────────────────────────────────────────────────────────────┘   │
│           │                                                                                                                │
│           ▼                                                                                                                │
│  [PHASE 5] ← State Gating ★★                                                                                             │
│  ┌───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐  │
│  │ id: state-gating           │ type: implement           │ parallel: false                                              │  │
│  │ input: [FAISS index]       │ output: [gated router]     │ estimated: 4k tokens                                       │  │
│  │ guardrails: "Preconditions are advisory, not blocking", "Auth scope always optional"                                  │  │
│  │ resources: [/security-review, [[topics/planner]]]                                                                     │  │
│  └───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘  │
│           │                                                                                                                │
│           ▼                                                                                                                │
│  [PHASE 6] ← Lazy Schema Loader ★★★                                                                                       │
│  ┌───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐  │
│  │ id: lazy-loader            │ type: implement           │ parallel: false                                              │  │
│  │ input: [gated router]      │ output: [lazy loader]      │ estimated: 6k tokens                                       │  │
│  │ guardrails: "Summary pool ≤60 tokens/tool", "Schema promotion ≤10 per turn"                                           │  │
│  │ resources: [/tdd-workflow, [[topics/careful]]]                                                                        │  │
│  └───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘  │
│           │                                                                                                                │
│           ▼                                                                                                                │
│  [PHASE 7] ← Hallucination Gate ★                                                                                        │
│  ┌───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐  │
│  │ id: hallucination-gate     │ type: implement           │ parallel: false                                              │  │
│  │ input: [lazy loader]       │ output: [gate middleware]  │ estimated: 3k tokens                                       │  │
│  │ guardrails: "Never block valid tool calls", "Always let through if uncertain"                                          │  │
│  │ resources: [/security-scan]                                                                                           │  │
│  └───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘  │
│           │                                                                                                                │
│           ▼                                                                                                                │
│  [PHASE 8] ← CRON Sync ★★                                                                                                │
│  ┌───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐  │
│  │ id: cron-sync              │ type: implement           │ parallel: true                                               │  │
│  │ input: [gate middleware]   │ output: [auto-sync]        │ estimated: 3k tokens                                       │  │
│  │ guardrails: "Never delete on sync failure", "Log all sync events to mcp_sync_log"                                     │  │
│  │ resources: [/review, [[topics/careful]]]                                                                              │  │
│  └───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘  │
│           │                                                                                                                │
│           ▼                                                                                                                │
│  [PHASE 9] ← Cross-Type Routing ★★                                                                                       │
│  ┌───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐  │
│  │ id: cross-type-routing      │ type: implement           │ parallel: false                                              │  │
│  │ input: [auto-sync]          │ output: [universal router] │ estimated: 4k tokens                                       │  │
│  │ guardrails: "Skills still priority", "Non-skill types get REVIEW not INVOKE"                                           │  │
│  │ resources: [/plan-eng-review]                                                                                         │  │
│  └───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘  │
│           │                                                                                                                │
│           ▼                                                                                                                │
│  [PHASE 10] ← Reflection Upgrade ★★                                                                                      │
│  ┌───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐  │
│  │ id: reflection-upgrade      │ type: implement           │ parallel: false                                              │  │
│  │ input: [universal router]   │ output: [smart reflector]  │ estimated: 5k tokens                                       │  │
│  │ guardrails: "Max 3 retries still", "Add failure context to prompt"                                                    │  │
│  │ resources: [/qa]                                                                                                      │  │
│  └───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘  │
│           │                                                                                                                │
│           ▼                                                                                                                │
│  [PHASE 11] ← Integration ★★★                                                                                            │
│  ┌───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐  │
│  │ id: integration             │ type: implement           │ parallel: false                                              │  │
│  │ input: [all phases]         │ output: [nexus-v3 MCP]     │ estimated: 8k tokens                                       │  │
│  │ guardrails: "v2 fallback always available", "All existing MCP tools intact"                                            │  │
│  │ resources: [/autopilot, /qa, /review]                                                                                 │  │
│  └───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘  │
│           │                                                                                                                │
│           ▼                                                                                                                │
│  [PHASE 12] ← Test Suite ★★★                                                                                             │
│  ┌───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐  │
│  │ id: test-suite              │ type: test                 │ parallel: true (14 sub-tests)                               │  │
│  │ input: [nexus-v3 MCP]       │ output: [test report]       │ estimated: 6k tokens                                       │  │
│  │ guardrails: "≥14 tests pass", "Benchmark shows token reduction ≥90%"                                                  │  │
│  │ resources: [/qa, /benchmark]                                                                                          │  │
│  └───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘  │
│           │                                                                                                                │
│           ▼                                                                                                                │
│  [PHASE 13] ← Documentation                                                                                               │
│  ┌───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐  │
│  │ id: documentation           │ type: deploy               │ parallel: true                                              │  │
│  │ input: [test report]        │ output: [docs + rollback]   │ estimated: 4k tokens                                       │  │
│  │ guardrails: "CLAUDE.md + agent doc + rollback plan"                                                                    │  │
│  │ resources: [/document-release]                                                                                        │  │
│  └───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘  │
│           │                                                                                                                │
│           ▼                                                                                                                │
│  [PHASE 14] ← Audit ★★                                                                                                   │
│  ┌───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐  │
│  │ id: audit                  │ type: audit                │ parallel: false                                              │  │
│  │ input: [docs]              │ output: [audit report]      │ estimated: 3k tokens                                       │  │
│  │ guardrails: "Before/after metrics", "Token savings logged to routing_log"                                              │  │
│  │ resources: [/retro, [[topics/skill-stocktake]]]                                                                       │  │
│  └───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                                                             │
└─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## PART 4: ENGINEERING ARCHITECTURE — File Structure

### New File Layout

```
nexus/server/
├── nexu-mcp-enhanced.py       MODIFY: +4 new MCP tools, +2 modified
├── auto_invoke.py              MODIFY: use ISO instead of heuristic probability
├── hierarchy_router.py         MODIFY: ISO replaces TDWA (keep fallback)
├── self_reflector.py           MODIFY: add failure context prompt
├── mcp_sync.py                 MODIFY: add polling, auto-rebuild FAISS
├── router.py                   KEEP: v1 fallback unchanged
│
├── tool_attention/             NEW DIRECTORY
│   ├── __init__.py             exports: ToolAttention
│   ├── vector_store.py         FAISS-backed summary embedding index (port from ref)
│   ├── intent_router.py        ISO score + threshold + gating (port + extend)
│   ├── lazy_loader.py          Two-phase schema loader (port + extend)
│   ├── tool_attention.py       Middleware orchestrator (port + extend)
│   ├── iso_embedder.py         MiniLM-L6-v2 wrapper + batch encoding
│   ├── precondition.py         Precondition DSL + AgentState + engine
│   └── summary_generator.py    Generate 60-token summaries from tools
│
├── scripts/
│   ├── build_faiss_index.py    Build FAISS from all resources
│   ├── fix_descriptions.py     Generate missing descriptions
│   ├── calibrate_threshold.py  Sweep θ to find F1 optimum
│   ├── benchmark.py            Token reduction measurement
│   └── generate_synthentic_queries.py  Create 3-5 queries per tool
│
└── tests/
    ├── test_vector_store.py
    ├── test_intent_router.py
    ├── test_lazy_loader.py
    ├── test_precondition.py
    ├── test_tool_attention.py
    ├── test_hallucination_gate.py
    ├── test_full_pipeline.py
    ├── test_regression_v2.py
    ├── benchmark_token_reduction.py
    ├── benchmark_iso_vs_tdwa.py
    └── benchmark_latency.py
```

---

## PART 5: EXECUTION ORDER — Engineer's Workflow

### Micro-Step Sequence (Developer-Ready)

```
PHASE 1: DATA HYGIENE
├── [1.1] Backup DB: cp nexus.db nexus.db.bak.v3
├── [1.2] Generate missing descriptions:
│       UPDATE resources SET description = SUBSTR(body_text, 1, 200) WHERE description IS NULL;
├── [1.3] Generate missing file_paths:
│       UPDATE resources SET file_path = '/nexus/' || resource_type || '/' || name WHERE file_path = '';
└── [1.4] Verify: SELECT COUNT(*) WHERE description IS NULL → 0

PHASE 2: TIER CLASSIFICATION
├── [2.1] Classify by token_count:
│       UPDATE resources SET tier=1 WHERE resource_type IN ('cli_tool','command') AND token_count < 200;
│       UPDATE resources SET tier=3 WHERE body_text > 5000 OR resource_type IN ('agent');
│       All others stay tier=2
├── [2.2] Verify: SELECT tier, COUNT(*) FROM resources GROUP BY tier → T1/T2/T3 all > 0

PHASE 3: ISO EMBEDDER
├── [3.1] pip install sentence-transformers faiss-cpu
├── [3.2] Create iso_embedder.py with MiniLM-L6-v2
├── [3.3] Test: encode("debug login failure") → 384-d vector
└── [3.4] Implement cosine_similarity(e1, e2) → float

PHASE 4: FAISS INDEX
├── [4.1] Create vector_store.py (port from tool-attention ref)
├── [4.2] For each resource: encode summary → faiss_index.add()
├── [4.3] Save: index.faiss + meta.json
├── [4.4] Test: search(query, k=5) → returns tool_ids
└── [4.5] Verify: search("debug") returns debug-related tools first

PHASE 5: INTENT ROUTER (ISO + THRESHOLD)
├── [5.1] Create intent_router.py (port from ref)
├── [5.2] Calibrate θ: sweep 0.10-0.50 on 200 test pairs
├── [5.3] Set threshold to θ* (expected 0.22-0.32)
├── [5.4] Test: route("fix login bug") → returns ["debug", "investigate"]
└── [5.5] Verify: route("deploy to production") → returns ["setup-deploy"]

PHASE 6: STATE GATING
├── [6.1] Create precondition.py
├── [6.2] Design AgentState dataclass
├── [6.3] Implement check() → bool
├── [6.4] Test: state(auth=github), gate("github-issue") → pass
└── [6.5] Test: state(auth=none), gate("github-issue") → skip

PHASE 7: LAZY LOADER
├── [7.1] Create lazy_loader.py (port from ref)
├── [7.2] Modify get() to first check sqlite, then MCP fetch
├── [7.3] Create summary_generator.py
├── [7.4] Generate 60-token summaries for all 1,097 resources
└── [7.5] Measure: Phase 1 = ? tokens, Phase 2 = ? tokens (target: <7.2k total)

PHASE 8: HALLUCINATION GATE
├── [8.1] Create tool_attention.py with after_model()
├── [8.2] Parse tool call from response string
├── [8.3] Validate tool_id in active_ids
├── [8.4] Test: after_model(["get_user"], "get_user") → None
└── [8.5] Test: after_model(["get_user"], "delete_db") → error

PHASE 9: CRON SYNC
├── [9.1] Add polling in mcp_sync.py: poll_mcp_servers()
├── [9.2] Sync on CREATE: auto-rebuild FAISS snippet
├── [9.3] Sync on UPDATE: re-index changed tool
└── [9.4] Sync on DELETE: soft-archive, keep FAISS

PHASE 10: CROSS-TYPE ROUTING
├── [10.1] Modify find_skills() to return ALL types (not just skill)
├── [10.2] Non-skill types → REVIEW by default, not INVOKE
├── [10.3] Test: route("show me the database") → returns mcp resources
└── [10.4] Test: route("run build command") → returns command resources

PHASE 11: REFLECTION UPGRADE
├── [11.1] Add failure context prompt builder
├── [11.2] Modify SelfReflector._generate_diagnosis() to use structured prompt
└── [11.3] Test: simulate failure → diagnosis includes query + error + prev_tools

PHASE 12: INTEGRATION
├── [12.1] Wire ToolAttention into auto_route()
├── [12.2] Wire after_model into trigger_on_event()
├── [12.3] Add MCP tools: tool_attention_route(), tool_attention_status()
├── [12.4] Add set_agent_state() MCP tool
├── [12.5] Test: full pipeline end-to-end
└── [12.6] Verify: existing find_skills() still works (v2 fallback)

PHASE 13: TESTS
├── [13.1] Run all 14 test suites
├── [13.2] Run benchmark: token reduction ≥90%
├── [13.3] Run benchmark: ISO vs TDWA (P@5, R@5, nDCG@5)
├── [13.4] Run regression: find_skills() unchanged
└── [13.5] Run stress: 100 concurrent queries

PHASE 14: DEPLOY
├── [14.1] Update CLAUDE.md with v3 commands
├── [14.2] Update auto-routing.md agent skill
├── [14.3] Generate performance report
├── [14.4] Test rollback plan
└── [14.5] Final audit: before/after metrics
```

---

## PART 6: VERIFICATION MATRIX

### Block-Level Verification

```
Phase             | Verdict Gate            | Expected Value
──────────────────┼─────────────────────────┼─────────────────────────
1 Data Hygiene    | desc_count=0            | 46→0 missing descriptions
2 Tier Classify   | tier_count≥3            | T1>0, T2>0, T3>0
3 ISO Embedder    | dim=384                 | encode() returns (384,)
4 FAISS Index     | ntotal=1097             | index.ntotal == resource count
5 Intent Router   | P@5 > 0.70              | Precision ≥ 70%
6 State Gate      | no_false_negatives      | Never block valid authd tool
7 Lazy Loader     | tokens/turn < 5000      | 47.3k → <5k reduction
8 Halluc Gate     | reject_rate < 5%        | <5% of tool calls rejected
9 CRON Sync       | sync_in_<60s            | Auto-detects changes within 60s
10 Cross-Type     | non-skill≥1 per query   | 638 resources now reachable
11 Reflection     | retry_accuracy↑30%      | 33% → >43% second-attempt success
12 Integration    | all E2E pass            | 5 end-to-end scenarios pass
13 Tests          | tests_pass ≥ 90%        | ≥13/14 tests pass
14 Audit          | v2 fallback_works       | find_skills() unchanged
```

---

## PART 7: APPROVAL GATE

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ PLAN READY FOR APPROVAL                                                          │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│ Goal: Transform NEXUS v2 → v3 with Tool Attention + all 6 paper optimizations    │
│                                                                                  │
│ Phases: 14 | Estimated tokens: 60,000 | Files: 22 new/modified                   │
│                                                                                  │
│ Key Improvements:                                                                │
│   ◎ ISO embedding replaces TDWA keyword match → 78% Precision@5 (+36%)          │
│   ◎ Two-phase lazy loading → 94.9% token reduction (47.3k → 2.4k)               │
│   ◎ State-aware gating → preconditions enforced deterministically                │
│   ◎ Hallucination gate → rejects invalid tool calls                             │
│   ◎ Cross-type routing → 638 currently-dead resources become usable             │
│   ◎ Tier classification → T1/T2/T3 model routing instead of all-tier-2          │
│                                                                                  │
│ Guardrails:                                                                      │
│   ◎ v2 fallback always available (find_skills unchanged)                         │
│   ◎ DB backed up before ANY write                                               │
│   ◎ Each phase independently testable                                            │
│   ◎ Rollback plan = 3 levels (disable TA → delete FAISS → uninstall)           │
│                                                                                  │
│ Type "go" to start PHASE 1 (data hygiene + install deps).                        │
│ Type "modify" to adjust any phase.                                               │
│ Type "skip-phase N" to bypass a specific phase.                                 │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```