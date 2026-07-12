# NEXUS System Design Plan v1
## Large-Scale Resource + Harness Engine Management

**Created:** 2026-07-12  
**Status:** P0–P5 APPROVED & EXECUTED (2026-07-12) — system design loop complete  
**Inputs:** Atom audit (v4/v5, record_outcome, harness) · first principles · ScaleMCP/Tool Attention/HGMF · Anthropic multi-agent research · Hermes OS · Claude OS patterns · CodeNomad · AgentSpace · browser-harness  

---

## 0. Root Question (First Principles)

**What problem must NEXUS actually solve?**

> Given a human task and ~3k heterogeneous resources (skills, agents, MCP tools, docs, hooks, harness channels), select a *small, correct, executable* working set, run it through the right *execution harness*, record only *verified* outcomes, and improve routing over time — without requiring every agent IDE to reinvent discovery.

Everything else (dashboard, multi-IDE, second brain, academic search) is either an **enabler** of that loop or **out of scope** until the loop is closed.

### Irreducible loop

```
task → retrieve(k) → authorize → execute(harness) → observe → learn → (better retrieve)
```

If any arrow is broken, scale multiplies failure (which is our current state).

---

## 1. Root Causes (Not Symptoms)

Prior audits listed 10+ gaps. First-principles collapse them into **seven root causes**:

| ID | Root cause | Why it is root | Symptoms it causes |
|----|------------|----------------|--------------------|
| **RC1** | **No hard layer boundaries** | One monorepo pretends to be LLM gateway + resource index + learning brain + research harness + IDE bridge | Dual ports (8000/8080), dual products, dual docs |
| **RC2** | **Process ownership is not load-bearing** | systemd + supervisord + IDE spawn; config on disk ≠ process memory | Stale daemon on v4 while disk defaults v5; env flags lie |
| **RC3** | **Feedback invents labels** | Neutral `routed` treated as wrong; NULL→partial auto-close | Partial floods, fake Thompson updates, edge graph static |
| **RC4** | **Router composition has side effects** | v5 L1 calls full v4 `route_task` which logs/learns | Double cost, wrong skill credited, cutover not pure |
| **RC5** | **Catalog is a dump, not a lifecycle** | Ingest without promote/archive/freshness SLAs | 48% cold, 261 zero-token, dual cluster SoT residual |
| **RC6** | **Harness is a toolkit, not a subsystem** | 25 tools registered; not retrieved/scoped as execution channels | Research never in production ranking; supervisor harness STOPPED |
| **RC7** | **No unified execution contract across IDEs** | Each CLI (Claude/OpenCode/Hermes/Codex) re-discovers tools | NEXUS MCP missing in some sessions; ruflo timeouts |

**Design law:** Fix root causes as architectural contracts. Do not add more routing algorithms until RC1–RC4 are closed.

---

## 2. Research Synthesis (What Works at Scale)

### 2.1 Papers / systems (tool scale)

| Source | Principle to adopt | NEXUS application |
|--------|--------------------|-------------------|
| **ScaleMCP** (arXiv:2505.06416) | MCP as SoT + CRUD sync; **TDWA** embeddings; agent *retrieves then loads* tools | Resource catalog CRUD; never inject 2.8k schemas; TDWA for skill/MCP docs |
| **Dynamic ReAct** (arXiv:2509.20386) | Search-and-load; ≤50% tool load; multi-stage selection | Two-stage: cluster → top-k tools; session tool budget |
| **Tool Attention** (arXiv:2604.21816) | Dynamic gating + lazy schema loading | Keep ISO gate; separate from learning writes |
| **HGMF** (hierarchical cluster→retrieve→rerank) | L1 cluster, L2 retrieve, L3 rerank | Keep v5 *shape*; make L1 pure (no log) |
| **Bruch et al.** convex > RRF cross-scale | Fixed interpretable blend for heterogeneous ranks | Keep convex L3; shadow wRRF for offline only |
| **Thompson / quality-weighted Beta** | Update only on terminal outcomes; quality ∈ [0,1] | Enforce early-return on `routed`/`pending` |
| **EFC** (arXiv:2605.29682) | Effective feedback compute for harness scaling | Outcome_log EFC only for verified closes |
| **Anthropic multi-agent research** | Orchestrator-worker; tool design critical; scale effort to complexity; parallel subagents | Research harness = lead + N channel workers; citations |
| **ACE-Router / semantic tool selection** | History-aware routing; flat search degrades >~1k tools | Hierarchical mandatory above 1k active |

### 2.2 Open-source “OS” patterns

#### Hermes Agent (agent-first OS)
- **Central tool registry** + **toolsets** (group tools; never dump all)
- **One agent loop**, many entry points (CLI/gateway/ACP)
- **SQLite + FTS5** session state
- **Profile isolation** (`-p name` → separate home/config/memory)
- **Loose coupling** via registry + availability checks  
**Adopt:** toolsets = our clusters/bundles; profile = `NEXUS_PROJECT`; single agent-loop-facing API.

#### Claude OS / multi-agent OS patterns
- **CLAUDE.md as kernel** (policy, not code dump)
- **Lazy-loaded skills**; hooks as lifecycle
- **Zero-trust** allow/deny for tools/MCP
- **Shared memory + mission board** for multi-agent  
**Adopt:** kernel policy file; skill lazy load; permission plane for high-risk harness tools.

#### Anthropic Research architecture
- Lead plans → memory of plan → parallel subagents → cite  
**Adopt for harness research:** not “merge paper titles as skills”, but **orchestrated channel workers** returning structured `ResearchArtifact[]`.

### 2.3 Your related projects — role assignment

| Project | What it is | Role in the NEXUS system map |
|---------|------------|------------------------------|
| **[CodeNomad](https://github.com/Roshan7869/CodeNomad)** | OpenCode **cockpit** (multi-instance, sidecars, sessions) | **Control / UX plane** — operator surface for sessions, sidecars to NEXUS dashboard + harness UIs. Not a resource brain. |
| **[AgentSpace](https://github.com/Roshan7869/AgentSpace)** | Human+agent **workspace**; **AgentRouter** normalizes Claude/Codex/OpenCode/OpenClaw/Hermes | **Execution + org plane** — agent identity, permissions, task queue, harness switching. NEXUS should plug in as **resource router service** that AgentRouter consults, not replace AgentRouter. |
| **[browser-harness](https://github.com/Roshan7869/browser-harness)** | Thin self-healing **CDP harness**; agent edits helpers; domain-skills | **Domain harness pattern** — protected core + agent-editable workspace; self-heal. Blueprint for *all* NEXUS harness channels (browser, research, terminal). |
| **NEXUS (this repo)** | Resource intelligence + learning + context/research tools | **Resource + learning plane** — catalog, retrieve, rank, feedback. |

**Bitter lesson of harnesses (browser-use):** don’t wrap the model’s tools forever; ship a **thin protected core** + **agent-editable skill layer**. For NEXUS this means: core retrieval/learning is protected; domain channel adapters and skill bodies can evolve; never freeze 2.8k resources as permanent context.

---

## 3. Target Architecture — “NEXUS OS” Layers

```
┌─────────────────────────────────────────────────────────────────────────┐
│ L5 CONTROL PLANE                                                         │
│  CodeNomad / Claude Code / Hermes CLI / AgentSpace Web                   │
│  Policy: CLAUDE.md / AGENTS.md / zero-trust tool grants                  │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │ MCP / HTTP contract (versioned)
┌───────────────────────────────▼─────────────────────────────────────────┐
│ L4 EXECUTION PLANE (AgentRouter-style)                                   │
│  Harness adapters: claude | opencode | hermes | codex | openclaw         │
│  Normalize: events, sessions, diagnostics, outcomes                      │
│  Owns: run task with loaded toolset — NOT resource discovery             │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │ ToolSession { tools[], budget, project }
┌───────────────────────────────▼─────────────────────────────────────────┐
│ L3 HARNESS ENGINE                                                        │
│  Channels: research | browser | web | memory | site                      │
│  Pattern: thin core + domain-skills (browser-harness style)              │
│  Orchestrator-worker for research (Anthropic pattern)                    │
│  Outputs: Artifact[] — NEVER fake resources.name rows                    │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │ retrieve / load / report
┌───────────────────────────────▼─────────────────────────────────────────┐
│ L2 RESOURCE INTELLIGENCE (NEXUS core product)                            │
│  Catalog · Hierarchical retrieve · Pure rank · Session load              │
│  Learning: terminal outcomes only → Thompson / edges / affinity          │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │ CRUD / index / FAISS
┌───────────────────────────────▼─────────────────────────────────────────┐
│ L1 DATA PLANE                                                            │
│  SQLite/PG catalog · embeddings · edges · outcome_log · sessions         │
│  Single SoT for cluster membership · lifecycle states                    │
└─────────────────────────────────────────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────────┐
│ L0 RUNTIME PLANE                                                         │
│  ONE supervisor (or systemd unit group) · health · rainbow deploys       │
│  Single config SoT → injected env · version stamp in /health             │
└─────────────────────────────────────────────────────────────────────────┘
```

### Layer rules (non-negotiable)

1. **L2 never executes side-effectful tools** (no browser, no ship). It only ranks and loads schemas.  
2. **L3 never writes resource counters** except via L2 `report_outcome` with terminal labels.  
3. **L4 never reimplements retrieval** — calls L2 `session.open(task)`.  
4. **L1 has one cluster SoT** (`cluster_membership`; `resources.cluster` is denormalized cache).  
5. **L0 restarts are gated** by `method == expected_router` smoke test.

---

## 4. Technical Design — Managing ~3k Resources

### 4.1 Resource lifecycle (fix RC5)

```
discovered → draft → active → degraded → archived → purged
                ↑        │
                └── promote after body+embedding+cluster+type valid
```

| State | Retrievable? | Learnable? | Rules |
|-------|--------------|------------|-------|
| draft | no | no | incomplete ingest |
| active | yes | yes | token_count ≥ N, embedding present |
| degraded | optional (penalty) | yes | error_rate high |
| archived | no (except admin) | no | cold 90d OR zero-token stubs |
| purged | deleted | — | after retention |

**Cold-start campaign:** UCB1 / info-gain only over **active** with times_triggered=0; never over archived.

### 4.2 Hierarchical retrieval (ScaleMCP + HGMF + clusters)

```
session.open(task, project, budget=k):
  1. classify intent + complexity (effort budget: 1 / 2-4 / 10+ sub-tools)
  2. cluster_route → top 1-2 clusters (+ general fallback if conf < θ)
  3. constrained retrieve (FTS + dense / TDWA) within cluster IDs
  4. pure rerank (v5 L3 math)  — NO DB writes
  5. optional harness plan (if research/browse intent) → L3 channel list
  6. return ToolSession:
       { session_id, tools[k], harness_channels[], model_hint, expires_at }
```

**Hard budget:** default k=8–12 schemas in context (Dynamic ReAct); expand only on explicit agent request (`tools.search` / ScaleMCP-style retriever tool).

### 4.3 Pure router vs learning writer (fix RC4)

| Function | May write DB? | Used by |
|----------|---------------|---------|
| `rank(task) → Ranking` | **No** | v5 L1/L2/L3, HTTP /route, MCP find_skills |
| `open_session(ranking)` | log `routing_log` outcome=`routed` only | session broker |
| `report_outcome(session, label)` | **Yes** full learning | IDE / agent / harness |

**Delete** `_log_decision → record_outcome` from ranking path.  
**v5 L1** must call `rank_v4_pure` or shared intent scorer — never full `route_task` with learning.

### 4.4 Learning contract (fix RC3)

```
Terminal labels: correct | wrong | partial | skip
Non-terminal:    routed | pending | null  → NO counter/Thompson/edge updates
```

- Implement **early return** in `record_outcome` for non-terminal.  
- **Delete** `_auto_close_stale_null` partial invention (or move to separate `orphan_routes` table for ops, not learning).  
- Real closes: IDE events (AgentSpace/Hermes) · MCP `log_outcome` · harness task_complete.  
- Edges update only on terminal correct/wrong (already in code; feed it real labels).

### 4.5 Catalog hygiene jobs (ops, not routing)

| Job | Cadence | Action |
|-----|---------|--------|
| purge zero-token MCP stubs | daily | archive |
| cluster_count sync | continuous trigger | membership → definitions |
| FAISS rebuild | after ≥N catalog changes | 1:1 with active embeddings |
| cold archive | weekly | times_triggered=0 & age>90d |
| outcome coverage report | daily | % terminal closes |

---

## 5. Technical Design — Harness Engine

### 5.1 What a harness is (and is not)

| Is | Is not |
|----|--------|
| Capability channel (browser, research, web) | A skill row in FAISS |
| Thin protected core + editable domain skills | 10k-line framework wrapping the model |
| Returns `Artifact` with citations | Fakes `resources.name = paper title` |
| Self-heals within its domain (browser-harness) | Auto-writes Thompson beliefs for synthetic names |

### 5.2 Harness channel model

```yaml
channel:
  id: research | browser | web_fetch | memory | site:<domain>
  core: protected Python package (stable API)
  domain_skills: agent-editable playbooks (browser-harness style)
  tools_exposed: 3-8 MCP tools max per channel (not 25 flat)
  fusion: RRF/convex at channel level only
  outcome: report_outcome(channel_id, terminal_label, quality)
```

### 5.3 Research harness (Anthropic-shaped)

```
LeadResearcher (NEXUS L3)
  → plan + memory of plan
  → spawn ChannelWorkers: arxiv | s2 | github | pwc | scholar | browser
  → each worker: search → evaluate → return Artifact{title,url,snippet,score,channel}
  → Lead fuses + CitationAgent
  → attach artifacts to ToolSession as context, NOT as skills
```

**Wiring:**  
- Intent `research|paper|arxiv|…` → L2 adds `harness_channels: [research]` to session.  
- Agent or orchestrator **executes** L3; L2 only **plans**.  
- `harness_research` never injects into skill ranking list.

### 5.4 Browser harness (browser-harness-shaped)

- Integrate as optional channel under L3.  
- Protected: CDP daemon + thin helpers.  
- Editable: `domain-skills/<site>/`.  
- NEXUS indexes **domain skill meta** as resources of type `craft` or `harness_skill`, not every CDP primitive.

### 5.5 Toolset exposure (Hermes-style)

Instead of 25 flat `harness_*` tools always registered:

| Toolset | Tools | When loaded |
|---------|-------|-------------|
| `core` | find_skills, log_outcome, session_* | always |
| `research` | harness_research, arxiv, s2, … | research intent or agent request |
| `browser` | browser_* | browse intent |
| `memory` | context_search/store | always light |

**ScaleMCP:** agent may call `tools.search(query)` to load additional toolsets mid-session.

---

## 6. Cross-Project Integration Map

```
                 ┌──────────────┐
                 │  CodeNomad   │  cockpit / multi-session / sidecars
                 └──────┬───────┘
                        │ embeds NEXUS dashboard + session UI
                 ┌──────▼───────┐
                 │ AgentSpace   │  org workspace, permissions, task queue
                 │ AgentRouter  │  run claude|opencode|hermes|…
                 └──────┬───────┘
                        │ before run: POST nexus/session.open
                        │ after run:  POST nexus/report_outcome
                 ┌──────▼───────┐
                 │    NEXUS     │  L0–L3 resource + harness intelligence
                 └──────┬───────┘
                        │ optional channel
                 ┌──────▼───────┐
                 │browser-harness│ domain browser channel
                 └──────────────┘
```

**Integration contract (stable API):**

```
POST /v1/session/open     {task, project, budget, ide}
  → {session_id, tools[], harness_channels[], model_hint}

POST /v1/session/{id}/report
  {outcome: correct|wrong|partial|skip, quality?, notes?}

GET  /v1/health
  {version, git_sha, router_mode, db_ok, flags}
```

AgentSpace AgentRouter and Hermes become **L4 clients**. NEXUS stops pretending to own multi-IDE orchestration (that is AgentSpace/Hermes).

---

## 7. Config & Runtime Design (fix RC2)

### Single SoT

```
nexus.yaml          # learning flags, ports, providers
        │
        ▼
load at process start → LearningConfig + process env
        │
        ▼
/health exposes: config_hash, router_mode, pid_start_time, git_sha
```

### Process model (choose one)

**Recommended:** supervisord as sole parent for:

| Program | Port | Role |
|---------|------|------|
| nexus-api | 8080 | L2 HTTP |
| nexus-mcp | stdio/socket | L2+L3 tools |
| nexus-harness | internal | L3 research/browser workers |
| nexus-dashboard | 8765 | L5 ops UI |

systemd only runs supervisord. **No** dual HTTP daemons.  
**Deploy gate:** smoke `/route` must return `router_mode` matching config or unit fails.

Deprecate LLM gateway :8000 or rehome as separate package `nexus-gateway` — not mixed with resource brain.

---

## 8. Typed DAG Execution Plan

```
goal: "NEXUS OS — reliable large-scale resource + harness management"
approved: false

dependency_graph:
  level_0: [RC2_runtime, RC3_learning_contract, RC5_lifecycle_schema]
  level_1: [RC4_pure_router, RC1_layer_split_api]
  level_2: [RC6_harness_subsystem, RC7_session_contract]
  level_3: [integrations_agentspace_hermes, codenomad_sidecar]
  level_4: [hygiene_jobs, eval_suite, cutover]

phases:

  P0 — Stabilize (1-2 days)
    type: fix
    tasks:
      - Restart processes onto disk cutover; health asserts router_mode
      - record_outcome early-return non-terminal
      - Disable partial auto-invention
      - Fix .mcp.json entrypoint (nexus-mcp-enhanced.py)
      - Single env injection from nexus.yaml
    verify:
      - live method matches USE_UNIFIED_ROUTER
      - 100 synthetic routes → 0 Thompson updates without log_outcome

  P1 — Pure ranking + session open (3-5 days)
    type: implement
    tasks:
      - Extract rank_pure() from route_task (no _log_decision)
      - v5 L1 uses rank_pure only
      - session.open writes routing_log outcome=routed only
      - MCP find_skills → session.open shape
    verify:
      - A/B suite top-1 agreement logged
      - L1 does not increment times_wrong

  P2 — Catalog lifecycle (2-4 days)
    type: implement
    tasks:
      - lifecycle states + promote rules
      - archive zero-token + cold 90d
      - cluster_membership as SoT + denorm cache job
      - FAISS rebuild pipeline
    verify:
      - never_triggered active < 35% within 14d of cold campaign
      - zero dual-cluster divergence

  P3 — Harness subsystem (1 week)
    type: implement
    tasks:
      - Channel interface + research orchestrator-worker
      - Stop injecting papers as skills
      - Toolsets: core | research | browser
      - browser-harness adapter (optional channel)
    verify:
      - research query returns artifacts[] not skill titles
      - research toolset only loaded on research intent

  P4 — Execution plane contract (1 week)
    type: integrate
    tasks:
      - Document AgentRouter/Hermes client flow
      - Wire AgentSpace daemon optional callback
      - CodeNomad sidecar to dashboard + session panel
    verify:
      - one end-to-end task: open session → run → report → belief delta

  P5 — Eval & ops (ongoing)
    type: verify
    tasks:
      - 20-query ab suite in CI
      - coverage dashboard: terminal_close_rate target ≥80%
      - FTSR/MRC harness metrics (existing harness_metrics.py)
```

---

## 9. Success Metrics (measurable)

| Metric | Current (approx) | Target (30d) |
|--------|------------------|--------------|
| Live router_mode matches config | fail (stale v4) | 100% |
| Terminal outcome close rate | ~60% | ≥80% |
| Non-terminal Thompson updates | many | **0** |
| Active never-triggered | ~48% | ≤30% |
| Zero-token active | 261 | 0 |
| Dual cluster divergence | 52 | 0 |
| Edge updates / terminal correct | low | >0.5 edges touched per correct |
| Tools loaded per session | unbounded / noise | ≤12 default |
| Research path uses artifacts | no | yes |
| Session E2E (open→report→belief) | partial | 100% of instrumented IDEs |

---

## 10. Anti-Patterns (explicit bans)

1. Adding a new router (v6) before pure rank + learning contract.  
2. Auto-labeling outcomes as correct/partial without signal.  
3. Registering all harness tools globally.  
4. Merging paper titles into `resources` skill ranking.  
5. Three process managers for one service.  
6. Treating CodeNomad/AgentSpace as competitors to NEXUS — they are **planes**.  
7. Building multi-IDE orchestration inside NEXUS when AgentRouter already exists.  
8. Growing FAISS with stubs “for completeness.”

---

## 11. Decision Log (first principles)

| Decision | Choice | Rejected | Why |
|----------|--------|----------|-----|
| Product focus | Resource + harness intelligence | Full multi-agent OS in NEXUS | AgentSpace/Hermes already OS; avoid duplicate |
| Router | Keep HGMF v5 shape + pure L1 | Rewrite from scratch | Math ok; wiring wrong |
| Learning | Terminal-only | Auto-partial | Audit RC3 |
| Harness | Channel subsystem + toolsets | Flat 25 MCP tools | ScaleMCP/Hermes |
| UX | CodeNomad sidecar | New Electron app | Already cockpit |
| Browser | browser-harness channel | Homegrown Playwright stack | Bitter lesson, self-heal |
| Process | One supervisor tree | Mixed systemd ad-hoc | RC2 |

---

## 12. Approval Gate

**This plan is not self-executing.** Approve with:

- `approve` — execute P0 immediately, then P1  
- `approve all` — full P0–P5 roadmap  
- `modify <section>` — adjust scope  
- `hold` — plan only

**Recommended first ship:** P0 + P1 only (closes RC2, RC3, RC4). Highest vision ROI per hour.

---

## Appendix A — Mapping audit atoms → plan

| Audit finding | Plan fix |
|---------------|----------|
| Stale HTTP v4 | P0 process + health gate |
| v5 L1 side effects | P1 pure rank |
| routed→wrong | P0 learning contract |
| NULL→partial | P0 disable auto-close |
| MCP NULL outcomes | P1 session.open semantics |
| Harness keyword-only | P3 channel plan on session |
| Dual cluster | P2 SoT |
| Cold index | P2 lifecycle |
| No IDE contract | P4 session API |

## Appendix B — Reference links

- ScaleMCP: https://arxiv.org/abs/2505.06416  
- Dynamic ReAct: https://arxiv.org/abs/2509.20386  
- Tool Attention: https://arxiv.org/abs/2604.21816  
- Anthropic multi-agent research: https://www.anthropic.com/engineering/multi-agent-research-system  
- Hermes architecture: https://hermes-agent.nousresearch.com/docs/developer-guide/architecture  
- CodeNomad: https://github.com/Roshan7869/CodeNomad  
- AgentSpace: https://github.com/Roshan7869/AgentSpace  
- browser-harness: https://github.com/Roshan7869/browser-harness  
- Bitter lesson of harnesses: https://browser-use.com/posts/bitter-lesson-agent-harnesses  
