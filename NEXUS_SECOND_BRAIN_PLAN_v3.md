# NEXUS Second Brain — Multi-Level Hierarchical Execution Plan v3
## Synthesizing arXiv (15 papers) + SSRN/Journals (17 papers) + GitHub (20 repos)

> Generated: 2026-06-26
> Protocol: NEXUS Planning Protocol (Phase 0 → Phase 1 [DONE] → Phase 2 [THIS] → Approval → Execute)
> Model: minimax-m3:cloud
> Research: 52 papers/repos analyzed across 3 parallel delegates

---

## PHASE 0: FIRST PRINCIPLES DECONSTRUCTION

### 0.1 — What Is NEXUS, Stripped to Fundamentals?

NEXUS is a **resource intelligence layer** that sits between a user/task and 2,815 tools (skills, commands, agents, MCP tools). Its job is to route the right resource to the right task, learn from outcomes, and improve over time.

The "second brain" vision extends this: NEXUS should also **manage multiple TUI IDEs simultaneously**, **autonomously call tools** without manual find_skills(), **track harness efficiency**, and **build a knowledge graph** of tool relationships.

### 0.2 — Atomized Components

| Atom | What It Does | Current State | Target State |
|------|-------------|---------------|--------------|
| A1: Router | Route task → resource | v4 cluster + ISO, 0.976 accuracy on feedback'd subset | Unified router with RRF fusion, cold-start, EFC |
| A2: Feedback Loop | Record outcomes, learn | 34.8% coverage, binary, dual-path | >80% coverage, quality-weighted [0,1], single path |
| A3: Cold Start | Activate 1877 never-triggered | UCB1 + two-tier built, flag OFF | Flag ON, <50% never-triggered in 7 days |
| A4: Process Supervisor | Manage MCP/daemon lifecycle | Ad-hoc subprocess, zombie spawns | supervisord, XML-RPC control, auto-reap |
| A5: TUI Bridge | Orchestrate multiple IDE agents | None | tmux-based multi-IDE, abtop-style monitoring |
| A6: Knowledge Graph | Tool relationship graph | 9126 edges, 2117 nodes isolated | GraphSAGE edge prediction, Graphify integration |
| A7: Harness Engine | Track efficiency metrics | None | FTSR/MRC/TLT/CDR dashboard, per-agent tracking |
| A8: Autonomous Pipeline | Auto-trigger on events | auto_invoke.py exists, threshold 0.68 | Event-driven, no manual find_skills(), reflection loop |
| A9: EFC Integration | Quality-weighted feedback compute | efc.py written, columns NULL | Columns populated, blended into combined_score |
| A10: Plan Executor | Typed DAG plan → phases → verify | nexus_plan_executor.py exists | LangGraph state machine, checkpointing |

### 0.3 — Anti-Hallucination Guardrails

- Every implementation claim backed by a paper or repo citation
- Every code change verified by smoke test before marking complete
- No architecture decision without measured numbers (per user preference)
- EFC formula from arXiv 2605.29682: `EFC_t = κ·I·V·R·M` (κ=10)
- RRF k=60 from SIGIR/WWW 2023-2024 multi-objective ranking paper
- KNN cold-start from ICML/KDD meta-learning paper (k=5, convergence 30→10)
- supervisord from Supervisor/supervisor (9,072★, battle-tested)
- Graphify already installed at /home/roshan/graphify

### 0.4 — OOM/OOD/OOC Guardrails

- **OOM**: Don't load all 2815 embeddings into memory — use FAISS incremental, lazy load clusters
- **OOD**: Plan for new resource types (new MCP servers, new agents) without schema migration
- **OOC**: Don't break existing MCP tools while adding unified router — feature flag, A/B test

---

## PHASE 1: RESEARCH (COMPLETE)

### 1.1 — arXiv (15 papers, 687 lines)
File: `/home/roshan/nexus_arxiv_research_2026.md`
Key papers: ToolkenGPT (NeurIPS 2024), AnyTool hierarchical, ScaleMCP TDWA, EFC arXiv 2605.29682, Reflexion, HuggingGPT 4-stage, Toolformer, ToolLLM.

### 1.2 — SSRN/Journals (17 papers, 243 lines)
File: `/home/roshan/nexus_academic_research.md`
Key papers: RL Tool Survey (TS optimal for single-step), Cold-Start Survey (content+active+transfer), RRF Multi-Objective (k=60), GraphSAGE inductive, IRT Task Difficulty, Quality-Weighted Beta, FTSR/MRC/TLT/CDR.

### 1.3 — GitHub OSS (20 repos)
Key repos: Supervisor (9K★, process control), Agent-of-Empires (2.7K★, multi-TUI), abtop (3.1K★, agent htop), Graphify (local, KG builder), LangGraph (36K★, state machine), CrewAI (54K★, orchestration), Claude-Obsidian (7.9K★, second brain), rmux (2.2K★, TUI SDK), JCode (7.8K★, harness ref).

---

## PHASE 2: TYPED GRAPHIFY PLAN (DAG)

### Plan Overview

```
                    ┌─────────────────────────────────────────────────────────┐
                    │                  NEXUS SECOND BRAIN v3                    │
                    │              Multi-Level Hierarchical Plan               │
                    └─────────────────────────────────────────────────────────┘
                                         │
               ┌─────────────────────────┼─────────────────────────┐
               │                         │                         │
        ┌──────▼──────┐          ┌───────▼───────┐         ┌──────▼──────┐
        │  TRACK A:   │          │  TRACK B:      │         │  TRACK C:   │
        │  Core RL &  │          │  Infra &      │         │  Second     │
        │  Routing    │          │  Process      │         │  Brain &    │
        │             │          │  Supervision  │         │  TUI Bridge │
        └──────┬──────┘          └───────┬───────┘         └──────┬──────┘
               │                         │                         │
    ┌──────────┼──────────┐    ┌─────────┼─────────┐    ┌──────────┼──────────┐
    │          │          │    │         │         │    │          │          │
  A1-A2     A3-A4     A9  │  B1-B2    B3-B4     B5  │  C1-C2    C3-C4     C5-C6
  Routing   Cold/EFC  EFC │  Superv   Config   Daemon│  TUI      Monitor   KG+
  Fusion   Start    Blend │  isord    Audit    Health│  Bridge   abtop     Graphify
    │        │        │   │    │        │        │   │    │        │        │
    └────────┴────────┴───┤    └────────┴────────┴───┤    └────────┴────────┴───┤
         ▼                │         ▼               │         ▼               │
    A5: Verify            │    B6: Deploy           │    C7: Autonomy         │
    A6: Audit             │    B7: Verify           │    C8: Full Audit       │
                         │                         │                         │
                         └─────────┬───────────────┘
                                   ▼
                          D1: Integration Test
                          D2: Production Cutover
                          D3: 7-Day Verification
```

### Track A: Core RL & Routing (6 tasks, ~6 hrs)

---

#### A1: Reciprocal Rank Fusion — Unify v3/v4 Routing
**Research basis**: Paper 3.1 (SIGIR/WWW 2023-2024), RRF k=60
**NEXUS issue**: v3 vs v4 disagree 5/6 on top-1
**Owner**: Core router
**Risk**: Medium (changes live routing path)
**Inputs**: `tool_attention.py` (v3), `tool_attention_v4.py` (v4), `cluster_router.py`
**Outputs**: `unified_router.py` with RRF fusion
**Verification**: Run 10 test queries, compare top-3 agreement rate (target: >80% same top-3)
**Rollback**: Feature flag `USE_UNIFIED_ROUTER=0` (already exists)

**Subtasks**:
1. (20 min) Write `rrf_fusion.py` — `def rrf_fuse(rankings: List[List[str]], k=60) -> List[Tuple[str, float]]`
2. (15 min) Wire into `unified.py` — call v3 and v4, fuse with RRF, return unified ranking
3. (10 min) Add `rrf_agreement_rate` metric to health check
4. (15 min) Smoke test: 10 queries, measure agreement before/after

**Pass/Fail**: agreement_rate > 0.80 AND no query returns empty results

---

#### A2: Quality-Weighted Thompson Sampling
**Research basis**: Paper 7.1 (NeurIPS/ICLR 2024), Paper 1.1 (RL Survey)
**NEXUS issue**: Binary outcomes lose information; `times_correct` can be fractional (0.5 for partial)
**Owner**: learning.py
**Risk**: Low (additive change, backward compatible)
**Inputs**: `learning.py:record_outcome()`, `outcome_log` table
**Outputs**: `quality_score REAL` column, quality-weighted Beta updates
**Verification**: Insert test outcome with quality=0.7, verify Beta params update by 0.7/0.3 not 1/0
**Rollback**: Default quality_score=1.0 preserves old behavior

**Subtasks**:
1. (10 min) `ALTER TABLE outcome_log ADD COLUMN quality_score REAL DEFAULT 1.0;`
2. (20 min) Modify `record_outcome()` — replace `if outcome == "correct": alpha += 1` with `alpha += quality_score`
3. (10 min) Add `quality_score` parameter to `report_outcome` MCP tool
4. (10 min) Map implicit signals: re-route same task = 0.3, first-try accept = 1.0, modification = 0.6
5. (10 min) Smoke test: insert quality=0.7 outcome, verify `times_correct` increments by 0.7

**Pass/Fail**: `times_correct` increments by quality_score value, not 1.0

---

#### A3: KNN Cold-Start Priors
**Research basis**: Paper 2.2 (ICML/KDD meta-learning), convergence 30→10 samples
**NEXUS issue**: 66.6% never-triggered (1877 resources)
**Owner**: rl/thompson.py + cold_warm.py
**Risk**: Low (only affects never-triggered resources)
**Inputs**: FAISS index, resources table (times_correct/wrong)
**Outputs**: `init_cold_start_priors(resource_name)` → Beta(alpha, beta) from k=5 neighbors
**Verification**: For 10 never-triggered resources, compute priors, verify they're non-uniform
**Rollback**: Uniform Beta(1,1) is current behavior — just don't call init_cold_start_priors

**Subtasks**:
1. (15 min) Write `cold_start_priors.py` — find k=5 nearest triggered neighbors via FAISS
2. (15 min) Compute: `Beta(1 + Σ(w_i · success_i), 1 + Σ(w_i · fail_i))` where `w_i = cosine_sim`
3. (10 min) Integrate into `unified_route()` — when resource has `times_triggered=0`, inject prior
4. (10 min) Log prior injection: `INSERT INTO cold_start_log (resource_name, prior_alpha, prior_beta, neighbors)`
5. (10 min) Smoke test: pick 5 never-triggered, verify priors differ from Beta(1,1)

**Pass/Fail**: ≥80% of tested resources get non-uniform priors (alpha≠1 OR beta≠1)

---

#### A4: Active Exploration Scheduler (epsilon-decreasing)
**Research basis**: Paper 2.1 (Cold-Start Survey), epsilon-decreasing schedule
**NEXUS issue**: Cold tier exists (UCB1 + two-tier 70/30) but flag OFF
**Owner**: unified.py / cold_warm.py
**Risk**: Medium (affects live routing — but behind feature flag)
**Inputs**: `cold_warm.py` (already built), `USE_UNIFIED_ROUTER` flag
**Outputs**: Epsilon schedule: `ε(t) = max(0.01, 0.30 * (1 - t/10000))`
**Verification**: Run 100 synthetic queries, verify ~30% include cold resources at t=0, ~1% at t=10000
**Rollback**: `USE_UNIFIED_ROUTER=0`

**Subtasks**:
1. (15 min) Add epsilon schedule to `cold_warm.py` — decreasing from 0.30 to 0.01
2. (10 min) Wire into `unified_route()`: with prob ε, replace one hot slot with a cold resource
3. (10 min) Track exploration: `INSERT INTO exploration_log (query, cold_resource, epsilon_t)`
4. (15 min) Smoke test: 100 synthetic queries, verify cold resource inclusion rate

**Pass/Fail**: Cold inclusion rate ≈ ε(t) ± 5% at each time step

---

#### A5: EFC Column Population Fix + Blending
**Research basis**: arXiv 2605.29682, `EFC_t = κ·I·V·R·M`
**NEXUS issue**: EFC columns all NULL despite computed values in notes
**Owner**: efc.py + nexus_http_daemon.py + learning.py
**Risk**: Medium (touches live DB writes)
**Inputs**: `efc.py` (189 lines, computes EFC correctly), `outcome_log` (7 EFC columns NULL)
**Outputs**: EFC columns populated, `combined_score = 0.5·TS + 0.2·keyword + 0.3·efc_norm`
**Verification**: Insert outcome, verify all 7 EFC columns are non-NULL
**Rollback**: combined_score stays 0.7·TS + 0.3·keyword (current)

**Subtasks**:
1. (20 min) Debug the UPDATE failure in `nexus_http_daemon.py` — likely column name mismatch or type error
2. (15 min) Fix: ensure `compute_efc()` return dict keys match column names exactly
3. (10 min) Add `efc_normalized = efc / max_efc` (normalize to [0,1])
4. (15 min) Update `combined_score` in `learning.py:~460`: `0.5·TS + 0.2·keyword + 0.3·efc_norm`
5. (10 min) Smoke test: insert outcome, SELECT all 7 EFC columns, verify non-NULL

**Pass/Fail**: All 7 EFC columns non-NULL after single outcome insert

---

#### A6: Routing Audit & Verification
**Research basis**: Paper 8.1 (FTSR/MRC/TLT/CDR)
**NEXUS issue**: No harness efficiency metrics
**Owner**: health check
**Risk**: None (read-only)
**Inputs**: routing_log (5023 rows), outcome_log (1708 rows)
**Outputs**: FTSR, MRC, TLT, CDR numbers
**Verification**: Numbers are sane (FTSR 0-100%, MRC ≥ 1.0)

**Subtasks**:
1. (15 min) Write SQL for FTSR: `SELECT COUNT(*) FILTER (WHERE outcome='correct' AND reroute_count=1) / COUNT(*) FROM routing_log`
2. (10 min) Write SQL for MRC: `SELECT AVG(reroute_count) FROM routing_log WHERE outcome IS NOT NULL`
3. (10 min) Write SQL for TLT: `SELECT AVG(routing_time_ms) / AVG(total_time_ms) * 100 FROM routing_log`
4. (10 min) Write SQL for CDR: `SELECT COUNT(DISTINCT resource_name) FILTER (WHERE last_used_at > datetime('now', '-7 days')) / COUNT(*) FROM resources`
5. (15 min) Add to `nexus_health_check` output

**Pass/Fail**: All 4 metrics produce sane numbers

---

### Track B: Infrastructure & Process Supervision (6 tasks, ~5 hrs)

---

#### B1: supervisord Configuration
**Research basis**: Supervisor/supervisor (9,072★), Circus (1,579★)
**NEXUS issue**: Duplicate MCP process spawning, zombie children, ad-hoc subprocess
**Owner**: Process management
**Risk**: High (kills and restarts MCP — must be done out-of-session)
**Inputs**: `nexus-mcp-enhanced.py`, `nexus_http_daemon.py`
**Outputs**: `supervisord.conf` with program sections, XML-RPC control
**Verification**: `supervisorctl status` shows all programs running, no duplicates
**Rollback**: Kill supervisord, restart MCP manually

**Subtasks**:
1. (15 min) Write `supervisord.conf`:
   ```ini
   [program:nexus-mcp]
   command=python /home/roshan/nexus/server/nexus-mcp-enhanced.py
   directory=/home/roshan/nexus
   autostart=true
   autorestart=true
   startsecs=3
   stopwaitsecs=10
   killasgroup=true
   priority=10

   [program:nexus-http-daemon]
   command=python /home/roshan/nexus/server/nexus_http_daemon.py
   directory=/home/roshan/nexus
   autostart=true
   autorestart=true
   priority=20
   depends=nexus-mcp
   ```
2. (10 min) `pip install supervisor` (or apt install supervisor)
3. (10 min) Start: `supervisord -c /home/roshan/nexus/supervisord.conf`
4. (10 min) Verify: `supervisorctl status` — both programs RUNNING
5. (15 min) Kill test: kill MCP PID, verify supervisord restarts within 3s

**Pass/Fail**: supervisorctl shows RUNNING for all programs, kill test passes

---

#### B2: XML-RPC Programmatic Control Bridge
**Research basis**: Supervisor XML-RPC API
**NEXUS issue**: Routing layer can't control process lifecycle
**Owner**: Infrastructure
**Risk**: Low (additive)
**Inputs**: supervisord XML-RPC at `http://localhost:9001`
**Outputs**: `process_control.py` with start/stop/status/restart functions
**Verification**: Call `process_control.restart("nexus-mcp")`, verify MCP restarts
**Rollback**: Don't import process_control.py

**Subtasks**:
1. (15 min) Write `process_control.py`:
   ```python
   import xmlrpc.client
   class ProcessControl:
       def __init__(self, url="http://localhost:9001"):
           self.server = xmlrpc.client.ServerProxy(url)
       def status(self, program): ...
       def restart(self, program): ...
       def stop(self, program): ...
       def start(self, program): ...
       def list_all(self): ...
   ```
2. (10 min) Wire into `nexus_health_check` — report process status
3. (10 min) Wire into `unified_route` — if MCP not running, auto-restart before routing
4. (5 min) Smoke test: restart MCP via XML-RPC

**Pass/Fail**: `process_control.restart("nexus-mcp")` returns success, MCP comes back RUNNING

---

#### B3: Config Audit & Schema Migration
**Research basis**: Paper 1.4 (ScaleMCP incremental), internal audit
**NEXUS issue**: `db/schema.sql` has old CHECK constraint, dual feedback paths, 841 unassigned
**Owner**: DB
**Risk**: Medium (schema changes)
**Inputs**: `db/schema.sql`, `db.py`, live `nexus.db`
**Outputs**: Schema.sql matches live DB, all constraints consistent
**Verification**: `sqlite3 nexus.db ".schema" | diff - schema.sql` shows no diffs
**Rollback**: Export live schema, revert if migration breaks

**Subtasks**:
1. (10 min) Dump live schema: `sqlite3 nexus.db ".schema" > /tmp/live_schema.sql`
2. (10 min) Diff against `db/schema.sql`, identify drift
3. (15 min) Update `db/schema.sql` to match live (add EFC columns, CHECK updates)
4. (10 min) Write migration script for fresh installs
5. (15 min) Assign 841 unassigned resources to clusters via `assign-nexus-clusters.py`

**Pass/Fail**: schema.sql matches live DB, 0 unassigned resources

---

#### B4: Daemon Health & Watchdog
**Research basis**: Circus flapping detection, abtop monitoring
**NEXUS issue**: HTTP daemon unsupervised, crash-looping undetected
**Owner**: Infrastructure
**Risk**: Low (additive monitoring)
**Inputs**: `nexus_http_daemon.py` (port 8080), supervisord
**Outputs**: `health_watchdog.py` — periodic health check, alert on crash
**Verification**: Kill daemon, watchdog detects within 10s, supervisord restarts
**Rollback**: Disable watchdog

**Subtasks**:
1. (15 min) Write `health_watchdog.py`:
   ```python
   def check_http_health():
       try: r = requests.get("http://127.0.0.1:8080/health", timeout=5)
       return r.status_code == 200
   ```
2. (10 min) Add flapping detection: >3 restarts in 60s = alert
3. (10 min) Wire into supervisord event listener
4. (5 min) Smoke test: kill daemon, verify restart + alert

**Pass/Fail**: Kill → detect → restart → alert, all within 15s

---

#### B5: Consolidate Dual Feedback Paths
**Research basis**: Paper 1.1 (single feedback path), Paper 7.1 (quality-weighted)
**NEXUS issue**: HTTP endpoint writes routing_log only, MCP writes outcome_log only
**Owner**: feedback.py + learning.py
**Risk**: Medium (changes data flow)
**Inputs**: `nexus_http_daemon.py`, `nexus-mcp-enhanced.py`, `learning.py`
**Outputs**: Single `_bridge_outcome_log()` called from both paths
**Verification**: Insert outcome via HTTP, verify it appears in outcome_log
**Rollback**: Revert bridge function

**Subtasks**:
1. (15 min) Audit both write paths: HTTP daemon vs MCP server
2. (20 min) Write `consolidate_feedback()` — single entry point, writes to both routing_log and outcome_log
3. (10 min) Replace both call sites with `consolidate_feedback()`
4. (10 min) Smoke test: route via HTTP, verify outcome_log row appears with EFC

**Pass/Fail**: Single insert via HTTP shows in both routing_log AND outcome_log

---

#### B6: Production Deploy (USE_UNIFIED_ROUTER=1)
**Research basis**: N/A (operational cutover)
**NEXUS issue**: Cold-start engine built but flag OFF
**Owner**: Operations
**Risk**: HIGH — kills live MCP session, must be done out-of-session
**Inputs**: All Track A work (A1-A5), Track B work (B1-B5)
**Outputs**: `USE_UNIFIED_ROUTER=1` in env, MCP restarted under supervisord
**Verification**: `curl http://127.0.0.1:8080/health` returns 200, routing works
**Rollback**: `USE_UNIFIED_ROUTER=0`, restart MCP

**Subtasks**:
1. (5 min) Set `export USE_UNIFIED_ROUTER=1` in supervisord environment
2. (5 min) `supervisorctl restart nexus-mcp`
3. (10 min) Verify: route 5 test queries, confirm cold resources appear in 30% of results
4. (10 min) Verify: health check passes, all metrics sane
5. (10 min) Log go-live timestamp for 7-day verification clock

**Pass/Fail**: 5/5 test queries return results, ≥1 includes cold resource

---

### Track C: Second Brain & TUI Bridge (7 tasks, ~7 hrs)

---

#### C1: tmux-Based Multi-IDE Orchestrator
**Research basis**: Agent-of-Empires (2.7K★, tmux orchestration), rmux (2.2K★, typed SDK)
**NEXUS issue**: No TUI IDE integration layer
**Owner**: New module: `tui_bridge.py`
**Risk**: Low (new module, doesn't touch existing)
**Inputs**: tmux installed, IDE CLIs (claude-code, opencode, codex, hermes)
**Outputs**: `TUIOrchestrator` class — spawn, monitor, kill IDE sessions via tmux
**Verification**: Spawn 2 IDE sessions, verify both running in tmux
**Rollback**: Kill tmux sessions

**Subtasks**:
1. (20 min) Write `tui_bridge.py`:
   ```python
   class TUIOrchestrator:
       def spawn(self, ide_name, project_dir, session_name=None): ...
       def list_sessions(self): ...
       def send_keys(self, session, keys): ...
       def kill_session(self, session): ...
       def capture_output(self, session, lines=100): ...
       def health_check(self, session): ...
   ```
2. (15 min) Implement spawn: `tmux new-session -d -s {name} "{ide_cmd} --cwd {dir}"`
3. (10 min) Implement monitoring: `tmux list-sessions`, `tmux capture-pane`
4. (10 min) Implement kill: `tmux kill-session -t {name}`
5. (15 min) Config: `tui_config.yaml` mapping IDE names → commands

**Pass/Fail**: Spawn 2 sessions (claude-code, opencode), both appear in `tmux list-sessions`

---

#### C2: abtop-Style Agent Monitor
**Research basis**: abtop (3.1K★, htop for AI agents), Paper 8.1 (FTSR/MRC/TLT/CDR)
**NEXUS issue**: No harness efficiency tracking
**Owner**: New module: `agent_monitor.py`
**Risk**: Low (read-only monitoring)
**Inputs**: tmux sessions, routing_log, outcome_log
**Outputs**: Real-time dashboard: tokens/context/rate-limits per session
**Verification**: Start monitor, spawn 1 IDE, verify it appears in dashboard
**Rollback**: Kill monitor process

**Subtasks**:
1. (20 min) Write `agent_monitor.py`:
   ```python
   class AgentMonitor:
       def get_session_stats(self, session_name): ...
       def get_token_usage(self, session_name): ...
       def get_context_window(self, session_name): ...
       def get_rate_limit(self, session_name): ...
       def render_dashboard(self): ...  # Rich/curses TUI
   ```
2. (15 min) Parse IDE output for token counts (regex on stdout capture)
3. (10 min) Query routing_log for per-session routing history
4. (15 min) Render dashboard with `rich` library
5. (10 min) Smoke test: spawn IDE, run monitor, verify session appears

**Pass/Fail**: Dashboard shows ≥1 active session with non-zero token count

---

#### C3: Graphify Integration for Knowledge Graph Completion
**Research basis**: Graphify (local install, community detection), Paper 4.2 (GraphSAGE)
**NEXUS issue**: 2117/2815 resources have no edges (sparse KG)
**Owner**: `graphify_nexus_bridge.py` (exists)
**Risk**: Low (read-only graph construction)
**Inputs**: NEXUS resources table, resource_edges table, Graphify at /home/roshan/graphify
**Outputs**: Predicted edges for isolated resources, community detection on full graph
**Verification**: Run Graphify on NEXUS resource catalog, verify new edges created
**Rollback**: Don't insert predicted edges

**Subtasks**:
1. (15 min) Export NEXUS resources to Graphify-compatible format (nodes.json, edges.json)
2. (20 min) Run Graphify community detection — group 2815 resources into communities
3. (15 min) Extract predicted edges: resources in same community with no edge → predicted edge
4. (10 min) Compute god-nodes: most connected resources per community
5. (15 min) Insert predicted edges with `edge_type='predicted_same_community'`, `weight=community_affinity`
6. (10 min) Verify: `SELECT COUNT(*) FROM resource_edges WHERE edge_type='predicted_same_community'` > 0

**Pass/Fail**: ≥500 new predicted edges inserted, isolated resource count drops below 1500

---

#### C4: GraphSAGE Edge Prediction (Structural Embeddings)
**Research basis**: Paper 4.2 (GraphSAGE inductive over GCN)
**NEXUS issue**: Semantic-only edges (similar_to) miss structural relationships
**Owner**: New module: `edge_predictor.py`
**Risk**: Low (additive to graph)
**Inputs**: resource_edges, resources embeddings (FAISS)
**Outputs**: `structural_embedding` per resource, predicted edges with confidence
**Verification**: For 10 resource pairs, predict edge, verify ≥6 are semantically plausible
**Rollback**: Don't insert predicted edges

**Subtasks**:
1. (20 min) Install PyTorch Geometric (if not present): `pip install torch_geometric`
2. (25 min) Write `edge_predictor.py`:
   ```python
   import torch
   from torch_geometric.nn import SAGEConv
   class NexusEdgePredictor:
       def __init__(self, in_dim=384, hidden=128): ...
       def build_graph(self, resources, edges): ...
       def train(self, epochs=50): ...
       def predict_edges(self, threshold=0.7): ...
   ```
3. (15 min) Node features: concat(embedding, [type_onehot, cluster_onehot, success_rate, trigger_count])
4. (15 min) Train on existing edges (9126), predict missing for 2117 isolated
5. (10 min) Insert top-K predicted edges with `edge_type='predicted_graphsage'`, `weight=confidence`
6. (10 min) Smoke test: predict 20 edges, manual review for plausibility

**Pass/Fail**: ≥60% of predicted edges are semantically plausible on manual review

---

#### C5: Autonomous Tool Calling Pipeline
**Research basis**: Paper 1.3 (AnyTool), Paper 6.2 (DSPy DAG), auto_invoke.py (exists)
**NEXUS issue**: No autonomous pipeline — manual find_skills() still needed
**Owner**: `auto_invoke.py` (490 lines, exists) + `event_bridge.py` (160 lines, exists)
**Risk**: Medium (changes when tools get triggered)
**Inputs**: auto_invoke.py (threshold 0.68), event_bridge.py, reflection_loop.py
**Outputs**: Event-driven trigger — no manual find_skills() needed
**Verification**: Send test error event, verify auto_invoke triggers + routes
**Rollback**: Raise threshold to 1.0 (effectively disables)

**Subtasks**:
1. (15 min) Lower `AUTO_TRIGGER_THRESHOLD` from 0.68 to 0.55 (per research: 0.5-0.6 optimal)
2. (20 min) Wire `event_bridge.py` to listen for IDE events (error, task_complete, context_need)
3. (15 min) Add trigger types: `POST_SUCCESS_LEARNING` (after success, update edges), `PRE_TASK_ROUTING` (before user query, pre-warm)
4. (15 min) Add reflection loop: on failure, call `self_reflector.py` → find alternatives → retry
5. (15 min) Log all auto-triggers: `INSERT INTO auto_trigger_log (event, trigger_type, resource, score)`
6. (10 min) Smoke test: inject error event, verify auto-trigger fires

**Pass/Fail**: Error event triggers auto_invoke, routing result returned, logged in auto_trigger_log

---

#### C6: Task Difficulty Estimation
**Research basis**: Paper 5.1 (IRT log-odds), Paper 5.2 (linear regression, quintile binning)
**NEXUS issue**: No difficulty normalization — easy/hard tasks compared equally
**Owner**: New module: `difficulty_estimator.py`
**Risk**: Low (additive, used for normalization only)
**Inputs**: routing_log (task summaries, success/fail), outcome_log
**Outputs**: `difficulty_score` per task (log-odds), `ability_score` per resource
**Verification**: Compute difficulty for 50 tasks, verify distribution is sane (not all same value)
**Rollback**: Don't use difficulty in routing (purely additive)

**Subtasks**:
1. (20 min) Write `difficulty_estimator.py`:
   ```python
   def estimate_difficulty(task_summary, routing_log):
       success_rate = get_success_rate(task_summary)
       difficulty = -math.log(success_rate / (1 - success_rate))  # log-odds
       return bin_to_quintile(difficulty)
   ```
2. (15 min) Features: description length, tools attempted, tokens consumed, error count
3. (10 min) `ALTER TABLE routing_log ADD COLUMN difficulty_score REAL;`
4. (15 min) Backfill: compute difficulty for all existing routing_log entries
5. (10 min) Add to routing context: difficulty included in route result for normalization

**Pass/Fail**: 50 tasks get difficulty scores across ≥3 quintiles (not all same)

---

#### C7: LangGraph State Machine for Plan Execution
**Research basis**: LangGraph (36K★), Paper 6.2 (DSPy DAG composition)
**NEXUS issue**: Plan executor exists but no state persistence/checkpointing
**Owner**: `nexus_plan_executor.py` (exists) + new `plan_state_machine.py`
**Risk**: Medium (changes plan execution model)
**Inputs**: nexus_plan_executor.py, plan_tracker.py
**Outputs**: LangGraph state machine with checkpointing
**Verification**: Execute 3-phase plan, verify checkpoints persist across restart
**Rollback**: Use existing executor

**Subtasks**:
1. (20 min) `pip install langgraph` (if not present)
2. (25 min) Define states: `INTAKE → ROUTE → EXECUTE → VERIFY → FEEDBACK → COMPLETE/RETRY`
3. (20 min) Define transitions with guards: VERIFY passes → COMPLETE, VERIFY fails → RETRY (max 3)
4. (15 min) Add checkpointing: state persists to SQLite after each transition
5. (15 min) Wire into `nexus_plan_executor.py` — replace ad-hoc phase tracking
6. (10 min) Smoke test: execute 2-phase plan, kill mid-execution, restart, verify resume from checkpoint

**Pass/Fail**: Plan resumes from last checkpoint after kill+restart

---

### Track D: Integration & Production (3 tasks, ~3 hrs)

---

#### D1: Integration Test
**Research basis**: N/A
**Owner**: QA
**Risk**: None (read-only testing)
**Inputs**: All Track A/B/C outputs
**Outputs**: Integration test report
**Verification**: All systems work together

**Subtasks**:
1. (20 min) Route query → verify RRF fusion → verify cold resource in 30% → verify EFC computed
2. (15 min) Spawn IDE via tmux → verify monitor shows it → verify auto-trigger fires on error
3. (15 min) Run Graphify → verify new edges → run GraphSAGE → verify predicted edges
4. (10 min) Execute plan via LangGraph → kill → restart → verify checkpoint resume
5. (10 min) Full health check: FTSR, MRC, TLT, CDR, EFC columns, cold-start, process status

**Pass/Fail**: All 5 integration scenarios pass

---

#### D2: Production Cutover
**Research basis**: N/A
**Owner**: Operations
**Risk**: HIGH — production change
**Inputs**: D1 passed
**Outputs**: Live system on v3 architecture
**Verification**: 5 real queries route correctly, health check green

**Subtasks**:
1. (5 min) `supervisorctl restart nexus-mcp nexus-http-daemon`
2. (10 min) Verify health: `curl http://127.0.0.1:8080/health` → 200
3. (10 min) Route 5 real queries, verify results
4. (5 min) Log go-live timestamp
5. (5 min) Start 7-day verification clock

**Pass/Fail**: 5/5 real queries return valid results, health check all green

---

#### D3: 7-Day Verification
**Research basis**: Paper 2.1 (cold-start convergence), Paper 8.1 (CDR)
**Owner**: Monitoring
**Risk**: None (observation only)
**Inputs**: 7 days of production traffic
**Outputs**: Verification report

**Metrics to track**:
| Day | Cold-start % | Feedback coverage | FTSR | MRC | EFC columns non-NULL |
|----|-------------|-------------------|------|-----|---------------------|
| 0  | 66.6%       | 34.8%             | ?    | ?   | 0%                  |
| 1  | <65%        | >36%              | ?    | ?   | >50%                |
| 3  | <58%        | >45%              | ?    | ?   | >80%                |
| 7  | <50%        | >55%              | >50% | <2.0| >95%                |

**Pass/Fail**: Day 7 cold-start <50%, coverage >55%, FTSR >50%, EFC >95% non-NULL

---

## DEPENDENCY GRAPH (DAG)

```
A1 (RRF Fusion) ──────┐
A2 (Quality TS) ───────┤
A3 (KNN Cold-Start) ───┤── A5 (EFC Fix) ── A6 (Audit) ──┐
A4 (Active Explore) ───┘                                │
                                                         │
B1 (supervisord) ─── B2 (XML-RPC) ── B3 (Schema) ── B4 (Watchdog) ── B5 (Consolidate) ── B6 (Deploy) ──┤
                                                                                                       │
C1 (tmux TUI) ── C2 (abtop Monitor) ──┐                                                                  │
C3 (Graphify) ── C4 (GraphSAGE) ──────┤── C5 (Autonomous) ── C6 (Difficulty) ── C7 (LangGraph) ────────┤
                                                                                                       │
                                              D1 (Integration) ── D2 (Cutover) ── D3 (7-Day Verify) ─── END
```

### Parallelizable Groups

| Group | Tasks | Can Run In Parallel | Est. Time |
|-------|-------|--------------------|-----------| 
| G1 | A1, A2, A3, B1, C1, C3 | All independent | 1 hr |
| G2 | A4, B2, B3, C2, C4, C6 | After G1 deps met | 1 hr |
| G3 | A5, B4, C5, C6 | After A2/A3/B1 | 1 hr |
| G4 | A6, B5, C7 | After A5/B4/C5 | 1 hr |
| G5 | B6 (Deploy) | After ALL A + B tasks | 30 min |
| G6 | D1 (Integration) | After ALL tracks | 1 hr |
| G7 | D2 (Cutover) | After D1 | 30 min |
| G8 | D3 (7-Day) | After D2 | 7 days |

**Total estimated active work time**: ~8 hours (parallelized)
**Total wall-clock time**: ~8 hours work + 7 days verification = 7 days + 8 hrs

---

## RESOURCE MAPPING (NEXUS Thompson Sampling)

| Task | Cluster | Recommended Skills | Confidence |
|------|---------|-------------------|------------|
| A1-A6 | analyzer_planner | systematic-debugging, test-driven-development | 0.85 |
| B1-B6 | devops_infra | deployment-patterns, careful | 0.78 |
| C1-C2 | frontend_ui | browser, design-html | 0.62 |
| C3-C4 | knowledge_wiki | graphify, learn | 0.80 |
| C5-C6 | analyzer_planner | auto_invoke, reflection | 0.72 |
| C7 | architect | writing-plans, plan | 0.75 |
| D1-D3 | quality_security | qa, verify, canary | 0.82 |

---

## MEASURED BASELINE (Before Plan)

| Metric | Current Value | Source |
|--------|--------------|--------|
| Total resources | 2,815 | `SELECT COUNT(*) FROM resources` |
| Active resources | 2,155 | `SELECT COUNT(*) FROM resources WHERE status='active'` |
| Never-triggered | 1,877 (66.6%) | `SELECT COUNT(*) FROM resources WHERE times_triggered=0` |
| Total edges | 9,126 | `SELECT COUNT(*) FROM resource_edges` |
| Isolated resources | 2,117 | resources with no edges |
| Feedback coverage | 34.8% | `outcome / routed` ratio |
| Routing accuracy (feedback'd) | 97.6% | correct / (correct+wrong) on triggered |
| v3/v4 agreement | 1/6 (16.7%) | top-1 match across 6 queries |
| EFC columns non-NULL | 0% | all 7 columns NULL |
| Cold-start flag | OFF | `USE_UNIFIED_ROUTER=0` |
| Process supervisor | None | ad-hoc subprocess |
| TUI integration | None | no bridge |
| Harness metrics | None | no FTSR/MRC/TLT/CDR |

## TARGET STATE (After Plan + 7 Days)

| Metric | Target | Verification |
|--------|--------|-------------|
| Never-triggered | <50% (<940) | `SELECT COUNT(*) FROM resources WHERE times_triggered=0` |
| Feedback coverage | >80% | outcome / routed ratio |
| Routing accuracy | >95% (on feedback'd) | correct / (correct+wrong) |
| v3/v4 agreement | N/A (unified via RRF) | RRF replaces both |
| EFC columns non-NULL | >95% | `SELECT COUNT(*) FROM outcome_log WHERE efc IS NOT NULL` |
| Cold-start flag | ON | `USE_UNIFIED_ROUTER=1` |
| Process supervisor | supervisord | `supervisorctl status` all RUNNING |
| TUI integration | tmux-based | `tmux list-sessions` shows IDE sessions |
| Harness metrics | FTSR/MRC/TLT/CDR | health check output |
| Isolated resources | <1,500 | resources with no edges |
| Predicted edges | >500 | `SELECT COUNT(*) FROM resource_edges WHERE edge_type LIKE 'predicted%'` |

---

## RISKS & MITIGATIONS

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| supervisord cutover kills live session | HIGH | HIGH | Do out-of-session, user confirms |
| EFC UPDATE bug persists | MEDIUM | MEDIUM | Debug with sqlite3 CLI, not code |
| GraphSAGE too slow on 2815 nodes | LOW | LOW | Sample 25 neighbors, 2 layers (bounded) |
| LangGraph adds dependency weight | LOW | LOW | Only for plan executor, not core routing |
| Cold-start epsilon too aggressive | MEDIUM | LOW | Start at 0.30, decay to 0.01 over 10K queries |
| Quality-weighted TS destabilizes beliefs | LOW | MEDIUM | Default quality=1.0 preserves old behavior |
| tmux sessions leak memory | LOW | LOW | Watchdog kills stale sessions |

---

## FILE INVENTORY (Files to Create/Modify)

### New Files
- `/home/roshan/nexus/server/rrf_fusion.py` — RRF ranking fusion
- `/home/roshan/nexus/server/cold_start_priors.py` — KNN-informed Beta priors
- `/home/roshan/nexus/server/process_control.py` — supervisord XML-RPC bridge
- `/home/roshan/nexus/server/health_watchdog.py` — daemon health monitor
- `/home/roshan/nexus/server/tui_bridge.py` — tmux multi-IDE orchestrator
- `/home/roshan/nexus/server/agent_monitor.py` — abtop-style agent dashboard
- `/home/roshan/nexus/server/edge_predictor.py` — GraphSAGE edge prediction
- `/home/roshan/nexus/server/difficulty_estimator.py` — IRT task difficulty
- `/home/roshan/nexus/server/plan_state_machine.py` — LangGraph state machine
- `/home/roshan/nexus/supervisord.conf` — process supervisor config
- `/home/roshan/nexus/server/tui_config.yaml` — IDE command mappings

### Modified Files
- `/home/roshan/nexus/server/learning.py` — quality-weighted TS, EFC blend, consolidate feedback
- `/home/roshan/nexus/server/unified.py` — RRF fusion, cold-start priors, active exploration
- `/home/roshan/nexus/server/efc.py` — fix column population bug
- `/home/roshan/nexus/server/nexus_http_daemon.py` — fix EFC UPDATE, consolidate feedback
- `/home/roshan/nexus/server/auto_invoke.py` — lower threshold, wire event bridge
- `/home/roshan/nexus/server/nexus_health_orchestrator.py` — add FTSR/MRC/TLT/CDR
- `/home/roshan/nexus/server/graphify_nexus_bridge.py` — Graphify integration
- `/home/roshan/nexus/server/nexus_plan_executor.py` — LangGraph state machine
- `/home/roshan/nexus/db/schema.sql` — sync with live DB
- `/home/roshan/nexus/core/config.py` — USE_UNIFIED_ROUTER flag

---

## APPROVAL CHECKPOINT

This plan is presented for user approval before execution.

**Decision points requiring user input**:
1. B6/D2: Production cutover timing — when to flip USE_UNIFIED_ROUTER=1 (must be out-of-session)
2. C7: LangGraph dependency — OK to add `pip install langgraph`?
3. C4: PyTorch Geometric — OK to install (large dependency)?
4. B1: supervisord — OK to install and take over process management?
5. Parallel execution: OK to run Track A/B/C in parallel via delegates?

**Default if approved**: Execute all tracks in parallel (G1→G2→G3→G4→G5→G6→G7→G8), with B6/D2 deferred to user-directed session.

---

*Plan ID: NEXUS-SB-v3-2026-06-26*
*Research basis: 52 papers/repos (arXiv 15 + SSRN 17 + GitHub 20)*
*Estimated active work: 8 hours parallelized*
*Estimated wall-clock: 8 hrs work + 7 days verification*