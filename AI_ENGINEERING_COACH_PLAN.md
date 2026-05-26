# AI Engineering Coach — NEXUS Integration Plan

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                        NEXUS (Python)                           │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────────┐   │
│  │ MCP Tools   │  │ Route v4     │  │ Learning Pipeline    │   │
│  │ coach_dash  │──│ ISO+FAISS    │──│ Thompson Sampling    │   │
│  │ coach_anti  │  │ → Coach Skill│  │ Reflection           │   │
│  │ coach_chat  │  │ → Anti-Pat   │  │ Cluster Affinity     │   │
│  └──────┬──────┘  └──────┬───────┘  └──────────┬───────────┘   │
│         │                │                      │              │
│         ▼                ▼                      ▼              │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │             Coach Bridge (Python)                        │  │
│  │  - Reads Coach cache JSON → NEXUS tables                │  │
│  │  - Routes coaching queries to Copilot LLM               │  │
│  │  - Bridges anti-patterns → reflection_failures          │  │
│  │  - Bridges practice scores → model_beliefs              │  │
│  └──────────────────────┬───────────────────────────────────┘  │
│                         │                                       │
└─────────────────────────┼───────────────────────────────────────┘
                          │ (disk — reads cache JSON files)
                          ▼
┌──────────────────────────────────────────────────────────────────┐
│              AI-Engineering-Coach (TypeScript/Node)              │
│  ┌────────────┐  ┌──────────────┐  ┌──────────────────────┐    │
│  │ Parsers    │  │ Analyzers    │  │ Cache (JSON files)   │    │
│  │ (6× parse) │→ │ (11×analyze) │→ │ /tmp/coach-cache/    │    │
│  └────────────┘  └──────────────┘  │ sessions.json        │    │
│                                    │ antipatterns.json    │    │
│  ┌────────────┐  ┌──────────────┐  │ scores.json          │    │
│  │ 45 Rules   │  │ LLM Features │  │ dashboard.json       │    │
│  │ (markdown) │  │ (Copilot API)│  └──────────────────────┘    │
│  └────────────┘  └──────────────┘                               │
└──────────────────────────────────────────────────────────────────┘
```

## Phases

### Phase 1 — Coach Data Schema Bridge
**Goal**: Mirror Coach's analytics types as NEXUS SQLite tables.

**Deliverables**:
- `server/coach/schema.sql` — Tables:
  - `coach_practice_scores(project, date, prompt_quality, session_hygiene, code_review, tool_mastery, context_management, total_score)`
  - `coach_antipatterns(id, project, date, rule_id, rule_name, group_name, severity, count, trend)`
  - `coach_sessions(id, harness, workspace, session_id, date, request_count, model_usage_json)`
  - `coach_skill_finder(project, pattern, matched_skill, confidence)`
- `server/coach/bridge.py` — Reads Coach cache JSON, upserts to NEXUS tables
- `server/coach/__init__.py` — Module init

**Files to create**: 4
**Risk**: Low — standard DB schema + import

---

### Phase 2 — NEXUS Coaching Skills
**Goal**: Register coaching capabilities as NEXUS resources so routing works.

**Deliverables**:
- 6 new NEXUS resources (skills):
  - `coach-dashboard` — "Analyze AI coding practice scores and trends"
  - `coach-antipatterns` — "Detect and explain AI coding anti-patterns"
  - `coach-conversational` — "Natural-language AI coaching assistant"
  - `coach-skill-finder` — "Find relevant skills based on prompt patterns"
  - `coach-learning-path` — "Generate personalized learning exercises"
  - `coach-session-analysis` — "Deep-dive analysis of AI coding sessions"
- SQL INSERTs into `resources` table with:
  - `resource_type='skill'`, `cluster='coaching'`, `status='active'`
  - Embedding summaries for FAISS search
- Register `coaching` cluster in `cluster_definitions`

**Files to modify**: Run SQL via `sqlite3`
**Risk**: Low — standard resource registration

---

### Phase 3 — Coaching MCP Tools
**Goal**: Expose Coach capabilities as NEXUS MCP tools.

**Deliverables**:
Add to `nexus-mcp-enhanced.py`:

1. `coach_query(query, project, include_scores, include_antipatterns, include_sessions)`:
   - Reads Coach cache JSON → returns aggregated dashboard
   - Returns: practice scores (5 categories + total), anti-patterns, recent sessions

2. `coach_antipatterns(rule_id, group, severity, since_date)`:
   - Filters and returns anti-pattern detections
   - Returns: count, trend, severity, suggestion text

3. `coach_converse(query, context)`:
   - Conversational coaching via Copilot LLM
   - Passes user query + coaching context → gets natural-language response

4. `coach_skill_finder(pattern)`:
   - Matches prompt patterns against NEXUS skill catalog
   - Returns top-5 skill matches with confidence

5. `coach_analyze_sessions(harness, since_date, limit)`:
   - Deep session analysis: request patterns, model usage, tool usage
   - Returns: session timeline, top models, common tools

**Files to modify**: `server/nexus-mcp-enhanced.py`
**Files to create**: `server/coach/mcp_handlers.py` — handler implementations
**Risk**: Medium — need to test MCP tool registration

---

### Phase 4 — Anti-Pattern → Reflection Bridge
**Goal**: Feed Coach anti-pattern detections into NEXUS reflection pipeline.

**Deliverables**:
- `server/coach/reflection_bridge.py`:
  - `sync_antipatterns_to_reflections()`:
    1. Query coach_antipatterns for high-severity items
    2. For each, INSERT into `reflection_failures`:
       - `task` = rule description
       - `resource_name` = matched skill (or "coach:rule_id")
       - `root_cause` = anti-pattern group + severity
    3. This triggers NEXUS self-reflection → finds alternative resources
    4. Logs to `auto_invoke` routing_log for retry tracking
  - `schedule_bridge(interval_minutes=60)` — Background sync

- Wire into `broker_runtime.py:execute_task_run()`:
  - After execution, call `sync_antipatterns_to_reflections()` for completed tasks

**Files to create**: `server/coach/reflection_bridge.py`
**Files to modify**: `server/broker_runtime.py` (add post-execution hook)
**Risk**: Medium — reflection pipeline already works (verified), just needs data

---

### Phase 5 — Practice Score → Thompson Beliefs
**Goal**: Coach practice scores influence NEXUS model/resource selection.

**Deliverables**:
- `server/coach/thompson_bridge.py`:
  - `sync_scores_to_beliefs()`:
    1. Query latest coach practice scores per project
    2. Map each score category to a task_type:
       - `prompt_quality` → task_type="coding"
       - `session_hygiene` → task_type="general"
       - `code_review` → task_type="reasoning"
       - `tool_mastery` → task_type="devops"
       - `context_management` → task_type="frontend"
    3. For each mapped coach:X → model_beliefs entry:
       - `UPDATE model_beliefs SET alpha = alpha + score_delta WHERE provider='coach'`
    4. This makes NEXUS routing prefer models that work well for
       practice areas the user is improving in

- Wire into `server/learning.py:_update_thompson_belief()`:
  - After normal belief update, check if coach scores changed
  - Apply score_delta as additional reward signal

**Files to create**: `server/coach/thompson_bridge.py`
**Files to modify**: `server/learning.py` (add coach reward signal)
**Risk**: High — Thompson Beliefs are Beta-distributed; score injection needs careful normalization

---

### Phase 6 — Conversational Coaching Interface
**Goal**: Natural-language coaching via Copilot LLM.

**Deliverables**:
- `server/coach/conversation.py`:
  - `coach_chat(query, context, history)`:
    1. Accept natural-language query (e.g., "why did my scores drop?")
    2. Gather context: recent scores, anti-pattern trends, session data
    3. Build system prompt with coaching persona + current analytics
    4. Call Copilot LLM via `panel-llm.js` or
       fallback to NEXUS provider (Ollama/NVIDIA NIM)
    5. Return structured response: answer + recommended actions + relevant skills

- Coaching persona system prompt template:
  ```
  You are an AI Engineering Coach. You analyze the user's AI coding
  assistant usage data and provide actionable coaching advice.
  
  Current practice scores:
  - Prompt Quality: {prompt_quality}/100
  - Session Hygiene: {session_hygiene}/100
  - Code Review: {code_review}/100
  - Tool Mastery: {tool_mastery}/100
  - Context Management: {context_management}/100
  
  Recent anti-patterns:
  {antipatterns_text}
  
  User question: {query}
  
  Provide:
  1. Direct answer to their question
  2. Specific actions they can take (max 3)
  3. Which NEXUS skills to use for improvement
  ```

- Wire into `nexus-mcp-enhanced.py` as `coach_converse()` tool

**Files to create**: `server/coach/conversation.py`
**Files to modify**: `server/nexus-mcp-enhanced.py`
**Risk**: Medium — LLM integration exists (Copilot API), just needs prompt engineering

---

## Execution Order & Dependencies

```
Phase 1 (Schema) ──→ Phase 2 (Skills) ──→ Phase 3 (MCP Tools)
                      │                      │
                      ▼                      ▼
                   Phase 4 ──────────→ Phase 5
                   (Reflection)       (Thompson)
                      │
                      ▼
                   Phase 6
                   (Conversation)
```

**Dependencies**:
- Phase 2 depends on Phase 1 (skills need schema)
- Phase 3 depends on Phase 1+2 (MCP tools query schema)
- Phase 4 depends on Phase 1 (needs antipattern data)
- Phase 5 depends on Phase 1 (needs score data)
- Phase 6 depends on Phase 3 (uses MCP tools) + Phase 4+5

## File Map

```
server/
├── coach/
│   ├── __init__.py            # Module init
│   ├── schema.sql             # SQLite tables
│   ├── bridge.py              # Coach cache → NEXUS sync
│   ├── mcp_handlers.py        # MCP tool implementations
│   ├── reflection_bridge.py   # Anti-pattern → reflection
│   ├── thompson_bridge.py     # Scores → beliefs
│   └── conversation.py        # Conversational coaching
├── nexus-mcp-enhanced.py      # +5 MCP tools (coach_*)
├── broker_runtime.py          # +post-execution hook
├── learning.py                # +coach reward signal
└── router.py                  # (no changes needed - auto-discovers skills)
```

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Coach cache JSON format changes | Medium | Medium | Use JSON Schema validation, fail gracefully |
| Copilot LLM API unavailable | Low | High (Phase 6) | Fallback to NEXUS Ollama/NVIDIA provider |
| Thompson belief corruption | Low | High (Phase 5) | Normalize score_delta to [0, 0.3] range, backup beliefs before update |
| Reflection bridge duplicates | Medium | Low | Use UPSERT with ON CONFLICT on (task, resource_name) |
| Coach not installed locally | High | Medium | All bridge code gracefully skips if no Coach cache found |

## Success Criteria

1. ✅ `coach_query()` returns practice scores + anti-patterns via NEXUS MCP
2. ✅ `coach_converse()` answers natural-language coaching questions
3. ✅ Anti-patterns appear in NEXUS `reflection_failures` table
4. ✅ Practice scores appear in NEXUS `model_beliefs` table
5. ✅ NEXUS route_v4 can route "coach me on prompt quality" to coach skill
6. ✅ All 40 existing tests + new coach tests pass
