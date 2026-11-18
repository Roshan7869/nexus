# Assistant Local Harness for IDEs (OpenCode-first) — HLD + LLD + Micro Plan

**Date:** 2026-09-14 · **Branch:** second-brain-v2 · **Repo:** /home/roshan/nexus
**Goal:** build a *local-first agentic harness* that any IDE (OpenCode primary, Claude Code/ZCode compatible) can spawn/use with zero cloud dependency for routing, caching, learning — grounded in (a) GitNexus study of this repo, (b) Cordis `arXiv:2608.25512`, (c) Tencent Hunyuan family (Large/TurboS/A13B/Video/DiT/3D/MT-Chimera).
**Companion eval:** `docs/nexus_cordis_tencent_evaluation.md`

---

## 0. GitNexus study — what was actually measured

> Per AGENTS.md: MUST run impact before editing, MUST run detect_changes before commit, MUST warn on HIGH/CRITICAL. This doc is the pre-edit impact surface. No code edited in this round.

- `node .gitnexus/run.cjs status`: repo `/home/roshan/nexus`, branch second-brain-v2, indexed 2026-08-18 (commit 2dfb62d) vs HEAD c8782ad → **STALE**. `meta.json`: 476 files, 8944 nodes, 15688 edges, 380 communities, 300 processes, embeddings 0.
- `node .gitnexus/run.cjs analyze` re-run 2026-09-14: exit 0 but **did not advance** the index — KuzuDB version skew (`Database file version: 43, Current build…`) + `FTS extension unavailable; continuing without FTS`. So all `impact/query/context` reasoning below uses the 08-18 graph + fresh `repo_map` + direct reads. **Do not trust zero-edge counts until re-index succeeds** (restart MCP / rebuild with matching KuzuDB, or `clean --force` + `analyze`).
- `repo_map` (fresh, 4351 defs / 512 files): top hubs by refs — `NumpyRouter.conn/get/search` (2047/2371/352), `IsoEmbedder` (42), `NexusLearner` (44), `NexusHealthOrchestrator` (31), `StateVariable` (45), `CircuitState` (39), `L0-L3Cache.fill/get/set/clear` (137/2371/580/82). **Blast-radius warning: `conn`, `get`, `close`, `load`, `search` are ultra-fan-in (>300 refs) — any signature change = CRITICAL.**
- Skill files consumed: `gitnexus-guide` (tools/resources), `gitnexus-exploring` (query→context→process), `gitnexus-impact-analysis` (d1 WILL BREAK / d2 LIKELY / d3 MAY), `gitnexus-cli` (analyze/status/clean/wiki).

### 0.1 Functionality inventory (verified by reads, not just graph)

| Area | Files | What it does |
|---|---|---|
| Routing v4 | `server/router.py` (1402 lines) | cluster → ISO (`tool_attention_v4`) → `precondition.check` → `hallucination.gate` → Thompson (`core/learner.py`) → keyword fallback. Papers: ToolkenGPT, Reflexion, SWE-agent |
| Routing v5 (prod, `USE_UNIFIED_ROUTER=2`) | `server/router_v2/v5.py` (597) + `fusion.py` | HGMF refiner: L1 intent → short-circuit (conf>θ + pool≥N) → L2 top-2 clusters → L3 convex `0.45·topic+0.25·intent+0.20·skill+0.10·listwise` → L4 autocut 0.5·max + reflexion consult (fail×0.7, fix+0.1·n). Read-only vs MCP/Thompson |
| Learning | `core/learner.py` (`NexusLearner`, `ModelBelief`), `server/learning.py`, `core/config.py:LearningConfig` | Thompson Sampling, `outcome_log`/`routing_log`, `reflection_failures/fixes`, cluster affinity. Flags YAML>env>default. `report_outcome` mandatory |
| Sessions | `server/tool_session_broker.py` (`ToolSessionBroker.start/end_session`), `server/execution_plane.py` (`open_session/report_session`), `server/nexus-mcp-enhanced.py:task_start/task_complete/nexus_session_open/report` | Ephemeral tool sessions (max 8 tools), SQLite-backed, IDE-link via `ide_manager`, belief_delta on report |
| Context harness | `server/context_harness/cache.py` (916, L0-L3), `backend_protocol.py` (`BackendProtocol/Registry`), `content_router.py`, `classifier/scorer.py`, `workflow_graph.py`, `server/harness_mcp_bridge.py` (27 tools merged) | Multi-backend retrieval, per-project bundles, ISO scoring, in-process bridge (no separate process) |
| Resilience | `server/resilience/circuit_breaker.py` (448: `CircuitBreaker`, `Bulkhead`, `_RedisStateBackend`) | Per-tool breakers + per-category semaphores; Redis optional best-effort |
| Rate limiting | `server/integrations/rate_limited_proxy.py` (`MemoryStore` ⊇ `redis.asyncio.Redis` interface) | Memory default, Redis-compatible |
| Topology | `docs/process_topology.md`, `supervisord.conf`, `.mcp.json`, `opencode-with-nexus.sh` | Split ownership: HTTP :8080 systemd, MCP stdio IDE-owned, dashboard :8765 supervisord, harness in-process. STOPPED for MCP/HTTP in supervisor = intentional |
| Config | `nexus.yaml`, `core/config.py`, `.env` (gitignored), `NEXUS_DB_PATH=db/nexus.db` | Providers (ollama_cloud + nvidia_nim), routing flags, model roles |
| DB | `db/migrations/*.sql`, `scripts/init_db.py` | `resources`, `resource_sections`, `resource_edges`, `bundle_membership`, `classification`, `model_beliefs`, `ide_events`, `routing_decisions`, `reflection_*`, `outcome/routing_log`, `cluster_*`, `ingestion_*`, tool-session tables |

### 0.2 Caching — as-built

`server/context_harness/cache.py`: L0 eager core (tier=1 + `core_infra`, 256 entries / 2048 tok) · L1 per-project_type lazy (500 / 8192) · L2 cluster on-demand (400 / 16384) · L3 per-task query-hash (64 / 4096, TTL 300s). Mechanics: TTL+LRU (`OrderedDict`), thread-safe, SQLite persistence (`DB_PATH=~/nexus/db/nexus.db` — note: **home-dir path, not repo `db/`** — compat risk), msgpack optional, token-budgeted fill/evict, `hit_count`/`loaded_at`/`last_used` telemetry. No compression, no quantization, no cross-process sharing.

### 0.3 Redis — as-built (important: almost entirely *planned*, not live)

- `requirements.txt`/`pyproject.toml`: **no `redis` dep** (only `faiss-cpu>=1.8.0`). Redis import in `circuit_breaker.py` is `try/except` → in-memory fallback.
- Live: `_RedisStateBackend` (key_prefix `nexus:circuit`) gated by `NEXUS_CIRCUIT_REDIS=1` + `NEXUS_REDIS_URL/REDIS_URL` (default `redis://localhost:6379/0`, 1s connect timeout, decode_responses). All ops best-effort with warn-and-fallback. `MemoryStore` satisfies the same interface as `redis.asyncio.Redis` for the proxy.
- Planned but **not created**: `server/cache/redis_cache.py` + `nexus/cache/redis_client.py` (`get_redis() -> redis.Redis`), Redis 7 + `aiocache` in compose, centroid/embedding cache with TTL + hit/miss metrics (`designs/nexus-remediation-plan-v1.md`, `docs/nexus_reconciled_plan.json`, ARCH-3 citation report). Dead-code warning: `designs/context-harness-execution-plan-v2.md` documents a Redis-backed `PatternLearner` with hardcoded seeds + dead ORM — do not copy that pattern.
- `opencode_connector.py` source **missing** (only `__pycache__/opencode_connector.cpython-312.pyc` remains) — treat any import of it as broken until re-created (see micro plan M8).

---

## 1. HLD — Assistant Local Harness (OpenCode-first)

### 1.1 Design intent (research-grounded)

- From **Cordis**: every session effect carries its inverse (temporal); every dependency is a declared coeffect with reactive activate/deactivate (spatial); one mediated `NexusContext` for all tool↔env interaction; declarative loader + reconciliation + transactional HMR so the IDE never restarts the harness.
- From **Tencent**: shared-expert prior + recycle routing (Large) for skill routing; adaptive no-think/think gate (TurboS) for L1 short-circuit; fine-grained small-active MoE (A13B) as the latency model; scaling-law-first sizing (Video/DiT); stage-pure + distilled fast path (3D); weak-to-strong candidate fusion (MT-Chimera) for L3 rerank.
- From **NEXUS as-built**: keep split ownership, keep v5 as production ranker, keep Thompson as the learning head, keep harness in-process (no new daemon).

### 1.2 Target topology (local, no new ports)

```
OpenCode (owns stdio pipe)
  └─ nexus-mcp-enhanced.py (per-session spawn, USE_UNIFIED_ROUTER=2)  [MODIFY: register local-harness tools]
       ├─ router_cutover.route_for_task → unified_route_v5 (read-only) [REUSE]
       ├─ LocalHarnessSession (new, Cordis-style ctx.effect + coeffects) [CREATE server/local_harness/session.py]
       ├─ L0-L3 cache (existing) + optional Redis L4 (new, off by default) [MODIFY + CREATE server/cache/redis_cache.py]
       ├─ harness tools in-process (existing bridge) [REUSE]
       └─ HTTP :8080 execution plane (optional; local harness works stdio-only) [REUSE, no change]
```

No new process, no new port, no new DB. SQLite remains SoT; Redis (if ever enabled) is L4 cache + circuit-state share only, never SoT — so local harness runs fully offline with `NEXUS_CIRCUIT_REDIS=0` and no `redis` package.

### 1.3 Compatibility contract (OpenCode + MCP)

- Transport: stdio MCP only (matches `.mcp.json` nexus entry + `opencode-with-nexus.sh` wrapper). Never fight systemd/supervisord for :8080 (see process_topology gate).
- Env passthrough (already in `.mcp.json`, harness inherits): `PYTHONPATH`, `NEXUS_DB_PATH`, `NEXUS_DAEMON_URL`, `USE_UNIFIED_ROUTER=2`, `NEXUS_*` flags, `HOME`. New opt-ins: `NEXUS_LOCAL_HARNESS=1`, `NEXUS_HARNESS_REDIS=0`, `NEXUS_V5_W_*` (fusion overrides).
- IDE adapters: `ide ∈ {opencode, claude-code, zcode, http}` sanitized by `execution_plane._sanitize_ide` — add `opencode` as first-class (currently defaults/clamps unknown IDEs; OpenCode sessions today arrive as `claude-code`).
- Baseline tools invariant: `end_session` returns `tools_removed` + `baseline_tools` — local harness preserves this so IDE surface always restores.

### 1.4 Computational feasibility

- Warm p95 <300ms budget (v5 target) holds: L1 short-circuit avoids L2/L3; L1 embedder distill (A13B lesson) keeps fast path small-active; L3 capped top_k=10 + autocut; FAISS `faiss-cpu` exact-scan limit 10k (GitNexus vectorSearch unavailable — same constraint applies, so keep cluster-constrained retrieval, never full-scan).
- Memory: L0 2k + L1 8k + L2 16k + L3 4k tokens ≈ 30k tokens ceiling per process; Redis L4 (when enabled) target −80% RAM via int8 quant + L1↔L2 dedup (GQA+CLA analog) — local default stays in-memory/SQLite, no extra RAM.
- No GPU, no new backend, no network required for route/learn/report. Research tools (`harness_*`) degrade gracefully offline (already return empty artifacts rather than fail — observed 2026-09-14).

---

## 2. LLD — modules, types, flows

### 2.1 New types (all in `server/local_harness/` package)

```python
# server/local_harness/context.py
@dataclass NexusContext:
  state: dict                  # effect context (what session mutates)
  accumulator: list[Inverse]   # Cordis ∂Γ φ — LIFO disposers, each () -> None
  deps: dict[key, CoeffectSpec] # coeffect context Σ — declared requirements
  realm: str                   # isolate analog: "local:<session>" default, "global:<name>" shared
  intercept: dict[key, Meta]   # policy metadata merged right-biased (context wins)
@dataclass CoeffectSpec:
  keys: set[str]               # e.g. {"db","faiss","embedder"}
  provider_uids: dict[key, str]# resolved at commit (fresh uid per provider revision)
  satisfied: bool
Inverse = Callable[[], None]   # must be idempotent, at-most-once (armed flag)
```

```python
# server/local_harness/session.py
class LocalHarnessSession:
  def effect(self, fn: () -> Inverse | None) -> None  # sole mutation path; registers inverse
  def require(self, *keys: str) -> None               # declare coeffects (spatial)
  def commit_providers(self) -> dict                 # resolve keys → uids, snapshot ω
  async def refresh(self) -> str                      # recompute targets; LOADING/ACTIVE/UNLOADING/INACTIVE
  def dispose(self) -> None                           # run accumulator LIFO, armed/at-most-once, parent-compose
```

Lifecycle states mirror Cordis Alg5: `INACTIVE → LOADING → ACTIVE ⇄ UNLOADING → INACTIVE`, with inertial chaining (complete in-flight transition before starting next) + per-iteration guard. Provider marks `UNLOADING` (out-of-service) *before* its inverses run; dependents teardown while bindings still readable (read-view-not-store via Proxy analog: resolve up session chain, `INACTIVE_ACCESS`/`UNDECLARED_ACCESS` errors).

### 2.2 Flows

**Open (stdio, offline-capable):** `local_harness_open(task, project, budget, ide="opencode")` → `route_for_task` (v5 read-only) → `broker.start_session` (existing, max 8) → wrap in `LocalHarnessSession` (register DB/FAISS/cache inverses, declare coeffects from route cluster) → return `{session_id, tools, skills, context_block, mcp_proxy, instructions}` (same shape as `open_session` payload so IDE code unchanged).

**Report:** `local_harness_report(session_id, outcome, notes, quality, skill_name)` → `broker.end_session` or `execution_plane.report_session` (existing belief_delta) → `LocalHarnessSession.dispose()` LIFO → return deltas. Chaos invariant: 100 sessions with injected failures → zero open handles (Cordis "unload reverts all").

**Reconcile/HMR (no restart):** `local_harness_reconcile(desired: EntryTree)` — entries `{id, kind, config, disabled, isolate, intercept}` keyed by `id`; per-field least-disruptive (id/kind rebuild, isolate realm-move with targeted notify, intercept in-place, config diff, disabled unload/reload-as-fresh). Batch endpoint for OpenCode config sync. Transactional: backup + swap + rollback on error (Cordis Alg8-10 simplified: no ESM graph, just skill rows + FAISS).

**L3 upgrade (Tencent):** shared-prior 5th fusion term + adaptive short-circuit threshold + Chimera K-candidate fusion live behind `NEXUS_V5_W_*` + `NEXUS_ROUTER_SHADOW` compare (existing shadow path) — no new ranker, just weight/policy changes in `fusion.py`/`v5.py`.

---

## 3. Micro plan — file-by-file (MODIFY vs CREATE)

> Ordered by dependency. Each row = one atomic commit with verification. Impact ratings per gitnexus-impact-analysis rubric; HIGH/CRITICAL rows require `impact()` output pasted in PR.

| # | File | Op | Change (exact) | Principle / paper | Verify |
|---|---|---|---|---|---|
| M1 | `server/local_harness/__init__.py` | CREATE | Package exports `NexusContext, CoeffectSpec, LocalHarnessSession, open/report/reconcile` | Cordis §3.3 context paradigm: single mediated type | `python -c "import server.local_harness"` |
| M2 | `server/local_harness/context.py` | CREATE | `NexusContext` + `CoeffectSpec` + `Inverse` + `satisfied()` + `notify()` trichotomy (activating/deactivating/neutral) | Cordis §3.2 reactive coeffects; Tencent intercept monoids | unit: trichotomy table test |
| M3 | `server/local_harness/session.py` | CREATE | `LocalHarnessSession.effect/require/commit_providers/refresh/dispose` + states + armed at-most-once + parent compose (`dispose∘=parent.dispose`) | Cordis §5.1.1 Alg1 + §5.1.3 Alg4/5 | chaos: 100 sessions, 0 leaks |
| M4 | `server/local_harness/loader.py` | CREATE | `EntryTree` + `reconcile()` keyed diff + per-field policy + backup/swap/rollback | Cordis §5.2.1 + Thm80/73 (quiescence = f(final config)) | insert→routable <2s, delete→reverted |
| M5 | `server/nexus-mcp-enhanced.py` | MODIFY | Register `local_harness_open/report/reconcile/status` tools (import lazily, no top-level cycle); thread `ide="opencode"` through | Compat: stdio-only, split ownership | `task_start` + new tools coexist; dashboard unchanged |
| M6 | `server/execution_plane.py` | MODIFY | `_sanitize_ide`: accept `opencode` (add to allowlist, keep clamp for unknown); `open_session` accepts prebuilt `LocalHarnessSession` (optional param, default None → current behavior) | Compat + Cordis §5.1.4 access discipline | existing tests + opencode e2e |
| M7 | `server/tool_session_broker.py` | MODIFY | `start_session`: accept `ctx: NexusContext|None`; when present, route tools through `ctx.effect` registration; `end_session`: run `ctx.dispose()` after `_record_learning`, before `_active.pop` | Cordis temporal Cor69 (departing contributes nothing) | broker tests; leak test |
| M8 | `server/opencode_adapter.py` | CREATE | Re-create missing `opencode_connector.py` as proper module: `get_opencode_config()`, `session_payload()`, `report_mapping()` (success/passed/done→correct etc. — reuse `report_session` normalizer) | Compat: replaces lost pyc-only module | import test; `opencode-with-nexus.sh` smoke |
| M9 | `server/precondition.py` | MODIFY | `StateVariable`: add `subscribe: list[str]` + `realm: str`; `StateGates.check` keeps boolean API (compat) but also emits `notify()` events consumed by `LocalHarnessSession.refresh` | Cordis spatial Thm70 (dependents down before provider withdraws) | kill-FAISS test: auto-deactivate/reactivate, no restart — **HIGH risk, impact first** |
| M10 | `server/router_v2/fusion.py` | MODIFY | Add `w_shared·shared_prior` 5th term; `NEXUS_V5_W_SHARED` override; recycle capacity (`NEXUS_V5_CLUSTER_CAP`) spill to 2nd cluster | Tencent Large: shared expert + recycle routing | `route_ab_compare` + 20-query suite, agreement +3-5pp — **MEDIUM** |
| M11 | `server/router_v2/v5.py` | MODIFY | Adaptive short-circuit (TurboS): replace fixed θ with `should_short_circuit(conf, pool, task_complexity)` policy fn (default = current constants; learned override via env); Chimera: optional K-candidate fuse in L3 behind `NEXUS_V5_CHIMERA=0` | TurboS adaptive CoT + MT-Chimera weak-to-strong | shadow agreement report; p95 unchanged — **HIGH, impact first** |
| M12 | `server/cache/redis_cache.py` | CREATE | `get_redis() -> redis|None`, `cached_skill_lookup`, TTL + hit/miss counters; **import-guarded, never required** | Remediation ARCH-3; Tencent GQA/CLA analog (quantize L1/L2 int8, L1↔L2 dedup) | `NEXUS_HARNESS_REDIS=0` default passes; `=1` without server → fallback warn |
| M13 | `server/context_harness/cache.py` | MODIFY | L4 hooks only: `try: from server.cache.redis_cache import … except: None`; keep L0-L3 semantics identical; fix `DB_PATH` to respect `NEXUS_DB_PATH` (today hardcoded `~/nexus/db/nexus.db`) | Compat bugfix + feasibility (no RAM regression) | cache tests; path-override test |
| M14 | `server/integrations/rate_limited_proxy.py` | MODIFY | None functional — add docstring noting `MemoryStore` is the local-harness default; Redis remains interface-compatible | Docs only | no test change |
| M15 | `server/resilience/circuit_breaker.py` | MODIFY | Thread session realm into `_key()` (`nexus:circuit:<realm>:<name>`) so concurrent OpenCode sessions don't share breaker state; keep default realm = current prefix (compat) | Cordis realms §3.2 (isolate) | breaker tests both realms |
| M16 | `.mcp.json` | MODIFY | Add `"NEXUS_LOCAL_HARNESS": "1"` to nexus env; document `opencode` IDE value in comment | Compat | spawn smoke |
| M17 | `opencode-with-nexus.sh` | MODIFY | Pass `NEXUS_IDE=opencode`; add `--help` probe that prints harness status endpoint | Compat | script smoke |
| M18 | `db/migrations/020_local_harness.sql` | CREATE | `local_harness_sessions(session_id PK, project, task, ide, status, tools_json, created_at, ended_at)` + index on (project, status); **sessions only, no skill data duplication** | SoT stays SQLite; Redis never SoT | `init_db` upgrade + downgrade |
| M19 | `tests/test_local_harness.py` | CREATE | Trichotomy, LIFO dispose, armed at-most-once, realm isolation, reconcile insert/delete, broker integration, leak chaos | Cordis Thm7/70/73/80 acceptance | `pytest tests/test_local_harness.py -v` |
| M20 | `docs/assistant_local_harness_design.md` | CREATE | This file | — | present |

**Explicit non-changes (compatibility guardrails):** no new port/process (topology gate); no `redis` in requirements (optional import only); no v5 default-weight change without A/B; no `conn/get/search/close/load` signature changes (CRITICAL fan-in); no FAISS full-scan (10k exact-scan limit); no `.env`/secrets/`db/nexus.db` commits.

---

## 4. System design principles — where each lands

- **Single mediated context (Cordis):** M1-M3 — all session mutations via `ctx.effect`, all reads via committed view; forbids off-context allocators (DB/FAISS/cache handles bound as coeffects).
- **Temporal locality:** M3+M7 — LIFO accumulator, armed dispose, parent compose; co-locate create/dispose (fixes activate/deactivate split).
- **Spatial locality:** M2+M9+M15 — declare-then-react, provider-uid targets (fresh uid, value-equality can't alias replacement), dependents teardown while bindings readable, realm-partitioned breaker keys.
- **Quiescence as function of config (Cordis Thm80):** M4 — concurrent fetch, no load order, dependents wait at Begin; re-enable = fresh fiber.
- **Shared vs specialized (Tencent Large):** M10 — global prior + cluster experts + capacity spill.
- **Adaptive depth (TurboS):** M11a — cheap no-think short-circuit vs full think pipeline, learned threshold.
- **Fuse-don't-pick (Chimera):** M11b — K cheap candidates + synthesizer rerank behind flag + shadow compare.
- **Compress the hot path (GQA/CLA → cache):** M12/M13 — quantize + dedup, distill L1 embedder (follow-up, not in M-scope).
- **Scale the eval (synthetic flywheel):** reuse `route_ab_compare`/`router_ab_suite` + `report_outcome` → Thompson/cluster-affinity; no new eval harness in this phase.
- **SOLID/least-privilege:** each Mx is one reason to change; intercept metadata is read-time only (no plugin edits for policy).

---

## 5. Verification & rollout

1. **Pre-edit:** `impact({target:"route_task",direction:"upstream"})`, `impact({target:"unified_route_v5",direction:"upstream"})`, `impact({target:"start_session",direction:"upstream"})`, `detect_changes({scope:"compare",base_ref:"main"})` — paste outputs in PR; halt on HIGH/CRITICAL without mitigation.
2. **Phase 0 (M1-M4+M19 skeleton):** pure-new package, zero existing-file risk; merge first.
3. **Phase 1 (M5-M8):** IDE wiring; OpenCode e2e `opencode-with-nexus.sh` + stdio tool list.
4. **Phase 2 (M9-M11):** reactive + routing upgrades behind flags (`NEXUS_V5_*=0` = current behavior); A/B required.
5. **Phase 3 (M12-M15):** cache/compat hardening; Redis remains OFF in CI.
6. **Gates:** `pytest tests/ -q`, `bash scripts/ci_eval.sh`, chaos 100-session leak test, kill-FAISS reactivate test, insert→routable <2s, p95 warm <300ms, agreement ≥ baseline.

---

## 6. Open risks

- GitNexus graph stale + KuzuDB skew — re-index with matching build or `clean --force` before relying on `impact` for M9/M11.
- `DB_PATH` home-vs-repo split (`cache.py` vs `NEXUS_DB_PATH`) — M13 unifies; until then local harness must pass explicit path.
- `opencode_connector` source loss — M8 re-creates; audit any other pyc-only modules (`find . -name "*.pyc" | xargs` vs sources).
- Cordis paper single-version (2026-08-26) + Tencent closed trillion-param model — adopt mechanisms, not proof/performance claims.
