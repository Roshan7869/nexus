# NEXUS — Micro Plan (atomised tickets)

Status: DRAFT v1 · 2026-09-13
Companions: `00-HLD.md`, `01-LLD.md`, `02-PLAN-MACRO.md`, `04-FEASIBILITY.md`

Every ticket carries the same six fields, because "done" must be checkable without re-reading this
document: **owner · risk · inputs · outputs · verification · rollback**.

Owners are roles, not people (this is a solo/agent-driven build): `ARCH` (requires your decision),
`ENG` (mechanical, verified by test), `OPS` (touches the live daemon or DB).

Estimates are wall-clock for a competent implementer with the repo loaded. Anything over 2h is a
signal the ticket is under-decomposed.

---

## TICKET INDEX

| id | section | phase | owner | risk | est | blocks |
|---|---|---|---|---|---|---|
| A4-1 | S7 | P1 | ENG | low | 30m | A2-1, A3-1 |
| A1-1 | S5 | P1 | OPS | low | 1h | all of P4 |
| A1-2 | S5 | P1 | OPS | low | 15m | — |
| A1-3 | S8 | P1 | OPS | low | 30m | — |
| A2-0 | S2 | P1 | ENG | low | 15m | **A2-1** |
| A2-1 | S2 | P1 | ENG | medium | 1h | I1 remediation |
| A2-2 | S2 | P1 | ENG | low | 30m | — |
| A3-1 | S1 | P1 | OPS | medium | 2h | C3-1 |
| B1-1 | S6 | P2 | ENG | low | 20m | — |
| B2-1 | all | P2 | OPS | medium | 1h | — |
| B3-1 | — | P2 | OPS | low | 10m | — |
| C0-1 | S6 | P4 | ENG | medium | 1.5h | C1-1 |
| C1-1 | S3 | P4 | ENG | medium | 2h | C2-1, C4-1 |
| C2-1 | S2/S3 | P4 | ENG | medium | 2h | C4-1 |
| C3-1 | S1/S7 | P4 | ENG | high | 2h | C4-1 |
| C4-1 | all | P4 | ENG | high | 2h | — |
| D1-1 | S2 | P5 | ENG | medium | 1.5h | — |
| D2-1 | S2 | P5 | ENG | low | 30m | — |
| D3-1 | S8 | P5 | ENG | low | 1h | D1-1, D2-1 |
| I2-1 | S6 | P6 | ENG | low | 1h | — |
| I3-1 | S5 | P6 | OPS | medium | 1.5h | — |
| I4-1 | S4 | P6 | ENG | medium | 2h | — |
| I5-1 | S3 | P6 | ENG | medium | 2h | C1-1 |
| I5-2 | S3 | P6 | ENG | medium | 2h | — |

---

## PHASE 1 — FUNDAMENT

### A4-1 — Isolate learner state per test
- **Owner** ENG · **Risk** low · **Est** 30m · **Blocks** A2-1, A3-1
- **Why** full suite is 2 failed / 374 passed, both on the learner-disabled path; `test_unified.py` is
  38/38 alone. Without this baseline, A2/A3 changes are unmeasurable.
- **Inputs** `tests/`, `server/learning.py`, the learner enable/disable flag
- **Outputs** `tests/conftest.py` fixture (`01-LLD.md` §7.2) restoring learner state on teardown
- **Verification** full suite green; green again under shuffled order; the two failures stay fixed in
  both orders
- **Rollback** delete the fixture — it is additive and touches no production module

### A1-1 — One served surface
- **Owner** OPS · **Risk** low · **Est** 1h · **Blocks** all of P4
- **Why** HLD §3: the running daemon serves the ops surface (32 routes); `/v1/models` and
  `/v1/chat/completions` 404; the README documents the unserved one.
- **Inputs** `server/nexus_http_daemon.py`, `server/unified.py:173`, `core/server.py:129`
- **Outputs** unified.py's OpenAI routes **mounted** onto the running daemon via
  `app.include_router` / `add_api_route`; `core/server.py` → `attic/`
- **Verification** `curl -o /dev/null -w '%{http_code}'` for every route the README names → non-404;
  `POST /v1/chat/completions` with a real payload → 200 or 4xx, never 404; daemon restart clean
- **Rollback** remove the mount; `nexus_http_daemon.py` is otherwise untouched
- **Note** mount rather than merge the files — merging two FastAPI apps by hand is where the 39-vs-32
  divergence came from

### A1-2 — Fix the dead daemon URL
- **Owner** OPS · **Risk** low · **Est** 15m
- **Inputs** `.mcp.json:**33**` (corrected — was cited as 34)
- **Outputs** `NEXUS_DAEMON_URL` → `http://127.0.0.1:8000`
- **Verification** `:8000` is the only listener (`ss -ltn`); an MCP tool that calls the daemon succeeds
- **Rollback** revert the one line

### A1-3 — `/v1/ops` latency **(RE-SCOPED — there is no hang)**
- **Owner** OPS · **Risk** low · **Est** 30m → **demoted out of P1**
- **Original claim** live probe returned `000` (no response in 3 s) while declared at
  `nexus_http_daemon.py:163`. Recorded as "a served route that times out is its own bug."
- **RE-VERIFIED 2026-09-13:** five consecutive probes return **200 in 1.35–2.07 s**. The original `000`
  was transient — the daemon was almost certainly mid-restart. **The stated symptom does not
  reproduce.** See `exec/06-CORRECTIONS.md` P3.
- **Re-scoped outputs** answer *why an ops endpoint costs the same 1.2–2.1 s as a full route* — the
  likely cause is that `/v1/ops` calls the router internally. This is a latency lead, not a bug fix.
- **Disposition** closed, or folded into latency profiling (`exec/04-EXEC-FEASIBILITY.md` M2).
  **Do not spend P1 time on a hang that is not there.**
- **Commands** `for i in 1 2 3 4 5; do curl -s -o /dev/null -m 5 -w '%{http_code} %{time_total}\n' http://127.0.0.1:8000/v1/ops; done`

### A2-0 — Which gate blocks the short-circuit? **(do this first)**
- **Owner** ENG · **Risk** low · **Est** 15m · **Prereq** A4-1 · **Blocks** A2-1
- **Why** `04-FEASIBILITY.md` §2: **0 of 8 realistic queries took the short-circuit path.** Either
  `confidence` never clears `SHORT_CIRCUIT_CONFIDENCE = 0.85` or `pool_size` never clears
  `SHORT_CIRCUIT_MIN_POOL = 50` (`v5.py:123-124, 463`). The two answers lead to **opposite actions**,
  so this must be measured, not assumed.
- **Inputs** `v5.py` gate condition; the 8-query set in `_research/_bench_route.py`
- **Outputs** a reported value for `intent["confidence"]` and `_cluster_pool_size(cluster)` per query
- **Verification** the report names which gate fails on how many of 8
- **Branch** confidence fails → `_short_circuit` is dead code; **delete it (B-track), cancel A2-1** ·
  pool_size fails → `_cluster_pool_size` undercounts; **fix the counter, then A2-1 is live**
- **Rollback** read-only instrumentation

> **This ticket was added after `_research/IMPLEMENTATION_PLAN.md` was written.** The measurement
> contradicted that design's premise. Do not implement A2-1 on the earlier reasoning alone.

### A2-1 — Cold quota on the L1 fast path
- **Owner** ENG · **Risk** medium · **Est** 1h · **Prereq** A2-0 (must return "pool_size fails")
- **Why** `01-LLD.md` §2.2: `_short_circuit` never reaches `_v5_rerank`, so a confident L1 returns
  100% already-triggered resources. This is G_COLDSTART — **latent, not active**, because the path is
  currently unreachable.
- **Do not implement if A2-0 shows the path is dead** — deleting it is the honest fix.
- **Inputs** `server/router_v2/v5.py` `_short_circuit` (237-268), call site 463, `cold_fraction`
  (`router_cutover.py:72`)
- **Outputs** `_cold_backfill()` + quota logic (`01-LLD.md` §2.3), ~10 LOC
- **Verification** seed one `times_triggered = 0` resource in a strong cluster; issue a query that
  trips the short-circuit; assert it appears. Then: `router_ab_suite` top-1 agreement non-regressing
  and `invoke_cold_target_pct` ≥ 35.0 **on the short-circuit path**
- **Rollback** single function; revert. **This touches the routing hot path — A/B before landing.**

### A2-2 — Waterfall hook point
- **Owner** ENG · **Risk** low · **Est** 30m
- **Outputs** a post-`_autocut` interception point where a listener that skips `next()` vetoes
  (`01-LLD.md` §2.4), attachable without editing `_autocut`
- **Verification** a registered veto removes one result; an unregistered run is byte-identical to today
- **Rollback** no listeners registered = no behaviour change

### A3-1 — Collapse to one lifecycle column
- **Owner** OPS · **Risk** medium · **Est** 2h · **Prereq** A4-1 · **Blocks** C3-1
- **Why** 132 contradictory rows, 2,389 NULLs, 2,521 rows needing a value (`00-HLD.md` §4 S1).
- **Inputs** `db/nexus.db` (**538.6 MB**; `resources` itself is only 22.2 MB) — **take a full dump first** — `resources` table
- **Outputs** migration `NNNN_unify_lifecycle.sql` (copy-don't-ALTER), CHECK constraint, recorded in
  `schema_migrations`
- **Verification** contradictory-row count = 0; a deliberate invalid write is rejected; migration
  re-runs idempotently; row count still 3,255
- **Rollback** down-migration restores both columns from the pre-migration dump
- **Note** stop the daemon; single writer. Test against a copy first.

---

## PHASE 2 — DELETION

### B1-1 — Remove the dead chain
- **Owner** ENG · **Risk** low · **Est** 20m
- **Inputs** `server/autonomous_pipeline.py` (**396 L** — re-measured, was cited 397),
  `router_v2/fts5.py` (**215 L** — was cited 216), `server/test_v3.py` (**380 L**)
- **Verification** suite green; `grep -rn autonomous_pipeline` returns only the two known prose
  references (`scripts/auto_close_routes.py:25`, `router_v2/scoring.py:26`) before deletion, zero after
- **Rollback** `git checkout` the three paths

### B2-1 — Quarantine the 95-file list
- **Owner** OPS · **Risk** medium · **Est** 1h
- **Inputs** `_research/delete_list_recovered.json`,
  `_research/verify_delete_recovered.py` (recovered; **never yet run to completion** — the earlier
  attempt died at the 300 s cap)
- **Outputs** 95 files moved to quarantine; suite result before/after recorded
- **Verification** suite green after; the script restores on every exit path incl. SIGINT/TERM/HUP
- **Rollback** the script's restore path; verify file count returns to 491 tracked / 95 present
- **Note** batch it (e.g. 20 files per run) so a timeout cannot leave a partial state

### B3-1 — Remove the literal `$HOME` directory
- **Owner** OPS · **Risk** low · **Est** 10m
- **Inputs** `/home/roshan/nexus/$HOME/nexus/db/nexus.db` (184 KB, 12 Jul)
- **Outputs** directory removed; the script that creates it fixed (unexpanded `$HOME`)
- **Verification** path gone; no file recreated after a full script run
- **Rollback** it is a stray 184 KB DB — nothing depends on it, but archive before deleting

---

## PHASE 4 — CORDIS (YES branch)

### C0-1 — The disposer
- **Owner** ENG · **Risk** medium · **Est** 1.5h
- **Inputs** `01-LLD.md` §6.2 (the full ~30 LOC), the live flow
  `harness_study_project → _harvest_all → harvest_site → _one → fetch_page`
- **Outputs** `server/disposer.py` (Handle/Disposed/atomic) + the study flow converted to `with`
- **Verification** `_demo()` self-check; a study killed mid-harvest leaves no orphan rows or temp
  files; second `close()` is a no-op; post-close `effect()` raises `Disposed`; order is reverse
- **Rollback** removing the `with` block restores prior behaviour — it is additive
- **Note** do **not** hand-write LIFO/idempotent/cascade/atomic. `ExitStack` is all four; `pop_all()`
  is atomic recomposition in one stdlib call. Writing them is the defect, not the fix.

### C1-1 — Finish the declared registry migration
- **Owner** ENG · **Risk** medium · **Est** 2h · **Prereq** C0-1
- **Why** `backend_protocol.py:142` declares `fuse_registered()`/`BackendRegistry` canonical; 13
  backends still register via the old `Fuser`; the new registry has zero production users.
- **Inputs** `server/context_harness/server.py:918-1188`, `fusion.py`, `backend_protocol.py`
- **Outputs** all 13 backends on `BackendRegistry` with optional `close()` (`01-LLD.md` §3.3)
- **Verification** `fusion.py` `Fuser` reaches zero production callers; all 13 backends still return
  results; a backend with `close()` is disposed on registry close and one without still works
- **Rollback** the old registration block is retained behind a module flag until verified

### C2-1 — Waterfall dispatch
- **Owner** ENG · **Risk** medium · **Est** 2h · **Prereq** C1-1
- **Inputs** `dsh.md` §4 (five modes: emit/parallel/serial/bail/waterfall), S2/S3 assembly points
- **Outputs** a dispatcher where a listener that does not call `next()` vetoes
- **Verification** a veto removes a step; parallel vs serial ordering observable; no listener =
  identical output
- **Rollback** unregistered dispatcher is a pass-through

### C3-1 — Fiber
- **Owner** ENG · **Risk** high · **Est** 2h · **Prereq** C1-1, A3-1
- **Inputs** `precondition.py:65-114` (`StateGates`), `01-LLD.md` §1.3
- **Outputs** `Fiber(parent, state, effects)` with θ ∈ Inactive/Reloading/Active/Unloading; DB
  `lifecycle_state` becomes a projection of θ
- **Verification** `lifecycle_divergence_count` = 0 in steady state; a gate registered on a Fiber is
  disposed with it; `check_preconditions`/`update_state` still work
- **Rollback** `StateGates` retained as a facade until the Fiber is proven
- **Note** `GLOBAL_GATES` is **live** — it backs two MCP tools. Wrap, do not delete.

### C4-1 — Hot swap
- **Owner** ENG · **Risk** high · **Est** 2h · **Prereq** C2-1, C3-1
- **Outputs** swap a backend with zero restart; `atomic()` keeps the previous tree on failure
- **Verification** swap under load with no dropped request; a deliberately failing recomposition
  leaves the old tree serving; no in-flight operation observes a half-mounted tree
- **Rollback** `atomic()` guarantees it by construction — this is the point of `pop_all()`
- **Open** in-flight swap semantics are **PARTIALLY SPECIFIED** in `dsh.md` §4. **Decide and document
  explicitly**: does disposal wait for, or abort, an in-flight operation? Do not inherit by accident.

---

## PHASE 5 — HY (parallel)

### D1-1 — Independent scoring
- **Owner** ENG · **Risk** medium · **Est** 1.5h
- **Inputs** `hy.md` §2.1 (sigmoid + learned bias, not softmax), `v5.py` `_rank_normalize` + RRF
- **Outputs** an independent per-resource score layer on top of the fusion
- **Verification** `router_ab_suite` top-1 agreement non-regressing; scores no longer normalise to 1.0
- **Rollback** fusion path retained; new score is additive and can be ignored by consumers

### D2-1 — Guaranteed fallback
- **Owner** ENG · **Risk** low · **Est** 30m
- **Inputs** `hy.md` §2.3 shared expert; `_autocut`'s `if not merged: return []`
- **Outputs** never return empty — always include the cluster's default skill
- **Verification** zero empty results across the A/B suite
- **Rollback** revert the fallback insertion

### D3-1 — Per-cluster telemetry
- **Owner** ENG · **Risk** low · **Est** 1h · **Prereq** D1-1, D2-1
- **Inputs** `hy.md` §2.5; existing `cluster_affinity_history`, `coach_burndown`
- **Outputs** per-cluster trigger counters + load-balance metric
- **Verification** the metric is what D1-1/D2-1 gates read

---

## PHASE 6 — PER-INTEGRATION

### I2-1 — Triage 24 dead agents
- **Owner** ENG · **Risk** low · **Est** 1h
- **Outputs** each of 24 either reactivated or archived with a reason in `archive_reason`
- **Verification** zero agents in `active` with no explanation

### I3-1 — MCP reconciliation for **absent** servers **(RE-SCOPED — premise retracted)**
- **Owner** OPS · **Risk** medium · **Est** 1.5h
- **RETRACTED:** the original claim was "28/329 active. `sync_mcp_tools` **upserts with no removal
  path** (`mcp_sync.py:222`), so a desynced server's tools persist forever." **This is false.**
  - `delete_tool` exists at **`mcp_sync.py:155`** and soft-deletes (`UPDATE resources SET
    status='archived'`).
  - `sync_from_mcp_server` (**`:178`**) calls it at **`:218-222`** for names not in `server_names`.
  - There is no symbol `sync_mcp_tools` in `mcp_sync.py` — that is an MCP *tool* name; the manager
    method is `sync_from_mcp_server`, called at `nexus-mcp-enhanced.py:964`. *(P2)*
- **Consequence** the 8.5% active rate is **not rot** — 301 archived rows are the removal path having
  **succeeded**. This must not be repeated as a health metric without saying what it measures.
- **Real defect** the blade only falls on tools of servers **still in the sync loop**. A server gone
  from `.mcp.json` is never called again, so its tools are **never evaluated for removal** and sit
  `active` forever. Reconcile against the server registry **including absent servers**.
- **Secondary defect** `delete_tool` never sets `archived_at`: `SELECT COUNT(*) FROM resources WHERE
  resource_type='mcp' AND status='archived' AND archived_at IS NULL` → **61**.
- **Verification** deliberately desync a *registered* server → its tools disappear; re-sync → they
  return; `mcp_sync_log` records both. **Plus** a test for an **absent** server's tools being reconciled.
- **Rollback** the reconciliation is a new function; disable it without touching the upsert

### I4-1 — Toolset registry
- **Owner** ENG · **Risk** medium · **Est** 2h
- **Inputs** `harness_engine/toolsets.py` (static tuples), `01-LLD.md` §4.2
- **Outputs** `ToolsetRegistry.mount(name, tools, requires=()) -> Handle`
- **Verification** a new toolset mounts and unmounts with no code edit; unmounting a dependency
  cascades; a failed mount leaves no half-bound plan
- **Rollback** the tuple constants remain the default until the registry is proven

### I5-1 — Disposable, extensible sites
- **Owner** ENG · **Risk** medium · **Est** 2h · **Prereq** C1-1
- **Outputs** adding a site = a registry entry + one channel module, **no edit to
  `server.py:918-1188`**; per-use-case defaults from `05-SITES-REGISTRY.md`
- **Verification** add one site end-to-end without touching `server.py`; it appears in fused results;
  removing it disposes cleanly
- **Rollback** registry defaults unchanged

### I5-2 — Load the site registry from YAML
- **Owner** ENG · **Risk** medium · **Est** 2h
- **Why** `05-SITES-REGISTRY.md` specifies `config/harness_sites.yaml` as the source of truth, but no
  loader exists — today registration is a code edit inside `server.py:918-1188`.
- **Inputs** `config/harness_sites.yaml`, `BackendRegistry` (`backend_protocol.py:35`)
- **Outputs** a startup loader that reads the YAML, resolves `channels/<file>.py`, and registers each
  site via `BackendRegistry.register()` with its weight/ttl/timeout
- **Verification** adding a site to the YAML and restarting makes it appear in fused results with **no
  code edit**; a malformed entry fails loudly at startup, not silently at query time; removing an
  entry disposes the site
- **Rollback** keep the code-path registrations as a fallback until the loader is proven

---

## Cross-cutting acceptance criteria

Every ticket, without exception:

1. `pytest` green before and after (full suite, not the file).
2. No new `# type: ignore`, no new `except Exception: pass`.
3. Rollback path stated in the same commit message as the change.
4. Touches the live daemon or DB → `owner: OPS`, tested against a copy first.
5. Routing-path change → `router_ab_suite` top-1 agreement recorded before/after.
6. ≥2h estimate → the ticket is under-decomposed; split it.
