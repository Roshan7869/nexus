# NEXUS Phase Plan

This is the canonical roadmap for the NEXUS resource manager. It defines all phases from data
hygiene (Phase 1) through production-level resilience (Phase 7+). Each phase has clear deliverables,
entry/exit criteria, and estimated effort. The plan prioritizes **TUI IDE integration** over LLM
proxying per user directive.

---

## Phase 1 — Data Foundation & Core Routing ✅ COMPLETE

**Dates:** May 11–16 2026

**Deliverables:**
- Data hygiene: deduplication, inactive cleanup, malformed repair
- Tier classification: T1/T2/T3/T4 with resource limit rules (8/20/40/80)
- Sentence-transformer embeddings for all 1,686 resources
- FAISS flat index (384-dim) for fast nearest-neighbor search
- State preconditions: `check_preconditions()` with resource availability gates
- Universal routing: `route_v4()` with Tool Attention v3 pipeline
- Domain clustering: 8 clusters, `cluster_route()`, `cluster_activate()`

**Entry criteria:** Raw resource dump existed (Phase 0)
**Exit criteria:** 25/25 tests passing, all endpoints working under 100ms (cached)

**Status:** COMPLETE — 25/25 PASS, 1,686 resources, 8 clusters, 168 routing logs.

---

## Phase 2 — Persistence & Integration Layer ✅ COMPLETE

**Dates:** May 17 2026

**Deliverables:**
- **2a: Persistent HTTP daemon** — FastAPI + uvicorn server on `:8080`
  - Caches SentenceTransformer model in RAM (14s → sub-100ms cold-start elimination)
  - All Phase 1 endpoints exposed as OpenAPI schema
  - Health, stats, cluster_list, route_v4, cluster_route, report_outcome, learning_stats
- **2b: MCP v2 bridge** — `nexus-mcp-v2.py` wiring tools through HTTP daemon
  - 19 MCP tools (v4 cluster + v3 routing + health + stats)
  - Environment variable `NEXUS_DAEMON_URL` points to `:8080`
  - Hermes config updated to use new MCP server
- **2c: systemd user service** — `nexus-daemon.service`
  - Restart=always, auto-start on boot (`systemctl --user enable`)
  - Wrapper script `/home/roshan/nexus/scripts/start-daemon.sh` handles venv activation

**Bug fixes applied in this phase:**
- Added missing `ClusterFAISS.get_cluster_stats()` (called by daemon but didn't exist)
- Fixed `report_outcome` CHECK constraint (`ide='nexus-daemon'` → `'unknown'`)

**Entry criteria:** Phase 1 complete
**Exit criteria:** Daemon responds to `/health`, systemd shows `active (running)`, MCP tools
available in Hermes.

**Status:** COMPLETE — daemon active (pid 227218), enabled on boot.

---

## Phase 3 — TUI IDE Integration (Next)

**Priority:** HIGH (user explicitly: "stick to resource management system instead of llm routing")

**Deliverables:**
- **`nexus_hierarchical_retrieve`** — browse all 8 clusters as a tree
  - Root: "NEXUS Skills"
  - Branches: 8 domain clusters (analyzer_planner, architect, frontend_ui, etc.)
  - Leaves: individual resources with tool name, description, match score
  - Support pagination and filter by setup_type / market_condition
- **`nexus_search`** — free-form semantic search within the full toolset
  - Query routed through `route_v4` or direct FAISS search
  - Returns ranked list with explanation of why each resource matched
- **CLAUDE.md integration** — auto-suggest skills when Hermes detects task signals
  - On user message, scan for trigger keywords (analyze, ship, deploy, test, etc.)
  - If match, append top 3 matching skills to system prompt context
  - Flag: `nexus_auto_context: true` in Hermes config
- **Resource preview** — for each skill, show key fields before invocation
  - Entry condition, stop loss, target, psychology note (for strategy-kb style skills)
  - Display as terminal-formatted table (not markdown)

**Entry criteria:** Phase 2 daemon stable
**Exit criteria:** TUI IDE can browse, search, and preview all NEXUS resources without
HTTP cold-start.

**Estimated effort:** 2–3 sessions

---

## Phase 4 — Performance Hardening

**Priority:** HIGH

**Deliverables:**
- **Embedding server-side caching** — warm FAISS index in memory, lazy-load only on change
  - Currently FAISS loads from disk on every search (acceptable for ~500ms but improvable)
  - Pre-build `faiss.serialize_index()` → `SharedMemory` or mmap
- **Predictive preloading** — auto-activate clusters based on session context
  - If last 3 messages mention "deploy" or "Docker", preload devops_infra cluster
  - Reduces first-hit latency from 7s to 100ms even on fresh conversation
- **Redis-backed result cache** — LRU cache for repeated queries
  - Key: `sha256(query + top_k)`, TTL: 5 minutes
  - Eliminates redundant FAISS/embedding computations
- **Request batching** — `batch_route()` for multi-skill lookups
  - Useful when TUI IDE wants to display top skills across multiple clusters simultaneously

**Entry criteria:** Phase 3 TUI integration working
**Exit criteria:** `p99 < 50ms` for cached queries, `p99 < 2s` for cold cluster activation.

**Estimated effort:** 1–2 sessions

---

## Phase 5 — Testing & Quality

**Priority:** MEDIUM

**Deliverables:**
- **Unit tests for daemon** — FastAPI test client (TestClient) for all endpoints
  - `/health`, `/stats`, `/cluster_list`, `/route_v4`, `/cluster_route`
  - Mock embedding model to avoid 14s load in CI
- **Integration tests for MCP bridge** — full stdio-to-HTTP roundtrip
  - `tools/list`, `tools/call` for `nexus_auto_route`, `nexus_cluster_route`
  - Assert < 200ms latency from MCP stdin to HTTP response
- **Load tests** — simulate 100 concurrent queries via `locust` or `wrk`
  - Identify lock contention in `ClusterFAISS` SQLite reads
  - Ensure systemd restart threshold handles crash loops gracefully
- **Property-based tests** — fuzz queries to catch edge cases
  - Empty query, unicode, 10k-char payloads, SQL injection attempts

**Entry criteria:** Phase 4 performance baseline established
**Exit criteria:** 90% test coverage, CI green, no regressions in query quality.

**Estimated effort:** 1–2 sessions

---

## Phase 6 — Documentation & Audit

**Priority:** MEDIUM

**Deliverables:**
- **README.md overhaul** — target: new contributor onboard in < 15 minutes
  - Architecture diagram (dark SVG per user's existing `architecture-diagram` skill)
  - API reference with `curl` examples
  - MCP setup guide for Hermes users
- **INTERNAL.md** — data pipeline documentation
  - How resources are ingested, embedded, clustered, and tiered
  - How learning feedback loops work (outcome → win rate → strategy ranking)
- **Audit report** — final validation of all 1,686 resources
  - Orphaned resources (no edges in graph)
  - Stale resources (last updated > 30 days)
  - Coverage gaps (clusters below minimum resource threshold)
- **Changelog** — from Phase 0 (raw dump) to Phase 6

**Entry criteria:** Phase 5 test suite stable
**Exit criteria:** Docs reviewed and accurate, no broken links.

**Estimated effort:** 1 session

---

## Phase 7 — Extension & Production Hardening (Future)

**Priority:** LOW (user wants resource manager first)

**Deliverables:**
- **Multi-DB backend** — support PostgreSQL for replica/HA setups
  - SQLite sufficient for single-node, but PG enables horizontal scale
- **Metrics & observability** — Prometheus `/metrics` endpoint
  - Request latency histograms, cluster activation frequency, cache hit rate
- **Admin dashboard** — web UI for browsing clusters, manually reclassifying resources
  - Read-only at first; write access behind auth
- **Plugin API** — let external tools register as NEXUS resources via HTTP POST
  - e.g. a new trading strategy from a Jupyter notebook can self-register
- **Backup & migration** — `nexus_backup` and `nexus_restore` CLI commands
  - Full JSON dump of DB + FAISS index + embeddings

**Entry criteria:** Phase 6 complete, user requests scale
**Exit criteria:** TBD per user priorities.

**Estimated effort:** 3–5 sessions (incremental)

---

## Quick Reference

| Phase | Status | Key File | Primary Goal |
|---|---|---|---|
| 1 | ✅ Done | `server/nexus_router.py` | Data + routing |
| 2 | ✅ Done | `server/nexus_http_daemon.py` | Persistence |
| 3 | **Next** | `server/nexus_tui.py` (TBD) | TUI IDE integration |
| 4 | Pending | `server/cache.py` (TBD) | Performance |
| 5 | Pending | `tests/` expansion | Quality |
| 6 | Pending | `README.md`, `INTERNAL.md` | Docs |
| 7 | Future | `server/admin.py` (TBD) | Scale + extend |

**Current daemon:** `http://127.0.0.1:8080` — systemd active, embedding cached.

**Start command (if needed):**
```bash
systemctl --user restart nexus-daemon.service
```

**MCP tool count:** 19 (via `nexus-mcp-v2.py`)

**Config:** `~/.hermes/config.yaml` → `mcp_servers.nexus` → `nexus-mcp-v2.py`

---

*Plan created: 2026-05-17. Next action: decide whether to proceed with Phase 3.*