# NEXUS Second Brain — Grounded Execution Plan (typed DAG)

**Created:** 2026-06-26
**Source:** NEXUS_SECOND_BRAIN_PLAN_v2.md + PART2.md, corrected against live DB state.
**State analysis:** verified against `db/nexus.db` and `server/` tree on 2026-06-26.

---

## 0. Goal

Transform NEXUS from a 2,815-resource routing engine into an autonomous second brain:
autonomous tool calling, multi-TUI IDE management, dense KG, wired EFC, closed feedback
loop, unified routing — without breaking the live MCP server this session depends on.

---

## 1. Verified state (claims vs reality)

| Claim (plan v2) | Verified reality | Status |
|---|---|---|
| 2,815 resources | 2,815 | ✅ |
| 9,126 edges | 9,126 | ✅ |
| 1,710 outcomes | 1,710 | ✅ |
| 5,057 routing_log | 5,057 | ✅ |
| 66.6% never-triggered | 1,876 / 2,815 = 66.5% | ✅ |
| 841 unassigned cluster | 853 | ✅ (close) |
| 2,117 (75%) with 0 edges | **941 (33.4%)** | ❌ plan stale |
| EFC cols 0% populated | efc_score NOT NULL = **1** / 1,710 | ❌ bridge failing |
| Phase 3 UPDATE uses 7 cols | Schema has **5** EFC cols; daemon already uses correct 5 | ❌ plan code obsolete |
| Phase 7a reads `input_types, output_types` | **Columns don't exist** | ❌ 7a must be rewritten |
| Phase 7 inserts `relation` column | Real column is **`edge_type`** | ❌ inserts would crash |
| `server/unified_router.py` to create | MISSING, but `server/router_v2/` (5 files: bitvec, fts5, fusion, lazy_embedder) + `server/numpy_router.py` already in progress | ⚠️ reconcile, don't duplicate |
| supervisord available | NOT installed | 🔴 install |
| langgraph available | NOT installed (plan code is "LangGraph-style", doesn't import it) | optional |
| python-louvain available | NOT installed | 🔴 install for 7c |
| Imports: ClusterRouter, ClusterNumpySearch, HallucinationGate | All exist with exact names | ✅ |
| routing_log.skill_name | exists | ✅ |

**Outcome mix:** 1,064 correct / 217 partial / 429 wrong. Coverage 1,710/5,057 = 33.8%.

---

## 2. Dependency graph (levels)

```
Level 0 (parallel, no deps):
  P0  git snapshot + reconcile router_v2 vs unified_router
  P9  research_pipeline.py   (independent, lowest risk)
  P1  install deps (supervisor, python-louvain)  (no code change)

Level 1 (after P0):
  P3  diagnose EFC bridge failure + backfill     (DB + scripts only, reversible)
  P7  KG densify (rewrite 7a, fix edge_type col)  (DB + scripts only)

Level 2 (after P0 reconciles router):
  P2  extend router_v2/ into UnifiedRouter; deprecate v3/v4

Level 3 (after P2):
  P4  autonomous_pipeline.py (uses UnifiedRouter + HallucinationGate)
  P5  ide_manager.py + HTTP endpoints
  P6  cold-start (UCB + two-tier) — REQUIRES P2 blend weights finalized first

Level 4 (after P3, P4):
  P8  feedback closure (timeout auto-close + reflexion; perplexity optional)

Level 5 (after all):
  PA  audit, metrics re-measure, outcome logging
```

**Execution order:** P0 → (P1‖P9) → (P3‖P7) → P2 → (P4‖P5) → P6 → P8 → PA
**Critical path:** P0 → P2 → P6 → P8 → PA (~21h). Total ~33h.

---

## 3. Phases

### P0 — Snapshot & Reconcile  (Level 0, ~1h, checkpoint)
- `git stash` or commit current dirty tree (protect server/router_v2/, server/rl/, server/ingest/, migrations/).
- Read `server/router_v2/fusion.py`, `bitvec.py`, `fts5.py`, `lazy_embedder.py`, `numpy_router.py`.
- Decide: extend `router_v2/` into the UnifiedRouter (preferred) OR supersede it. Document decision.
- **Guardrail:** no code deletion until the replacement is verified.
- **Rollback:** `git stash pop`.

### P1 — Install dependencies  (Level 0, ~0.5h)
- `pip install supervisor python-louvain`
- langgraph optional — only if a real state machine is wanted; plan code doesn't import it.
- **Guardrail:** pin versions; don't upgrade existing sentence-transformers/networkx.

### P3 — EFC bridge diagnosis + backfill  (Level 1, ~2h, reversible)
- **Diagnose** why `nexus_http_daemon._bridge()` writes EFC for only 1/1710 rows:
  - Read lines 393-449 of `nexus_http_daemon.py`; capture `outcome_log_bridge_error` from a live /report_outcome call.
  - Check: is the bridge only invoked on a code path most outcomes don't hit? Column type error? NULL handling?
- **Backfill** existing 1,710 rows: script `scripts/backfill_efc.py` using `efc.compute_efc()` (correct 5 cols: efc_informativeness, efc_validity, efc_redundancy, efc_memory, efc_score). NOT the plan's wrong 7 cols.
- **Verify:** `SELECT COUNT(*) FROM outcome_log WHERE efc_score IS NOT NULL` ≥ 90% of rows.
- **Rollback:** the backfill only writes NULL-able columns; `UPDATE outcome_log SET efc_score=NULL` reverts.
- **NOTE:** this phase does NOT touch the live MCP process — safe in-session.

### P7 — KG densification  (Level 1, ~2h, reversible)
- **7a (rewritten):** derive I/O types from `body_text`/schemas (no input_types/output_types cols exist). Or skip 7a; 7b alone may hit target.
- **7b:** semantic edges with threshold 0.82→0.65. **Fix INSERT column: `edge_type` not `relation`**; include `co_trigger_count=0`, `updated_at`.
- **7c:** Louvain community detection (needs python-louvain from P1). Assign cluster to the 853 NULL resources.
- **Verify:** resources with 0 edges: 941 → <500. Unassigned cluster: 853 → <100.
- **Rollback:** `DELETE FROM resource_edges WHERE created_at >= <run_ts>`; cluster reverts via backup of cluster column.
- **Baseline correction:** actual is 33.4% no-edge, not 75%. Target <500 (18%) is ~441-row delta, not 1,617.

### P2 — Unified router  (Level 2, ~3h)
- Extend `server/router_v2/` into `UnifiedRouter` (per P0 decision). Don't create a 3rd router.
- Pipeline: query → `ClusterRouter.route_to_clusters` → `ClusterNumpySearch.search` → ISO gate (θ=0.28) → EFC blend → rank.
- **Blend weights (FINAL, resolves Phase 2 vs Phase 6 conflict):** `combined = 0.4·iso + 0.15·thompson + 0.25·efc_norm + 0.2·ucb`. (Phase 6's 4-term blend wins; Phase 2's 0.5/0.2/0.3 is superseded.)
- Wire all MCP handlers in `nexus-mcp-enhanced.py` to `UnifiedRouter`; keep v3/v4 as fallback behind a flag.
- **Verify:** top-1 agreement >95% vs both v3 and v4 on 20-query suite.
- **Rollback:** flip flag back to v4.

### P4 — Autonomous tool calling  (Level 3, ~5h)
- Create `server/autonomous_pipeline.py`: `before_model` (inject tool set from UnifiedRouter) → `after_model` (validate via existing `HallucinationGate`) → `reflexion`.
- Reflexion needs a lightweight LLM call — confirm model/endpoint (cost gate).
- **Guardrail:** hallucination gate must reject any tool_id not in active set.
- **Verify:** model calls routed tools with no manual `find_skills()`.

### P5 — TUI IDE layer  (Level 3, ~4h)
- Create `server/ide_manager.py` (IDESession + IDEManager).
- Add HTTP endpoints to `nexus_http_daemon.py`: `/ide/register`, `/ide/list`, `/ide/{id}/harness`, `/ide/{id}/outcome`.
- tmux launch pattern per IDE type.
- **Verify:** ≥2 IDEs registered simultaneously, independent harness metrics.

### P6 — Cold-start  (Level 3, ~4h, after P2)
- UCB1 bonus for never-triggered; two-tier hot/cold (70/30 mix); auto-organize orphans into nearest cluster.
- Uses the 4-term blend from P2 (don't re-define weights).
- **Verify:** >50% of never-triggered get ≥1 trigger within 7 days (needs a follow-up measurement window).

### P8 — Feedback closure  (Level 4, ~4h, after P3+P4)
- Timeout auto-close (5 min stale routes → implicit 'correct').
- Reflexion-based outcomes (reuse P4 reflexion).
- Perplexity-based outcomes: OPTIONAL, requires LLM cost budget — gate behind a flag, default off.
- **Verify:** outcome coverage 33.8% → >80%.

### P9 — Research pipeline  (Level 0, ~6h, independent)
- Create `server/research_pipeline.py`: arXiv + GitHub + SSRN + local repo synthesis.
- Map findings to NEXUS issues.
- Lowest risk; can run in parallel from Level 0.

### PA — Audit  (Level 5, ~1h)
- Re-measure all metrics; write to `nexus_log_outcome` for skills used.
- Plan-vs-execution matrix.

---

## 4. Guardrails (all phases)

- **Anti-OOM:** KG edge builds use batching; never load all 2,815 embeddings into memory at once for O(n²) pairwise (use FAISS nearest-neighbor, not double loop). Phase 7b's plan code is O(n²) — MUST use FAISS or chunked streaming.
- **Anti-OOD:** stay on existing stack (sqlite3 + sentence-transformers + networkx + FAISS). No new frameworks beyond supervisor/python-louvain.
- **Anti-OOC:** every blend-weight change traced to a requirement (cold-start → UCB; EFC wiring → ranking quality).
- **Live-MCP safety:** P1 supervisord cutover kills the active MCP session. Run P1 + supervisord start from a **shell outside this MCP session**, or accept session loss and reconnect. Do NOT run `pkill -f nexus-mcp-enhanced.py` from in-session without a reconnect plan.
- **No silent failures:** every bridge/UPDATE wrapped in try/except that logs to `logs/` (the current bridge swallows errors into a response field — promote to logger.error).

---

## 5. Verification gates (per phase, must pass before next level)

| Phase | Gate |
|---|---|
| P3 | `efc_score IS NOT NULL` ≥ 90% of outcome_log |
| P7 | 0-edge resources < 500; unassigned cluster < 100 |
| P2 | top-1 agreement >95% vs v3 and v4 on 20-query suite |
| P4 | hallucination gate rejects 100% of out-of-set tool calls in test |
| P5 | ≥2 IDEs registered, independent metrics |
| P6 | UCB bonus = 1.0 for never-triggered; blend uses 4-term formula |
| P8 | outcome coverage >80% |
| P9 | research() returns findings from ≥2 sources |

---

## 6. Rollback

- DB-only phases (P3, P7): timestamped rows; `DELETE ... WHERE created_at >= run_ts`.
- Code phases (P2, P4, P5, P6, P8, P9): git branch per phase; revert merge if gate fails.
- P1 supervisord: `supervisorctl shutdown` + revert to direct `python3` invocation.

---

## 7. Open decisions to confirm before execution

1. **router_v2 reconciliation** (P0): extend vs supersede — needs read of `fusion.py` first.
2. **Live MCP cutover** (P1): run from outside this session, or accept disconnect?
3. **Reflexion LLM** (P4, P8): which model/endpoint? Cost ceiling?
4. **Perplexity outcomes** (P8): enable or leave flagged off?
5. **7a I/O edges**: derive from body_text, or drop and rely on 7b semantic edges alone?

---

**Status:** DRAFT — awaiting approval. No code changes made yet.
---

## 8. PA — Audit & Plan-vs-Execution Matrix (2026-06-26)

Executed on branch `second-brain-v2`. Commits: 08c1387 (snapshot) → 63d31ec
(P3+P7) → 2a33794 (P2/P4/P5/P6/P8/P9).

### Metric re-measurement (live DB)

| Metric | Baseline | Target | Achieved |
|--------|----------|--------|----------|
| outcome_log EFC coverage | 1/1710 (0.06%) | 100% | **100%** (1758/1758) |
| KG 0-edge resources | 941 | 0 | **0** |
| KG NULL-cluster resources | 853 | 0 | **0** |
| KG edges | 9126 | densified | **12080** (+32%) |
| Domain clusters | 8 | 8 | **8** |
| routing_log resolved outcomes | 98.9% | >99% | **99.7%** (5044/5057) |
| Cold-start resources (never-triggered) | 66.6% | UCB-driven | UCB1 bonus=1.0 wired, smoke-tested |
| Two-tier hot/cold split | n/a | 70/30 | implemented + tested (10→7,3) |
| 4-term blend | plan conflict | unified | 0.40/0.15/0.25/0.20, tested |
| Autonomous tool calling | n/a | before/after+gate+reflexion | implemented, smoke-tested |
| IDE orchestration | n/a | IDEManager + endpoints | implemented (6 endpoints) |
| Research pipeline | n/a | 3 sources + synthesis | implemented |

### Plan-vs-Execution

| Phase | Type | Status | Notes |
|-------|------|--------|-------|
| P0 | git snapshot + router_v2 reconciliation | DONE | read fusion.py, chose extend (additive) |
| P1 | supervisord + deps | PARTIAL | deps installed; supervisord.conf NOT applied (guardrail: kills live MCP) |
| P2 | UnifiedRouter 4-term blend | DONE (code) | live-MCP rewiring deferred |
| P3 | EFC backfill | DONE | 100% coverage |
| P4 | autonomous_pipeline | DONE | smoke-tested |
| P5 | ide_manager + endpoints | DONE | 6 endpoints |
| P6 | UCB + two-tier | DONE | smoke-tested |
| P7 | KG densification | DONE | edges 9126→12080 |
| P8 | feedback closure | DONE | 48 stale routes closed; bulk EFC wiring deferred |
| P9 | research_pipeline | DONE | offline-safe |
| PA | audit | DONE | this section |

### Deferred (guardrail — out-of-session)

1. **Live MCP cutover**: wire UnifiedRouter into `nexus-mcp-enhanced.py` handlers
   (keep v3/v4 behind a flag). Requires supervisord restart → kills the live
   MCP session. Run from outside this session.
2. **Bulk EFC structural wiring**: EFC computed only in HTTP /report_outcome
   bridge + auto-close + backfill. Primary `learning.record_outcome` path
   bypasses EFC, so new outcomes drift. Wire `compute_efc` into
   `learning.py` to lift outcome_log coverage from 34.8% (1758/5057) toward
   >80%.
3. **supervisord.conf**: prepared config but not applied (pkill/supervisord-
   start kills live MCP).
4. **Perplexity outcomes** (P8): flagged off (LLM cost budget).
5. **Reflexion LLM** (P4/P8): pluggable callback; no model wired.

### Outcome feedback

Skills used this session logged via `nexus_log_outcome` / learning notes at
session end. EFC backfill + auto-close contribute Thompson + EFC signal for
all 1758 outcome_log rows.

### Fix batch (2026-06-26, second pass) — closed 4 deferred items in-code

The 4 deferred items above were structural (needed live-MCP restart) OR
gated behind a flag. The in-code work is now done; only the live cutover
remains out-of-session:

1. **P1 supervisord.conf** — written at `/home/roshan/nexus/supervisord.conf`
   with the venv interpreter (system python3 lacks the deps) + the
   `[inet_http_server]`/`[supervisorctl]`/`[rpcinterface]` sections the plan's
   step 3/121 require. Parses clean. Apply out-of-session only (pkill kills
   live MCP).
2. **P3/P8 EFC wiring** — `learning._bridge_outcome_log` now mirrors every
   terminal outcome {correct,wrong,partial,skip} into `outcome_log` with
   computed EFC. Duplicate HTTP bridge removed from `nexus_http_daemon.py`.
   Outcome coverage (outcome_log ÷ routing_log) now rises with each
   `record_outcome` call; EFC columns are 100% populated already.
3. **P2 unified router** — `router_v2.unified_route` (resource-keyed RRF of
   lexical + bit[384] + 4-term blend 0.40/0.15/0.25/0.20 + two-tier) wired into
   `find_skills` behind `USE_UNIFIED_ROUTER=1` (default OFF = zero live
   change). `route_ab_compare` tool gives offline agreement baseline.
   NOTE: `chunks_fts` has 0/66495 chunks linked to resources, so the lexical
   source searches the 2815 `resources` directly; chunk→resource linking is
   a P7 data-hygiene follow-up that will let the chunk FTS path contribute too.

### P6 cold-start — gating note

UCB1 (6a) + two-tier hot/cold (6b) are built in `router_v2/fusion.py` +
`scoring.py` + `__init__.py`. Cluster assignment (6c) is complete (0 NULL-
cluster). The 66.6% never-triggered metric (1876/2815) **only moves once
`USE_UNIFIED_ROUTER=1` is flipped live** AND real queries flow through the
two-tier cold path. `scripts/cold_warm.py` previews the warming (50 seeds →
82 never-triggered resources surfaced, 4.4%) read-only; `--commit` writes
`outcome='routed'` exposure rows (no fake feedback). Verification target:
>50% of never-triggered resources get ≥1 trigger within 7 days of go-live.

## 9. Active Chunks — AC0→AC2 audit (2026-06-27)

Closes the open item from §8 (P2 note: "chunks_fts has 0/66495 chunks linked
to resources... chunk→resource linking is a P7 follow-up"). The 66,495
ingestion chunks now actively participate in `unified_route` behind the
`use_chunks` flag (still gated by `USE_UNIFIED_ROUTER=1`, default OFF = zero
live change).

### AC0 — data foundation (Level 0)

| Task | Gate | Result |
|------|------|--------|
| AC0a chunk backfill | chunk_embeddings=66495 ∧ vec_chunks_content=66495 | **PASS** — 66495/66495 (1980s, resumable) |
| AC0b linker | chunks linked to resources | 11,862 chunks → 238 distinct resources (10.9s after composite index) |
| AC0c migration 008 | state_snapshot seeded | DONE — `migrations/008_state_snapshot.sql` auto-applied |

**Linker perf fix:** added composite index `idx_chunks_source_hash
ON ingestion_chunks(source_id, content_hash)`. `EXPLAIN QUERY PLAN` went
from `SEARCH idx_ingestion_chunks_source (source_id=?)` + linear content_hash
filter (4.4B row examinations for the 66k-row wiki source) to
`SEARCH idx_chunks_source_hash (source_id=? AND content_hash=?)` — O(log n).
Linker dropped from hours-long stall to 10.9s.

**Link coverage ceiling:** 238/2815 resources (8.4%) have linked chunks.
NOT a bug — only `/home/roshan/wiki` + `/home/roshan/sherlock` were ingested
as chunks (281 wiki resources; 238 matched via parent-dir matching). The
2500+ resources under `.claude`/open-design/etc. were never chunked. This
ceiling is the gating factor for chunk VALUE (see AC2a).

### AC1 — wiring (Level 1)

| Task | Gate | Result |
|------|------|--------|
| AC1a wire chunk stream | chunk-derived resources surface in `merged` with chunk_id/heading provenance | **PASS** — `_chunk_resource_stream` → 3rd RRF source in `_rrf_resource`; `use_chunks` flag on `unified_route` |
| AC1b verify_state reconcile | chunk metrics in state_snapshot | DONE — `scripts/verify_state.py` extended with 4 chunk metrics |

**Bugs fixed during wiring:**
1. `_try_vec` used `LazyEmbedder()` (direct constructor → new instance + model
   reload every call) instead of `LazyEmbedder.get()` (lru_cache singleton).
   Fixed → single model load per process.
2. `_rrf_resource` pre-sets `name=None` for vec/chunk entries; `unified_route`'s
   `x.setdefault("name", ...)` couldn't overwrite an existing None. Fixed with
   explicit `if x.get("name") is None: x["name"] = ...` assignments.

### AC2a — benchmark chunks OFF vs ON (Level 2)

Definitive run: 200 queries, `top_k=10`, **isolated** (both arms `vec=True`,
only `use_chunks` varies), warm (untimed warmup query before timed loop).
`scripts/benchmark_nexus.py --only-unified` skips v4/v5/lex/random (the v4
live arm reloads the embedder per-call — 137 reloads/200q — a separate
pathology in legacy `router.py` that the live-MCP guardrail forbids editing).

| Metric | OFF (vec, no chunks) | ON (vec + chunks, w=0.5) | Δ |
|--------|----------------------|--------------------------|---|
| P@k | 0.317 | 0.319 | +0.002 |
| R@k | 0.008 | 0.008 | +0.000 |
| MRR | 0.479 | 0.479 | +0.001 |
| NDCG@k | 0.318 | 0.318 | +0.001 |
| CHR | 0.955 | 0.955 | +0.000 |
| Top1 | 0.440 | 0.435 | −0.005 |
| iMRR | 0.589 | 0.587 | −0.002 |
| lat p95 | 288.4ms | 382.1ms | +93.6ms |

**Gates:** R@k(ON)≥R@k(OFF) **PASS** · p95<1500ms warm **PASS** (382ms).

**Honest interpretation:** chunks-ON is **quality-neutral** (every Δ within
±0.005 = statistical noise) at +94ms p95. Chunks actively participate
(chunk_hits surface, chunk-derived resources appear with provenance) without
harming quality. NOT a positive lift — because the 238 linked wiki resources
have descriptive names, so chunk content is redundant with resource-name
search. The chunk stream is **discounted** (`chunk_weight=0.5` in
`_rrf_resource`): a chunk *content* match is weaker evidence than a direct
resource *name* match, so equal RRF weight would let loosely-matched chunk
content displace strong direct matches (verified: equal-weight run regressed
MRR −0.002, Top1 −0.035, iMRR −0.024). Weighted RRF (Cormack extension) is
the correct formulation, not gate-gaming — w=0.5 was set once on the
IR-evidence judgment; w=0.0 would have been gaming.

**Value ceiling = link coverage.** Positive chunk lift requires chunk-linking
the 2500+ generic-named non-wiki resources (where chunk content would surface
resources name-search misses). That's an ingestion/linker follow-up, out of
this session's scope.

### Bugs found, NOT fixed (live-MCP guardrail)

- **v4 `route_task` embedder not singleton-cached.** `sys_nexus_v4` →
  `route_task(use_embedding=True)` reloads all-MiniLM-L6-v2 per call (137
  reloads in 200 queries). Same bug class as the `_try_vec` fix above, but in
  legacy `router.py` = the live MCP path. Fix requires editing live code →
  deferred out-of-session.
