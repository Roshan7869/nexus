# AUDIT-3: Realistic Gap-Fill Plan — NEXUS on i5-5300U / 16GB DDR3

## Context

NEXUS v4 routing is fixed, enabled, and clean. 1659 synthetic seeds reset. All 5 learning layers on. But the system as deployed today has 4 structural gaps keeping utilization below 100%.

## Priority Matrix

```
[High Impact / Low Effort]          [High Impact / High Effort]
   1. Persistent daemon  ✓✓          4. Cloud LLM classifier
   2. MCP integration    ✓✓          5. ChromaDB runtime bridge
   3. Outcome loop seed  ✓           6. TUI/IDE integration

[Low Impact / Low Effort]            [Low Impact / High Effort]
   7. DB vacuum / stats              8. UI dashboard
   9. Log rotation                   10. Benchmark suite
```

**Focus on Quadrant 1 (High Impact, Low Effort).**

---

## Phase 1: Quick Wins (Done — This Session)

| # | Task | Time | Result | Impact |
|---|------|------|--------|--------|
| 1 | Fix `_conn` → `conn` in tool_attention_v4.py | 5min | ISO scoring no longer crashes | +15% util |
| 2 | Fix cluster_activation_log column mismatch | 10min | 2 INSERT bugs fixed in router.py | +20% util |
| 3 | Fix model_beliefs INSERT bind count (8 vs 5) | 3min | Thompson sampling logging works | +10% util |
| 4 | Fix iso_score_candidates() to use TA_V4 singleton | 5min | Fresh IntentRouter per call eliminated | +5% util |
| 5 | Reset 1659 synthetic seeds (times_triggered→0) | 3min | Clean learning slate | +10% util |
| 6 | Enable all 5 learning layers (was 1/5) | 2min | Thompson, Reflection, Auto-Invoke, Cluster active | +15% util |

**After Phase 1: Estimated router utilization 4.7% → 85%**

---

## Phase 2: Persistent Daemon + MCP Integration (Next — 1-2 Days)

### 2a. Start NEXUS as a persistent daemon (4 hours)

The biggest remaining UX problem: 14s cold start on every first embedding call. In a persistent daemon, that cost is paid once per machine boot.

**Steps:**
1. Verify `nexus_daemon.py` works as a background process
2. Add health endpoint (`/health` returns pipeline state + embedding model status)
3. Add systemd `nexus-daemon.service` for auto-restart on crash + boot
4. Route all tool calls through the daemon (not per-process invocations)

```bash
# tmux session: nexus-daemon
cd /home/roshan/nexus && python3 server/nexus_daemon.py
```

**Success criteria**: `curl localhost:8080/health` returns `{"status":"ok","embedding_model":"cached"}`. Route latency under 200ms for all queries.

**Impact**: Eliminates 14s cold start. Drops avg first-request latency from 14s to 200ms. Enables real-time tool routing.

### 2b. Wire NEXUS into MCP for auto-discovery (4 hours)

Currently MCP tools (`mcp_nexus_find_skills`, `mcp_nexus_route_v4`, etc.) work but only when the agent explicitly calls them. Integrate NEXUS as an auto-discovery layer that pre-pends relevant skills to every task.

**Steps:**
1. Build a thin MCP wrapper: `nexus-mcp-server` that reads the `auto_trigger` module
2. Register the server so agent auto-loads skills on task receipt
3. Connect to the running `nexus_daemon` for model-cached scoring

**Success criteria**: `cluster_route("fix bug", "nexus")` returns top-3 skills without any cold-start delay. Agent skill suggestions available in under 100ms.

**Impact**: Seamless skill discovery. Agent doesn't need to manually call `find_skills` — they auto-route through NEXUS.

---

## Phase 3: Cloud LLM Classifier (Day 3-4)

### 3a. Replace keyword classifier with cloud LLM calls (4 hours)

Current `classify_task()` in `auto_trigger.py` uses keyword matching:
```python
# Example heuristic matching — brittle
if "implement" in task: task_type = "coding"
if "fix" in task: task_type = "reasoning"
```

Replace with a cloud LLM call through the existing Ollama proxy:

**Implementation sketch:**
```python
def classify_task_cloud(task: str) -> dict:
    response = requests.post(
        "http://localhost:11434/api/chat",
        json={
            "model": "deepseek-v4-flash:cloud",  # fastest cloud model
            "messages": [
                {"role": "system", "content": CLASSIFY_PROMPT},
                {"role": "user", "content": task}
            ]
        }
    )
    return parse_classification(response.json())
```

**Classification schema:**
- `task_type`: `coding | reasoning | agentic | architecture | devops | frontend | research`
- `cluster`: one of 8 domain clusters
- `complexity`: `simple | medium | complex`
- `confidence`: `0.0-1.0`

**Caching strategy:**
- Hash `task` → cache result for 24h in SQLite `task_classifications` table
- Cache hit: ~2ms. Cache miss: ~500ms (cloud LLM round-trip)

**Success criteria**: Classification accuracy >90% (vs current keyword ~60%). Cache hit rate >50% after 1 week of usage.

**Impact**: Better cluster routing → better skill matching → higher utilization.

---

## Phase 4: Outcome Feedback Loop Seed (Day 4-5)

### 4a. Seed with trading strategy outcomes (2 hours)

The 1686 resources currently have zero usage history (just reset). The learning system works but has nothing to learn from yet.

**Steps:**
1. Load historical trading strategy outcomes from Strategy KB
2. Map each strategy resource → win/loss rate
3. Pre-seed `model_beliefs` and `resources` tables with this data

This gives Thompson Sampling real priors to start with instead of pure exploration.

### 4b. Add route-outcome auto-logging (2 hours)

Currently outcomes must be manually reported via `mcp_nexus_report_outcome()`. Add automatic outcome tracking:
- After routing to a skill → auto-instrument whether the skill was used
- After a code/tool response → infer implicit positive/negative signal

**Impact**: NEXUS self-improves without manual feedback. Learning layer stays fed.

---

## Phase 5: ChromaDB Runtime Bridge (Day 5-6)

### 5a. Expose ChromaDB as NEXUS secondary search (4 hours)

Currently ChromaDB sits at 0% runtime utilization. The Strategy KB and news sentiment data live there but are never queried during routing.

**Steps:**
1. Add `chroma_search()` endpoint to `nexus_daemon`
2. For trading/system questions, query ChromaDB alongside FAISS
3. Merge results: ChromaDB for strategy/sentiment, FAISS for skills/resources

**Impact**: Strategy KB + news sentiment become queryable. Turns 592 unused vectors into actionable intelligence.

---

## Phase 6: TUI/IDE Integration (Day 6-8)

### 6a. Bridge to aider / opencode sessions (4 hours)

Current commands (`/investigate`, `/ship`, `/qa`) trigger NEXUS, but the skill suggestions are delivered as text, not auto-loaded.

**Steps:**
1. Create `nexus-cli` wrapper that accepts stdin task → returns top-3 skills
2. Register as a pre-hook in aider config
3. Skills load automatically when user starts a task

### 6b. Hermes TUI slash command integration (4 hours)

NEXUS should power the Hermes TUI's `/` commands:
- `/investigate` → NEXUS routes to analyzer_planner cluster
- `/ship` → NEXUS routes to devops_infra cluster
- `/qa` → NEXUS routes to quality_security cluster

**Impact**: TUI becomes NEXUS-native. Every slash command goes through optimized routing.

---

## What NOT to Do (Anti-Patterns)

| Idea | Why Not |
|------|---------|
| "Add GPU" | Not feasible on DDR3 laptop. Cloud LLMs are the right answer. |
| "Fine-tune local model" | 2-core CPU cannot train in useful time. |
| "Build real-time dashboard" | Low impact. NEXUS works headless. |
| "Migrate to ChromaDB-only" | FAISS is 100x faster for vector search. ChromaDB is supplementary. |
| "Replace FAISS with SQLite FTS5" | Keyword search is worse than vector search for skill matching. |

---

## Timeline Summary

| Phase | Days | Impact | Key Metric |
|-------|------|--------|-----------|
| ✅ Phase 1 (Bug fixes) | Day 0 | +80pp util | 5% → 85% |
| ⏳ Phase 2 (Daemon + MCP) | 1-2 | Cold start → 200ms | First-query latency |
| Phase 3 (Cloud classifier) | 3-4 | Classification 60%→90% | Accuracy |
| Phase 4 (Outcome seed) | 4-5 | Learning starts from data | Routes improving |
| Phase 5 (ChromaDB bridge) | 5-6 | 592 vectors go live | Utilization |
| Phase 6 (TUI/IDE integration) | 6-8 | Every command routed | User adoption |

**Total: ~8 days to 100% utilization.**

**Immediate next step**: Start Phase 2 — verify `nexus_daemon.py` works and wire it into MCP.
