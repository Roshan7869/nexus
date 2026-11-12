# NEXUS HLD — High-Level Design (CORDIS-adapted)

Status: DRAFT v1 · 2026-09-13 · HEAD `c8782ad`
Companions: `01-LLD.md`, `02-PLAN-MACRO.md`, `03-PLAN-MICRO.md`, `04-FEASIBILITY.md`, `05-SITES-REGISTRY.md`
Upstream evidence: `_research/IMPLEMENTATION_PLAN.md`, `_research/graph_design.md`, `_research/dsh.md`

---

## 1. System context

NEXUS is a **resource intelligence layer**: it indexes skills / agents / MCPs / commands / docs /
design-systems and routes a task to the smallest useful subset. It is not an agent runtime; it is what
an agent runtime asks *what do I need for this*.

| fact | value (live, 2026-09-13) |
|---|---|
| resources | 3,255 rows — 1,862 `status='active'` |
| resource_types | skill 1296 · topic 419 · mcp 329 · command 276 · doc 194 · agent 186 · design_system 173 · spec 156 · cli_tool 137 · hook 77 · craft 12 |
| clusters | 8 — knowledge_wiki 618 · frontend_ui 576 · devops_infra 500 · architect 488 · general 435 · analyzer_planner 333 · backend_api 175 · quality_security 130 |
| tiers | 2 → 2,373 · 1 → 843 · 3 → 39 |
| DB | `db/nexus.db`, 538.6 MB (538,640,384 B), 40 tables |
| graph index | `.gitnexus/lbug` — 8,944 nodes / 15,688 edges / 380 communities / 300 processes |
| serving | `:8000` only. `:8080`, `:9999` not listening |

**Structural absences the graph found, unchanged:** `Module = 0`, `Interface = 0`, `ACCESSES = 2`.
NEXUS has no node type for a module boundary, none for an interface, and almost no records of what
touches what. Every CORDIS-adjacent capability lands against that vacuum.

---

## 2. Architectural defect #1 — two parallel fusion systems

This is the single most important fact for the whole design, and it is in the code's own words.

| | old path | new path |
|---|---|---|
| contract | `BackendDef` | `BackendProtocol` (runtime-checkable `Protocol`) |
| registry | `RRFFuser` (`fusion.py:34`; `unregister` at `:44`) | `BackendRegistry` (`backend_protocol.py:35`) |
| fusion | `fuse()` | `fuse_registered()` (`backend_protocol.py:70`) |
| registration | `server.py:918-1188`, 13 backends | no production registrations yet |

`backend_protocol.py:142` states the policy outright:

> *"New code should prefer `fuse_registered()` with a `BackendRegistry`."*

So the migration is **declared and not executed**. Thirteen backends still register the old way
(`scrapling_fetch`, `sherlock_probe`, `scrapecraft_pattern`, `scrapling_spider`, `arxiv`,
`semantic_scholar`, `github`, `paperswithcode`, `google_scholar`, `openalex`, `crossref`, `pubmed`,
`target_site`), and the new registry has zero production users.

**Design consequence:** Track C does not add a registry. It **finishes this migration** and adds the
disposal discipline the old `Fuser` lacks. `BackendRegistry.register()` today is:

```python
def register(self, backend: BackendProtocol) -> None:
    if backend.name in self._backends:
        raise KeyError(f"Backend '{backend.name}' is already registered")
    self._backends[backend.name] = backend
```

No cleanup handle, no reload, no unload. That is exactly the gap CORDIS/DSH closes.

---

## 3. Architectural defect #2 — the served surface is not the documented surface

Live probe of the process on `:8000` (`server/nexus_http_daemon.py`, 32 routes):

```
/health 200   /stats 200   /v1/health 200        <- served
/v1/models 404   /v1/chat/completions 404        <- documented, NOT served
/v1/ops 200 (1.35-2.07s)                         <- served, slow (RE-VERIFIED: not a hang)
```

`/v1/chat/completions` is declared in `server/unified.py:173` (39 routes) **and** `core/server.py:129`
(10 routes). Neither is the running process. Plus `.mcp.json:33` sets
`NEXUS_DAEMON_URL=http://127.0.0.1:8080` — a port that is not listening, on an app that is not the
daemon.

> **Re-verification note (2026-09-13):** `/v1/ops` was originally recorded here as `000 (TIMEOUT)`.
> Five consecutive probes now return **200 in 1.35–2.07 s**. The original `000` was transient (daemon
> mid-restart). See `exec/06-CORRECTIONS.md` P3 — A1-3 is **re-scoped**, not a hang fix.

**Design consequence:** no composition work above a surface whose advertised API 404s. This is Track A
and it precedes everything.

---

## 4. The eight sections

Every integration target below belongs to exactly one section. Section IDs are used verbatim by
`02-PLAN-MACRO.md` and `03-PLAN-MICRO.md`.

### S1 — Resource Catalog & Ingestion
`server/ingest/`, `server/db.py`, `server/catalog_lifecycle.py`, `resources` table (40 tables total).
Owns identity, status, lifecycle, embedding. **Live defect:** `status` and `lifecycle_state` disagree
on 132 rows and `lifecycle_state` is NULL on 2,389 of 3,255.
**CORDIS relevance:** the `Fiber` state machine replaces the dual scalar at the runtime layer; the DB
`CHECK` constraint replaces it at the storage layer. Both, not either.

### S2 — Router
`server/router_v2/` (`v5.py`, `scoring.py`), `cluster_router.py`, `numpy_router.py`,
`cross_type_router.py`, `router_cutover.py`.
Owns task→resource selection. **Live defect:** `_short_circuit` (v5.py:237-268) bypasses the cold-tier
reservation that `_v5_rerank` performs.
**CORDIS relevance:** the interception point — `G_INTERCEPT` / waterfall dispatch. The `ACCESSES = 2`
gap means the router is nearly the only edge the graph can see.

### S3 — Context Harness Engine
`server/context_harness/` — `backend_protocol.py`, `fusion.py`, `content_router.py`, `compression.py`,
`engine.py`, `channels/` (15 files), `rate_limited_http.py`, `cache.py`, `toin.py`, `workflow_graph.py`.
Owns retrieval backends and window packing. **Contains defect #1.**
**CORDIS relevance:** primary graft site — backends are the resources that must record their cleanup.

### S4 — Harness Engine & Toolsets
`server/harness_engine/` — `plan.py`, `orchestrator.py`, `toolsets.py`, `attach.py`, `models.py`;
`server/harness_metrics.py`, `server/harness_mcp_bridge.py`.
`toolsets.py` is a hardcoded static partition (`CORE_TOOLS` / `RESEARCH_TOOLS` / `BROWSER_TOOLS` /
`MEMORY_TOOLS`).
**CORDIS relevance:** the toolset partition is the natural `Registry`; the `attach` step is a mount.

### S5 — MCP Surface
`server/nexus-mcp-enhanced.py`, `server/mcp_sync.py`, `server/ephemeral_mcp_proxy.py`,
`.mcp.json` (3 servers: gitnexus, claude-flow/ruflo, nexus).
Owns the 116-tool external contract. **Live defect:** `NEXUS_DAEMON_URL` → dead `:8080`.
**CORDIS relevance:** every MCP tool is a registration that must dispose. `mcp_sync_log` is the audit.

### S6 — Execution / Agent Plane
`server/execution_plane.py`, `server/forge.py`, `server/orchestrator.py`, `server/plan_tracker.py`,
`server/broker_runtime.py`, `server/nexus_plan_executor.py`, `server/ephemeral_mcp_proxy.py`.
**Live defect:** `autonomous_pipeline.py` (**396 L**, re-measured) is dead — two prose references only.
**CORDIS relevance:** the live multi-step flow (`harness_study_project → _harvest_all → harvest_site →
fetch_page`) is the first disposer adopter, and `G_MODULE` lives here and is **not** closable by
CORDIS.

### S7 — Learning & Feedback
`server/learning.py`, `feedback.py`, `implicit_outcomes.py`, `efc.py`, `reflection*.py`, `coach/`,
`rl/`, `server/learning_faithful.py`.
**Live defect:** learner enabled/disabled state leaks across tests (2 failures in the full suite, both
the learner-disabled path).
**CORDIS relevance:** `G_STATE` — the process-wide singleton `StateGates` (`precondition.py:65-114`)
is a `Fiber` in all but name.

### S8 — Ops & Observability
`server/ops_metrics.py`, `harness_metrics.py`, `process_supervisor.py`, `dashboard/`,
`logs/ops_eval_latest.json`.
Owns `routing_coverage` (6,670 routes; `routed = 13`), gate pass/fail, supervisor tree.

---

## 5. CORDIS adaptation at architecture level

CORDIS is not a framework to install. `_research/dsh.md:259` reduces the kernel to one constraint:

> **every resource a plugin registers while mounting must simultaneously record a cleanup function;
> unload runs those functions in reverse.**

That is `contextlib.ExitStack`. Four primitives, mapped:

| CORDIS | NEXUS attachment point | Real implementation |
|---|---|---|
| **Disposer** (`Γ`) | every backend in S3, every MCP tool in S5 | `ExitStack` + a `Handle` with a `_closed` guard |
| **Registry** | `BackendRegistry` (S3, defect #1), `toolsets.py` (S4) | complete the declared migration; add handles |
| **Waterfall** | `v5.py` post-filters (S2), context assembly (S3) | 5 dispatch modes; a listener that skips `next()` vetoes |
| **Fiber** (`π,θ,Γ`) | `StateGates` (S7), resource lifecycle (S1) | `(parent, state, effects)`; θ ∈ Inactive/Reloading/Active/Unloading |

**Explicitly out of architecture scope:**
- CORDIS proof obligations (Thms 68–80, `C_CONFLUENCE`, `C_PROGRESS`) — DSH ships without them.
- `G_MODULE` — Fiber trees compose *runtime* resources and cannot impose *static* module structure.
  Fix with `import-linter` or a package restructure. **Do not build a DI container over boundaries
  that already exist and are already being violated** (268/300 traced flows cross a community edge).
- In-flight swap semantics — `dsh.md` §4 marks this **PARTIALLY SPECIFIED**. Decide it explicitly.

---

## 6. The decision gate

```
Track A (Fundament)  ──  correct regardless of architecture choice  ──  DO FIRST
        │
        └── DECISION: is NEXUS becoming a plugin host?
                │                       │
               NO                      YES
        ~30 LOC disposer          Track C: C0→C1→C2→C3→C4
        + delete 15,851 LOC       ~250-400 LOC, replaces hardcoded pipeline
```

`plan_review.md` is right that this is the load-bearing question. A composition layer built *after*
plugins exist costs several times more than one built before.

---

## 7. Deployment topology (target)

```
:8000  ONE FastAPI process
       ├── ops / IDE / cluster surface   (32 routes, live today)
       └── OpenAI-compat surface         (mount unified.py's routes, don't merge files)
:8080  DEAD — remove from .mcp.json, or bring up deliberately
graph  .gitnexus/lbug — 8,944 nodes; LadybugDB, not queryable from this shell (no client installed)
db     db/nexus.db 538.6 MB — single writer, read-only connections via cache.get_ro_conn()
```

---

## 8. Non-goals

- No distributed coordination. `C_AFFECT`, `C_FIBER` proofs, `C_ORDER` are scoped to a multi-process
  theory NEXUS does not implement.
- No framework. The whole disposer is smaller than its own test file.
- No static module enforcement via runtime machinery (`G_MODULE`).
- No rewrite of `fusion.py`. The migration is declared in `backend_protocol.py:142`; execute it.
  (The class to migrate **off** is `RRFFuser`, not `Fuser` — see `exec/06-CORRECTIONS.md` P1.)
