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