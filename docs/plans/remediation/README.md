# NEXUS Remediation — Per-Change Plans (index)

Source: `NEXUS_REMEDIATION_EXECUTION_PLAN_v4.md` (repo root). This folder splits that plan into one file per change (A1 → E2, 14 changes). Each file has: verified current state, corrections to the source plan, corrected pseudocode, ordered steps, tests, rollback, and open decisions.

Phase 1 (Cordis site binding) is separate: `docs/plans/cordis-phase1-plan.md` — **implemented** (commits `0ced4ad` … `6a3abaa`).

## Scope

Covers the 14 changes in the remediation plan. It does **not** cover every Python file in the repo (~60 modules under `server/` alone). Files outside the plan are listed only where a change depends on them.

## Index and order

| # | File | Change | Depends on | Status |
|---|---|---|---|---|
| 1 | [A1-single-router.md](A1-single-router.md) | One production router path | — | Open (A1.2 already done) |
| 2 | [A2-record-outcome.md](A2-record-outcome.md) | One outcome API | A1 | Open |
| 3 | [A3-real-verifier.md](A3-real-verifier.md) | Replace fake verification | A2 | Open — verifier timeout issue |
| 4 | [A4-gate-dashboard.md](A4-gate-dashboard.md) | Gate dashboard endpoints | A1 | Open |
| 5 | [B1-project-scope.md](B1-project-scope.md) | Project-scoped ranking | A1 | Open |
| 6 | [B2-project-memory.md](B2-project-memory.md) | Inject project memory | B1 | Open — `get_project_context` missing |
| 7 | [C1-preview-default.md](C1-preview-default.md) | Autopilot preview-only default | A3 | Open |
| 8 | [C2-real-dispatcher.md](C2-real-dispatcher.md) | Real tool dispatch + approval | C1, A3 | Open — pseudocode is a sketch |
| 9 | [C3-ide-sessions.md](C3-ide-sessions.md) | Auto-register IDE sessions | — | Open — conflicts with CLAUDE.md design rule |
| 10 | [D1-harness-eager.md](D1-harness-eager.md) | Harness eager by default | A1 | Open |
| 11 | [D2-harness-prefetch.md](D2-harness-prefetch.md) | Harness context before routing | D1 | Open — plan pseudocode uses wrong return shape |
| 12 | [D3-harness-ranking.md](D3-harness-ranking.md) | Harness toolsets boost ranking | D2 | Open |
| 13 | [E1-supervisor-topology.md](E1-supervisor-topology.md) | Supervisor owns MCP + HTTP | A4, C3 | **Blocked on decision** — conflicts with CLAUDE.md |
| 14 | [E2-boot-sanity.md](E2-boot-sanity.md) | Boot sanity + `/health` | E1 | Open — `/health` already exists |

## Corrections that apply to all changes

These were found by checking the source plan against the code on `second-brain-v2`.

1. **Route and search endpoints live in `server/unified.py` (port 8000), not `server/nexus_http_daemon.py` (port 8080).** The plan's `/nexus/route`, `/nexus/search`, `/nexus/autopilot/run`, `/nexus/task-runs`, and dashboard endpoints are all in `unified.py`. The plan's `curl … :8080/nexus/route` checks will fail. Use `:8000`.
2. **Line numbers are stale.** Examples: `broker_runtime.py:475` already calls `route_for_task`; `auto_execute` default is at `broker_runtime.py:555` (not 553); the hardcoded `outcome="correct"` is at `broker_runtime.py:1080` (not 692); `router.py:_evaluate_outcome` is at `:391` (not 389). Re-locate with `grep` before every edit.
3. **Gate test files named in the plan don't exist** (`test_router_unified.py`, `test_learning_loop.py`, `test_no_fake_verify.py`, `test_project_boost.py`). Create them under `tests/`.
4. **Migrations go in `db/migrations/`** (last file: `021_experiments.sql`; next: `022_…`), not `server/migrations/`.
5. **`core/session_builder.py` does not exist.** Put project memory helpers in `server/learning.py` (plan's own fallback).
6. **Unit tests for learning must not write to the live DB.** The plan's `test_learning_loop.py` mutates `hermes-agent` counters in `db/nexus.db`. Use the in-memory fixtures in `tests/conftest.py` (`make_db`, `mock_rw_conn`).
7. **CLAUDE.md conflicts with two plan items** — see E1 and C3. Those need a decision before implementation.

## Gates (from plan, mapped to files)

| Gate | Check | File that delivers it |
|---|---|---|
| G1 | HTTP, MCP, broker return same top-3 | A1 |
| G2 | Non-terminal outcome leaves counters unchanged | A2 |
| G3 | Terminal outcome increments `times_correct` | A2 |
| G4 | Production routing never returns `correct` | A3 |
| G5 | Real verifier matches pytest exit code | A3 |
| G6 | `project=default` → HTTP 400 | B1 |
| G7 | Project boost ranks project resources higher | B1 |
| G8 | Autopilot returns `status: previewed` | C1 |
| G9 | `/nexus/route` response has `harness` key | D1 |
| G10 | `/health` reports `all_pass: true` | E2 |

## Verification baseline (before any remediation change)

- Full suite: 490 passed, 24 skipped (`pytest tests -q`).
- Golden routing eval (105 cases): pass 0.600, skill match 0.500, cluster-ok 0.750 — at both `c8782ad` and `6a3abaa`.
- Ops gates: `cluster_divergence` fails (1326 vs 0) and `process_topology` fails (0.0) — **pre-existing**, not caused by Phase 1.

Re-run the golden eval after each remediation commit. Any drop in pass rate is a regression for that commit.

## Working rules

- One change per commit, in the plan's order (see plan's "Suggested Commit Order").
- Run `pytest tests -q` after each commit; stop on failure.
- Before editing any symbol: run impact analysis (GitNexus MCP was unavailable in the last session — fall back to `grep` for callers and record it).
- Keep files under 500 lines (`CLAUDE.md`).
