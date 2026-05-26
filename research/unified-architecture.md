# NEXUS v2 Unified Architecture
# Based on: ToolkenGPT + AnyTool + Tool-to-Agent + HuggingGPT + ScaleMCP

## Problem Statement
Current NEXUS has:
- Keyword-only matching (no embeddings)
- Flat search (no hierarchy)
- No self-reflection on failure
- Manual trigger required
- No MCP dynamic sync

## Target: Autonomous Tool Triggering System

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         NEXUS v2 ARCHITECTURE                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  [User Query / System Event]                                                 │
│         │                                                                     │
│         ▼                                                                     │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  PHASE 0: Context Engine (Phase 0 of adaptive-imagining-cat)        │   │
│  │  - Read CLAUDE.md for routing rules                                  │   │
│  │  - Check session state (checkpoint)                                  │   │
│  │  - Query knowledge graph for relevant skills                          │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│         │                                                                     │
│         ▼                                                                     │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  PHASE 1: INTENT PARSER (HuggingGPT Stage 1: Task Planning)        │   │
│  │  - LLM parses query into structured subtasks                        │   │
│  │  - Identifies dependencies between tasks                            │   │
│  │  - Classifies intent type (research|implement|test|deploy|audit)   │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│         │                                                                     │
│         ▼                                                                     │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  PHASE 2: HIERARCHICAL RETRIEVAL (AnyTool 3-Layer)                   │   │
│  │                                                                      │   │
│  │  ┌─ Layer 1: Category Router ──────────────────────────────────┐  │   │
│  │  │  • Cosine similarity with category embeddings                │  │   │
│  │  │  • Select top-3 relevant categories                          │  │   │
│  │  │  • Fast: ~5ms latency                                       │  │   │
│  │  └──────────────────────────────────────────────────────────────┘  │   │
│  │                                                                      │   │
│  │  ┌─ Layer 2: Tool Retriever (per category) ─────────────────────┐  │   │
│  │  │  • HNSW vector search within each category                 │  │   │
│  │  │  • Select top-5 tools per category                          │  │   │
│  │  │  • Apply TDWA embedding (ScaleMCP) for better matching      │  │   │
│  │  └──────────────────────────────────────────────────────────────┘  │   │
│  │                                                                      │   │
│  │  ┌─ Layer 3: Tool-to-Agent Join ───────────────────────────────┐  │   │
│  │  │  • Traverse metadata to parent MCP server/agent              │  │   │
│  │  │  • Combined scoring: α*tool_sim + (1-α)*agent_sim          │  │   │
│  │  │  • Rerank by joint score                                    │  │   │
│  │  └──────────────────────────────────────────────────────────────┘  │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│         │                                                                     │
│         ▼                                                                     │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  PHASE 3: TOOL SELECTION (ToolkenGPT-style)                          │   │
│  │  - Compute tool probabilities via softmax                            │   │
│  │  - P(tool_k) = softmax(h · e_k / τ)                                 │   │
│  │  - Select top tool with highest probability                         │   │
│  │  - If top tool < threshold, use hierarchical fallback               │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│         │                                                                     │
│         ▼                                                                     │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  PHASE 4: EXECUTION + SELF-REFLECTION (AnyTool)                      │   │
│  │                                                                      │   │
│  │  MAX_RETRIES = 3                                                     │   │
│  │  attempts = 0                                                        │   │
│  │                                                                      │   │
│  │  WHILE attempts < MAX_RETRIES:                                       │   │
│  │    1. Execute tool with arguments                                    │   │
│  │    2. LLM analyzes result against query                             │   │
│  │    3. IF result.successful AND satisfies(query):                    │   │
│  │         → RETURN result                                              │   │
│  │    4. LLM selects alternative tool from candidates                  │   │
│  │    5. attempts += 1                                                  │   │
│  │  END WHILE                                                           │   │
│  │  → RETURN failure with diagnostic                                   │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│         │                                                                     │
│         ▼                                                                     │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  PHASE 5: DYNAMIC SYNC (ScaleMCP CRUD)                              │   │
│  │                                                                      │   │
│  │  MCP Server Event → Pipeline                                         │   │
│  │  ├── CREATE: New tool detected → Add to vector index                │   │
│  │  ├── READ:   Tool retrieved → Merge with MCP schema                 │   │
│  │  ├── UPDATE: Schema changed → Re-index with new embedding           │   │
│  │  └── DELETE: Tool removed → Remove from vector index                 │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘

## Component Specifications

### 1. Toolken Embedding Engine
- Pre-compute embeddings for all tools/skills using sentence-transformers
- Store in sqlite-vec with HNSW index
- Support dynamic addition of new toolkens
- Parameters: embedding_model, dimension (768/1024/1536)

### 2. Category Hierarchy
- Level 1: 8-12 broad categories (coding, research, design, deploy, etc.)
- Level 2: Sub-categories (e.g., coding → [frontend, backend, testing, ...])
- Level 3: Individual tools/skills
- Category embeddings learned from tool distribution

### 3. TDWA Embedding (from ScaleMCP)
- Tool name: weight 2.0
- Synthetic questions: weight 1.5
- Description: weight 1.0
- Parameters: weight 0.5
- Final: L2 normalize

### 4. Self-Reflection Loop
- Max 3 attempts per tool invocation
- Reflection prompt: analyze success/failure against original query
- Alternative tool selection from remaining candidates

### 5. Dynamic MCP Sync
- Subscribe to MCP server change events
- Background sync pipeline with debouncing
- Conflict resolution: MCP server is source of truth

## Data Flow

```
User Query
    ↓
Intent Parser → {subtasks: [], intent_type: "", dependencies: []}
    ↓
Hierarchical Router
    ├── Category Embeddings → top-3 categories
    ├── Tool Embeddings (TDWA) → top-5 per category
    └── Tool-Agent Join → combined scores
    ↓
Toolken Selector → {tool: "", probability: 0.85, alternatives: []}
    ↓
Executor + Self-Reflection
    ├── Try tool_1
    ├── Success? → Return
    └── Failure? → Try tool_2 (max 3)
    ↓
Result + Learning
    └── Update tool success rates in DB
        └── Trigger dynamic sync if needed
```

## Implementation Priority

### Phase 1: Core Router (Week 1-2)
- Implement hierarchical retrieval (Layer 1-2)
- Add vector embeddings to existing DB
- Test with existing skills

### Phase 2: Tool Selection (Week 3)
- Implement ToolkenGPT-style selection
- Add probability thresholding
- Alternative tool fallback

### Phase 3: Self-Reflection (Week 4)
- Implement reflection loop
- Add max retry logic
- Error classification and recovery

### Phase 4: Dynamic Sync (Week 5-6)
- Implement ScaleMCP CRUD pipeline
- Add MCP server event subscription
- Test with real MCP servers

### Phase 5: Integration (Week 7-8)
- Full orchestration with adaptive-imagining-cat
- End-to-end testing
- Performance optimization