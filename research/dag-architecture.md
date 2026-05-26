# NEXUS v2 — Complete DAG Architecture + Research Paper Mapping

## Legend

| Paper | ArXiv | Abbreviation |
|-------|-------|-------------|
| ToolkenGPT | 2305.11554 | **TKG** |
| AnyTool | 2402.04253 | **ANY** |
| Tool-to-Agent Retrieval | 2511.01854 | **T2A** |
| HuggingGPT | 2303.17580 | **HGP** |
| ScaleMCP | 2505.06416 | **SMC** |

## DAG: Full Component Graph

```
┌───────────────────────────────────────────────────────────────────────────────┐
│                           TRIGGER SOURCES  [ANY]                             │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────────────┐   │
│  │ ERROR EVENT │  │ COMPLEX TASK │  │ FAIL PATTERN │  │ USER QUERY      │   │
│  │ (0.95 conf) │  │ (0.85 conf)  │  │ (0.80 conf)  │  │ (0.70 conf)     │   │
│  └──────┬──────┘  └──────┬───────┘  └──────┬───────┘  └────────┬────────┘   │
│         │                │                  │                   │             │
│         └────────────────┴──────────────────┴───────────────────┘             │
│                                    │                                          │
│                                    ▼                                          │
│                    ┌───────────────────────────────┐                          │
│                    │  auto_invoke.AutoInvoke       │  [TKG]                   │
│                    │  .detect_trigger()            │  ToolkenGPT trigger      │
│                    │  • classify trigger type      │  detection via embeddings│
│                    │  • compute confidence score   │                          │
│                    └──────────────┬────────────────┘                          │
└──────────────────────────────────┼───────────────────────────────────────────┘
                                   │
                                   ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│              LAYER 1: CATEGORY ROUTER  [ANY]  Section 3.1                     │
│                                                                               │
│  ┌───────────────────────────────────────────────────────────────────────┐   │
│  │  hierarchy_router.HierarchicalRouter.route_category()                  │   │
│  │                                                                       │   │
│  │  categories = {                                                       │   │
│  │    coding:   [code, implement, build, create, api, query, schema],    │   │
│  │    debug:    [debug, error, crash, exception, traceback, fail],       │   │
│  │    research: [research, investigate, analyze, explore, search],       │   │
│  │    design:   [design, architecture, spec, plan, mockup, pattern],     │   │
│  │    test:     [test, qa, verify, coverage, unit, e2e],                │   │
│  │    deploy:   [deploy, ship, release, push, build, ci, cd],           │   │
│  │    review:   [review, audit, check, assess, security],               │   │
│  │    refactor: [refactor, optimize, improve, cleanup, migrate]          │   │
│  │  }                                                                    │   │
│  │                                                                       │   │
│  │  → keyword overlap scoring → top-3 categories (latency: ~5ms)        │   │
│  └──────────────────┬────────────────────────────────────────────────────┘   │
│                     │                                                        │
│                     ▼                                                        │
└───────────────────────────────────────────────────────────────────────────────┘
                     │
                     ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│          LAYER 2: TDWA TOOL RETRIEVAL  [SMC]  Section 3.1                     │
│                                                                               │
│  ┌───────────────────────────────────────────────────────────────────────┐   │
│  │  hierarchy_router.HierarchicalRouter.search_tools_tdwa()               │   │
│  │                                                                       │   │
│  │  TDWA WEIGHTS (ScaleMCP):                                            │   │
│  │  ┌──────────────────────┬──────────┐                                  │   │
│  │  │ Component            │ Weight   │                                  │   │
│  │  ├──────────────────────┼──────────┤                                  │   │
│  │  │ tool_name            │   2.0    │  ← highest: unique identifier    │   │
│  │  │ description          │   1.0    │                                  │   │
│  │  │ body_text            │   0.5    │                                  │   │
│  │  │ success_rate_bonus   │ +0.002   │  ← experience-weighted          │   │
│  │  └──────────────────────┴──────────┘                                  │   │
│  │                                                                       │   │
│  │  score = Σ(weight_i * overlap_i) / |query_words| + sr/500            │   │
│  │                                                                       │   │
│  │  → top-15 tools per category (latency: ~15ms)                         │   │
│  └──────────────────┬────────────────────────────────────────────────────┘   │
│                     │                                                        │
│                     ▼                                                        │
└───────────────────────────────────────────────────────────────────────────────┘
                     │
                     ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│       LAYER 3: TOOL-TO-AGENT JOIN  [T2A]  Section 3                           │
│                                                                               │
│  ┌───────────────────────────────────────────────────────────────────────┐   │
│  │  hierarchy_router.HierarchicalRouter.join_with_agents()                │   │
│  │                                                                       │   │
│  │  FOR each tool in top-15:                                            │   │
│  │    1. Find parent MCP server: repo_name match                        │   │
│  │    2. Find parent agent: agent description match                     │   │
│  │    3. Compute combined_score:                                        │   │
│  │       combined = α * tdwa_score + (1-α) * parent_score              │   │
│  │       where α = 0.7 (tool weight)                                   │   │
│  │                                                                       │   │
│  │  → rerank by combined_score → top-5 with agent context               │   │
│  └──────────────────┬────────────────────────────────────────────────────┘   │
│                     │                                                        │
│                     ▼                                                        │
└───────────────────────────────────────────────────────────────────────────────┘
                     │
                     ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│        TOOLKEN SELECTOR  [TKG]  Section 3                                     │
│                                                                               │
│  ┌───────────────────────────────────────────────────────────────────────┐   │
│  │  auto_invoke.AutoInvoke._toolken_select()                              │   │
│  │                                                                       │   │
│  │  probability = min(score * 1.5, 0.99)                                │   │
│  │                                                                       │   │
│  │  classification:                                                      │   │
│  │    INVOKE  ← prob >= 0.68  (auto-trigger)                            │   │
│  │    REVIEW  ← 0.45 <= prob < 0.68  (read body, decide)                │   │
│  │    SKIP    ← prob < 0.45  (not relevant)                             │   │
│  │                                                                       │   │
│  │  → top-5 ranked tools with class + probability                        │   │
│  └──────────────────┬────────────────────────────────────────────────────┘   │
│                     │                                                        │
│                     ▼                                                        │
└───────────────────────────────────────────────────────────────────────────────┘
                     │
                     ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│    EXECUTOR + SELF-REFLECTION LOOP  [ANY]  Section 3.2                        │
│                                                                               │
│  ┌───────────────────────────────────────────────────────────────────────┐   │
│  │  auto_invoke.AutoInvoke.execute_with_reflection()                      │   │
│  │  self_reflector.SelfReflector.reflect()                                │   │
│  │                                                                       │   │
│  │  attempt = 0                                                          │   │
│  │  WHILE attempt < 3:                                                   │   │
│  │    │                                                                  │   │
│  │    │  tool = select_best(candidates, tried_names)                     │   │
│  │    │  result = execute_tool(tool)                                     │   │
│  │    │                                                                  │   │
│  │    │  IF result.success:                                              │   │
│  │    │    │  log_success(tool)  → UPDATE times_correct += 1             │   │
│  │    │    │  RETURN {status: 'success', attempts: N}                    │   │
│  │    │                                                                  │   │
│  │    │  ELSE ← self-reflection triggered                               │   │
│  │    │    │                                                             │   │
│  │    │    │  ERROR CLASSIFIER [ANY]:                                    │   │
│  │    │    │  ├── null_none   → [investigate, debug, error-handling]     │   │
│  │    │    │  ├── type_error  → [investigate, debug, type-checking]      │   │
│  │    │    │  ├── import_err  → [research, search-first, setup]          │   │
│  │    │    │  ├── permission  → [security-review, permissions]           │   │
│  │    │    │  ├── timeout     → [performance, retry]                     │   │
│  │    │    │  ├── not_found   → [research, search-first]                 │   │
│  │    │    │  ├── syntax      → [debug, lint]                            │   │
│  │    │    │  ├── memory      → [optimization, performance]              │   │
│  │    │    │  └── network     → [deployment, monitoring]                 │   │
│  │    │    │                                                             │   │
│  │    │    │  ALTERNATIVE FINDER:                                        │   │
│  │    │    │    SELECT * FROM resources                                  │   │
│  │    │    │    WHERE category IN (recommended_categories)               │   │
│  │    │    │    AND name != failed_tool                                  │   │
│  │    │    │    ORDER BY times_correct DESC                              │   │
│  │    │    │                                                             │   │
│  │    │    │  log_failure(tool) → UPDATE times_wrong += 1                │   │
│  │    │    │  attempt += 1                                               │   │
│  │    │    │                                                             │   │
│  │    │  ┌───────────────────────────────────────────────────────────┐  │   │
│  │    │  │  LEARNED APPROACH [ANY]:                                    │  │   │
│  │    │  │  "Try tool(s) that handle 'null_none' errors:               │  │   │
│  │    │  │   [alternative_1, alternative_2, ...]"                      │  │   │
│  │    │  └───────────────────────────────────────────────────────────┘  │   │
│  │                                                                      │   │
│  │  RETURN {status: 'failed', attempts: N, tried_tools: [...]}        │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                               │
│  ┌───────────────────────────────────────────────────────────────────────┐   │
│  │  ROUTING LOG (persistent tracking):                                   │   │
│  │    INSERT INTO routing_log(task, skill_name, outcome='correct|wrong') │   │
│  │    UPDATE resources SET times_correct/wrong += 1 WHERE name = tool    │   │
│  │                                                                       │   │
│  │  DEGRADED DETECTION:                                                  │   │
│  │    IF times_triggered >= 5 AND error_rate > 40%:                     │   │
│  │      SET status = 'degraded'                                          │   │
│  └───────────────────────────────────────────────────────────────────────┘   │
└───────────────────────────────────────────────────────────────────────────────┘
                     │
                     ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│        INTENT PARSER  [HGP]  Section 3.2 (Stage 1)                            │
│                                                                               │
│  ┌───────────────────────────────────────────────────────────────────────┐   │
│  │  orchestrate() → HuggingGPT 4-Stage Flow:                              │   │
│  │                                                                       │   │
│  │  Stage 1 — Task Planning:                                             │   │
│  │    Parse request → structured subtasks with dependencies              │   │
│  │    [HGP] Section 3.2: "LLM outputs [task, dep, args] list"           │   │
│  │                                                                       │   │
│  │  Stage 2 — Tool Selection:                                            │   │
│  │    HierarchicalRouter.route() → best tool for each subtask            │   │
│  │    [HGP] "select models according to function descriptions"           │   │
│  │                                                                       │   │
│  │  Stage 3 — Task Execution:                                            │   │
│  │    ExecuteWithReflection → parallel/sequential per dependency         │   │
│  │    [HGP] "execute each subtask with selected AI model"                │   │
│  │                                                                       │   │
│  │  Stage 4 — Response Synthesis:                                        │   │
│  │    Aggregate results → coherent output                                │   │
│  │    [HGP] "summarize response according to execution results"          │   │
│  └───────────────────────────────────────────────────────────────────────┘   │
└───────────────────────────────────────────────────────────────────────────────┘
                     │
                     ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│        DYNAMIC MCP SYNC  [SMC]  Section 3.2                                   │
│                                                                               │
│  ┌───────────────────────────────────────────────────────────────────────┐   │
│  │  mcp_sync.MCPSyncManager                                              │   │
│  │                                                                       │   │
│  │  ┌─ CRUD Pipeline ─────────────────────────────────────────────────┐  │   │
│  │  │  CREATE → INSERT INTO resources(name, description, body_text)    │  │   │
│  │  │  READ   → SELECT * FROM resources WHERE type='mcp'               │  │   │
│  │  │  UPDATE → UPDATE resources SET body_text = new_schema            │  │   │
│  │  │  DELETE → UPDATE resources SET status = 'archived'               │  │   │
│  │  └──────────────────────────────────────────────────────────────────┘  │   │
│  │                                                                       │   │
│  │  ┌─ Background Sync Thread ────────────────────────────────────────┐  │   │
│  │  │  WHILE running:                                                  │  │   │
│  │  │    1. Process queued CRUD operations (debounced)                │  │   │
│  │  │    2. Deduplicate by tool name → keep latest op                 │  │   │
│  │  │    3. Sleep 60s (configurable poll_interval)                    │  │   │
│  │  └──────────────────────────────────────────────────────────────────┘  │   │
│  │                                                                       │   │
│  │  ┌─ Conflict Resolution ──────────────────────────────────────────┐  │   │
│  │  │  MCP Server = source of truth                                   │  │   │
│  │  │  If server.tool exists AND db.tool != server.tool → UPDATE     │  │   │
│  │  │  If server.tool missing AND db.tool exists → archive            │  │   │
│  │  └──────────────────────────────────────────────────────────────────┘  │   │
│  └───────────────────────────────────────────────────────────────────────┘   │
└───────────────────────────────────────────────────────────────────────────────┘
                     │
                     ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│        DATABASE  [sqlite-vec] — 1,096 Resources                               │
│                                                                               │
│  ┌───────────────────────────────────────────────────────────────────────┐   │
│  │  ┌──────────────┐  ┌──────────────────┐  ┌────────────────────────┐   │   │
│  │  │ resources    │  │ routing_log      │  │ plans                  │   │   │
│  │  │──────────────│  │──────────────────│  │────────────────────────│   │   │
│  │  │ 1,096 rows   │  │ 12+ rows         │  │ 2 rows                 │   │   │
│  │  │ embedding    │  │ task_summary     │  │ goal, phases_json      │   │   │
│  │  │ (BLOB 3072)  │  │ skill_name       │  │ status                 │   │   │
│  │  │ times_*       │  │ outcome          │  └────────────────────────┘   │   │
│  │  │ resource_type │  │ created_at       │                                 │   │
│  │  └──────────────┘  └──────────────────┘  ┌────────────────────────┐   │   │
│  │                                           │ execution_log          │   │   │
│  │  ┌──────────────┐  ┌──────────────────┐   │────────────────────────│   │   │
│  │  │ mcp_sync_log │  │ embeddings_cache │   │ plan_id, phase_id     │   │   │
│  │  │──────────────│  │──────────────────│   │ matched_model         │   │   │
│  │  │ operation    │  │ text_hash        │   │ outcome               │   │   │
│  │  │ tool_name    │  │ embedding (BLOB) │   └────────────────────────┘   │   │
│  │  │ timestamp    │  │ model_name       │                                 │   │
│  │  └──────────────┘  └──────────────────┘                                 │   │
│  └───────────────────────────────────────────────────────────────────────┘   │
└───────────────────────────────────────────────────────────────────────────────┘
                     │
                     ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│        MCP API SERVER  [nexus-mcp-enhanced.py]                                │
│                                                                               │
│  TOOLS EXPOSED:                                                               │
│  ┌───────────────────────────────────────────────────────────────────────┐   │
│  │  find_skills(task, project)        ← v1 legacy [ANY]                  │   │
│  │  get_skill(name)                   ← v1 legacy                         │   │
│  │  log_outcome(...)                  ← v1 legacy                         │   │
│  │  audit_routing()                   ← v1 legacy                         │   │
│  │  list_resources(type, status)      ← v1 legacy                         │   │
│  │                                                                       │   │
│  │  design_plan(task, project)        ← adaptive-imagining-cat           │   │
│  │  execute_phase(plan_id, phase)     ← adaptive-imagining-cat           │   │
│  │  verify_phase(plan_id, phase)      ← LDB verification                 │   │
│  │  audit_plan(plan_id)               ← execution audit                  │   │
│  │  nexus_health()                    ← system health check              │   │
│  │                                                                       │   │
│  │  ▼ v2 AUTONOMOUS TOOLS (NEW):                                        │   │
│  │  ┌───────────────────────────────────────────────────────────────┐   │   │
│  │  │  auto_route(task, project)                                     │   │   │
│  │  │  → Full pipeline: trigger→category→TDWA→T2A→toolken→reflect    │   │   │
│  │  │  ← Returns {route, trigger, execution, total_candidates}       │   │   │
│  │  │                                                               │   │   │
│  │  │  [TKG+ANY+T2A+SMC+HGP]                                       │   │   │
│  │  └───────────────────────────────────────────────────────────────┘   │   │
│  │  ┌───────────────────────────────────────────────────────────────┐   │   │
│  │  │  trigger_on_event(event, context, project)                     │   │   │
│  │  │  → Auto-detect trigger type (error 0.95, complex 0.85, …)    │   │   │
│  │  │  → execute_with_reflection()                                  │   │   │
│  │  │  ← Returns {status, attempts, tried_tools}                   │   │   │
│  │  │                                                               │   │   │
│  │  │  [ANY] Section 3.2 Self-Reflection Loop                      │   │   │
│  │  └───────────────────────────────────────────────────────────────┘   │   │
│  │  ┌───────────────────────────────────────────────────────────────┐   │   │
│  │  │  hierarchical_retrieve(query, categories, top_k)              │   │   │
│  │  │  → 3-layer: Category→TDWA→T2A routing only (no execution)    │   │   │
│  │  │  ← Returns {layer1, layer2, layer3_with_agents}              │   │   │
│  │  │                                                               │   │   │
│  │  │  [ANY+SMC+T2A]                                               │   │   │
│  │  └───────────────────────────────────────────────────────────────┘   │   │
│  │  ┌───────────────────────────────────────────────────────────────┐   │   │
│  │  │  sync_mcp_tools(server_name, tools_json)                      │   │   │
│  │  │  → CRUD pipeline (create/read/update/delete)                  │   │   │
│  │  │  → MCP server = source of truth                              │   │   │
│  │  │  ← Returns {server, results: {created, updated, deleted}}    │   │   │
│  │  │                                                               │   │   │
│  │  │  [SMC] Section 3.2 CRUD Pipeline                             │   │   │
│  │  └───────────────────────────────────────────────────────────────┘   │   │
│  └───────────────────────────────────────────────────────────────────────┘   │
└───────────────────────────────────────────────────────────────────────────────┘

## Paper → Component Mapping

```
RESEARCH PAPER           SECTIONS     COMPONENTS IMPLEMENTED
─────────────────────────────────────────────────────────────────────────────
ToolkenGPT               Section 3    auto_invoke.AutoInvoke
(2305.11554)                            • detect_trigger() — trigger detection
                                        • _toolken_select() — tool probability
                                        • P(tool_k) = softmax(h · e_k / τ)

AnyTool                  Section 3.1   hierarchy_router.HierarchicalRouter
(2402.04253)             Section 3.2     • route_category() — Layer 1
                                         • _get_category_tools() — Layer 2
                                         • _find_alternatives() — fallback
                                        auto_invoke.AutoInvoke
                                         • execute_with_reflection() — retry loop
                                        self_reflector.SelfReflector
                                         • _classify_error() — error patterns
                                         • _generate_diagnosis() — diagnosis
                                         • _generate_learned_approach() — learning

Tool-to-Agent Retrieval  Section 3     hierarchy_router.HierarchicalRouter
(2511.01854)                             • join_with_agents() — Layer 3
                                         • _find_parent_agent() — metadata traversal
                                         • combined_score = α*tool + (1-α)*agent

HuggingGPT               Section 3.2   nexus-mcp-enhanced.py
(2303.17580)                            • design_plan() — Task Planning (Stage 1)
                                        • auto_route() — Tool Selection (Stage 2)
                                        • execute_phase() — Task Execution (Stage 3)
                                        • audit_plan() — Response Synthesis (Stage 4)

ScaleMCP                 Section 3.1   hierarchy_router.HierarchicalRouter
(2505.06416)             Section 3.2     • _compute_tdwa() — TDWA embedding
                                         • search_tools_tdwa() — weighted retr.
                                        mcp_sync.MCPSyncManager
                                         • create_tool() — CREATE
                                         • read_tool() — READ
                                         • update_tool() — UPDATE
                                         • delete_tool() — DELETE
                                         • sync_from_mcp_server() — full sync
                                         • _process_pending_syncs() — debouncing

adaptive-imagining-cat   Phase 1-6     orchestrator.Orchestrator
(local skill)                           • phase_1_state_analysis()
                                        • phase_2_plan_creation()
                                        • phase_3_execute()
                                        • phase_4_verification()
                                        • phase_5_audit()
                                        nexus-mcp-enhanced.py
                                        • design_plan(), execute_phase()
                                        • verify_phase(), audit_plan()
```

## Data Flow (Execution Path)

```
User Query / System Event
    ↓
[DETECT_TRIGGER]           → trigger type + confidence  [ANY]
    ↓
[ROUTE_CATEGORY]           → top-3 categories (~5ms)     [ANY]
    ↓
[SEARCH_TOOLS_TDWA]        → top-15 tools (~15ms)       [SMC]
    ↓
[JOIN_WITH_AGENTS]         → reranked top-5              [T2A]
    ↓
[TOOLKEN_SELECT]           → INVOKE|REVIEW|SKIP          [TKG]
    ↓
[EXECUTE_WITH_REFLECTION]  → retry 3x on failure         [ANY]
    ↓                                      |
    ├── Success → log_correct + return     |
    └── Failure → classify_error → alternatives → retry
    ↓
[ROUTING_LOG]              → persistent tracking         [ANY]
    ↓
[SYNC_MCP]                 → background CRUD sync        [SMC]
```