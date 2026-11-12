# NEXUS — Macro Plan (per integration)

Status: DRAFT v1 · 2026-09-13
Companions: `00-HLD.md`, `01-LLD.md`, `03-PLAN-MICRO.md`, `04-FEASIBILITY.md`, `05-SITES-REGISTRY.md`

A **macro phase** is a milestone with an entry gate, a set of integrations that move together, and an
exit gate that is measured, not asserted. If the exit gate fails, the phase does not advance.
Ticket-level atomisation lives in `03-PLAN-MICRO.md`.

---

## 0. Integration inventory (live, 2026-09-13)

Active vs total per integration. 1,862 / 3,255 active overall (57%).

| # | integration | active / total | inactive | health |
|---|---|---|---|---|
| I1 | **Skills** | 529 / 1,296 | 767 | 41% — largest absolute rot |
| I2 | **Agents** | 162 / 186 | 24 | 87% — healthy |
| I3 | **MCPs** | **28 / 329** | **301** | see I3 below — **the 8.5% measures the removal path working, NOT rot** |
| I4 | Harness agents (harness_engine) | n/a (code) | — | 4 modules, static toolset partition |
| I5 | Harness sites (channels/backends) | 13 registered | — | two parallel registries (HLD §2) |
| — | Commands | 220 / 276 | 56 | 80% |
| — | Docs | 106 / 194 | 88 | 55% |
| — | Design systems | 73 / 173 | 100 | 42% |
| — | Specs | 142 / 156 | 14 | 91% |
| — | CLI tools | 130 / 137 | 7 | 95% |
| — | Hooks | 44 / 77 | 33 | 57% |
| — | Topics | 419 / 419 | 0 | 100% |
| — | Craft | 9 / 12 | 3 | 75% |

**The macro story:** skills (767 inactive) are 74% of all inactive resources, and **I1 is gated on A2** —
a skill inactive because it was never *reachable* is not dead. MCPs are **not** the crisis they were
described as: their removal path works (see I3). Skills are where the real rot is, and part of it is
measurable as a routing defect rather than decay.

---

## PHASE 0 — Correct the record

**Entry gate:** none. **Duration:** hours. **Code change:** none.

Three research claims failed live re-verification (`_research/IMPLEMENTATION_PLAN.md` PART 0):

| claim | verdict | correction |
|---|---|---|
| "`times_triggered > 0` filters out 743" | **wrong mechanism** | the queries *annotate* `hot`; the lockout is `_short_circuit` bypassing the cold tier (v5.py:237, 463, 481) |
| "three copies of one API" | **wrong shape** | one served ops surface (32 routes) + two unserved forks of a *different* surface |
| "659 lifecycle NULLs" | **subset** | 659 is NULLs *among archived*; 2,521 rows need a value |

**Plus three more found in the execution pass (`exec/06-CORRECTIONS.md`):**

| claim | verdict | correction |
|---|---|---|
| `Fuser` (`fusion.py:44`) | **wrong symbol** | the class is **`RRFFuser`** (`fusion.py:34`); `unregister` is at `:44` (P1) |
| I3: "`sync_mcp_tools` upserts with **no removal path**" | **FALSE** | `delete_tool` (`mcp_sync.py:155`) exists and `sync_from_mcp_server` (`:178`) calls it at `:218-222`. The 8.5% MCP figure measures the removal path **working**, not rot (P2) |
| A1-3: "`/v1/ops` returns `000` (TIMEOUT)" | **not reproducible** | 5/5 probes = **200 in 1.35–2.07 s**; the original `000` was transient (P3) |

**Exit gate:** `docs/design/` reflects all six corrections. No code touched.

---

## PHASE 1 — Fundament (Track A)

**Entry gate:** P0 exit. **Repos touched:** S1, S2, S3, S5, S7, S8. **~60 LOC.**

Runs regardless of the PART 1 decision, and must complete before any composition work.

| macro step | integrations | section | exit gate |
|---|---|---|---|
| **A4** deterministic suite | I1, I5, S7 | S7 | full `pytest` green in default **and** shuffled order |
| **A1** one served surface | I3, S5 | S5 | every README-named route non-404; `POST /v1/chat/completions` ≠ 404; `/v1/ops` under timeout; `NEXUS_DAEMON_URL` → `:8000` |
| **A2** cold quota on fast path | I1, I2, I3 | S2 | short-circuit path returns ≥1 never-triggered resource on a seeded query; `invoke_cold_target_pct` ≥ 35.0 measured on that path; `router_ab_suite` top-1 agreement non-regressing |
| **A3** one lifecycle column | all | S1 | contradiction count = 0; invalid write rejected; migration idempotent and in `schema_migrations` |

**Order is A4 → A1 → A2 → A3 and is not negotiable.** A4 gives the baseline that makes A2/A3
measurable; A1 removes the surface ambiguity before anything is built above it.

**Phase exit gate (all four):** suite green · README routes non-404 · `router_mode = unified_v5` ·
`lifecycle_divergence_count` = 0.

---

## PHASE 2 — Deletion

**Entry gate:** P1 exit. **Deletes:** 632 LOC + 95 files / 15,219 LOC.

| macro step | what | gate |
|---|---|---|
| **B1** | `autonomous_pipeline.py` (396 L) + `router_v2/fts5.py` (215 L) + `test_v3.py` (380 L) | suite green; `grep -rn autonomous_pipeline` returns prose only |
| **B2** | the recovered 95-file list | `verify_delete_recovered.py` completes; suite green after each of 95, in batches |
| **B3** | the stray literal `$HOME` directory | path gone; no script recreates it |

**Do NOT delete:** `GLOBAL_GATES` / `precondition.py` (live — backs `check_preconditions` /
`update_state`); `HallucinationGate` (constructed at `router.py:321`). The research already retracted
this; it is flagged here so nobody re-kills it.

**Exit gate:** net −15,851 LOC, suite green, no live import of any deleted module.

---

## PHASE 3 — THE DECISION

**Entry gate:** P2 exit.

> **Is NEXUS becoming a plugin host?**

| answer | consequence | size |
|---|---|---|
| **NO** | C0 only — apply the disposer to the one live flow. Nothing else. | ~30 LOC |
| **YES** | C0→C1→C2→C3→C4 | ~250–400 LOC, replaces the hardcoded pipeline |

Retrofit cost is asymmetric: building composition *after* plugins exist costs several times more than
before. If the answer is "probably eventually", it is YES and the answer should be given now.

---

## PHASE 4 — CORDIS composition (YES branch only)

**Entry gate:** P3 = YES. **Sections:** S3 (primary), S4, S5, S6, S7.

| macro step | primitive | integration touched | exit gate |
|---|---|---|---|
| **C0** | Disposer | I5, S6 | `_demo()` passes; killed-mid-harvest study leaves no orphan rows/temp files; double `close()` no-op; post-close `effect()` raises `Disposed` |
| **C1** | Registry (complete the declared migration) | I5, I3 | all 13 backends on `BackendRegistry`; `fusion.py` `Fuser` reaches zero production callers; `backend_protocol.py:142` policy satisfied |
| **C2** | Waterfall | I4, S2, S3 | a listener that skips `next()` vetoes an assembly step; mode is observable in telemetry |
| **C3** | Fiber | S1, S7 | `StateGates` and resource lifecycle are Fibers; `lifecycle_divergence_count` = 0 |
| **C4** | Hot swap | all | swap a backend with zero restart; failed recomposition keeps the previous tree (`atomic()`); no in-flight operation observes a half-mounted tree |

**Explicitly not in Phase 4:** CORDIS proof obligations (Thms 68–80, `C_CONFLUENCE`, `C_PROGRESS`),
`G_MODULE` (needs `import-linter`/restructure, not a DI container), and in-flight swap semantics
(`dsh.md` §4: PARTIALLY SPECIFIED — decide explicitly, do not inherit by accident).

---

## PHASE 5 — HY routing quality (independent; parallel from P1)

**Entry gate:** none beyond P1. **Sections:** S2, S8. **Closes zero gaps** — this is quality, not
structure.

| macro step | pattern | exit gate |
|---|---|---|
| **D1** | `C_INDEP_SCORE` — independent per-resource score instead of competing for 1.0 | `router_ab_suite` top-1 agreement non-regressing; score distribution no longer sums to 1.0 |
| **D2** | `C_SHARED_EXP` — guaranteed fallback: never return empty; always include the cluster default | zero empty results over the A/B suite; `_autocut`'s `if not merged: return []` unreachable from routing |
| **D3** | `C_TELEMETRY` — per-cluster trigger counters + load balance | metric exists and is queried by D1/D2 gates |

`C_ANNOTATE` (TAG-MoE) is **analogy-only** (`hy.md` §4.1, §6.0) — routing tokens to experts inside a
model is not routing tasks to resources. Do not over-generalise.

---

## PHASE 6 — Per-integration remediation (runs alongside P1–P5)

Each integration gets its own remediation track because each rots differently. Detail in
`03-PLAN-MICRO.md`.

### I1 — Skills (529/1,296 active; 767 dead)
**Macro goal:** every inactive skill is either restored or explained. A skill is inactive either
because it is genuinely obsolete or because **it was never reachable — and I5 says the cold tier was
starved on the fast path.** P1/A2 is therefore *also* an I1 fix: un-starve the cold tier, wait one
measurement window, and re-count. Only what remains dead is genuinely dead.
**Gate:** 767 → measured drop attributable to A2, then an explicit decision per remaining skill.

### I2 — Agents (162/186; 24 dead)
**Macro goal:** triage 24. Small, cheap, do it during P1.
**Gate:** 24 → 0 unexplained.

### I3 — MCPs (28/329 active; 301 archived) — **PREMISE RETRACTED**
**Macro goal:** the original framing ("worst surface, critical rot") was **wrong**. `delete_tool`
(`mcp_sync.py:155`) exists and `sync_from_mcp_server` (`:178`) calls it at `:218-222` — **the removal
path has been working**, which is *why* 301 rows are archived. The 8.5% figure measures successful
removal, not rot. See `exec/06-CORRECTIONS.md` P2.
**Real gap:** servers **absent** from the sync loop are never reconciled — their tools stay `active`
forever. The blade only falls on servers still being synced.
**Macro steps:** A1 fixes the dead port; reconcile against the server registry *including absent
servers*; set `archived_at` in `delete_tool` (61 rows today have `archived_at IS NULL`); re-count.
**Gate:** a deliberately desynced *registered* server loses its tools; a test exists for an **absent**
server's tools being reconciled; `archived_at IS NULL` among archived MCP rows → 0.

### I4 — Harness agents (`harness_engine/`)
**Macro goal:** replace the static `CORE_TOOLS`/`RESEARCH_TOOLS`/`BROWSER_TOOLS`/`MEMORY_TOOLS` tuple
partition with a `ToolsetRegistry` (`01-LLD.md` §4.2) supporting `requires=` dependencies and cascade.
**Gate:** a new toolset mounts and unmounts without a code edit; a failed mount leaves no half-bound
plan.

### I5 — Harness sites (channels/backends)
**Macro goal:** finish the migration declared at `backend_protocol.py:142`; make sites disposable and
**user-extensible per use case**. This is the integration the user extends directly — see
`05-SITES-REGISTRY.md`.
**Gate:** adding a site is a registry entry + one channel module, no edit to `server.py:918-1188`.

---

## Critical path (summary)

```
P0 ─→ P1 (A4→A1→A2→A3) ─→ P2 (B1→B2→B3) ─→ P3 DECISION ─→ P4 (C0→C1→C2→C3→C4)
                                                │
                                                └─ NO → C0 only, stop

P5 (D1→D2→D3)  ──  parallel from P1, depends on nothing
P6 (I1..I5)    ──  parallel, gated by P1 for I1/I3
```
