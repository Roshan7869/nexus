# NEXUS Remediation — Execution Plan

Derived from `README.md`, the 14 change files, and `REVIEW.md` in this directory.

**Revision 2 — 2026-11-10.** Revision 1 was reviewed by three Stage 9 Goldfish
(`stage9-implementation-readiness.md`). Nine findings were confirmed against source and
merged here; the classification of every finding is in `goldfish_review_report.md`. The
load-bearing corrections: four steps named symbols that do not exist, S7 depended on a table
that was never created, S6 gated on an audit log instead of the policy table, and no step
restarted the process whose behaviour the gates test.

Method: EGM draft · EGM Stage 9 review ×3 · ponytail full · gstack plan-eng-review ·
primary-source research (§6).

---

## 1. The decision that gated everything — recorded

**G0 decision: B — the daemon stays and gains `/nexus/*` aliases.**
Decided 2026-11-10 by the assistant, acting on "go execute", because §4 was written for B and
A requires a full rewrite of the HTTP surface. This is a reversible default, not an owner
ratification: the Owner may veto at any point up to S3, and no step before S3 touches the
HTTP surface. Reverting B means deleting one alias module.

Measured:

    :8000  nexus_http_daemon.py --port 8000   PID 288888, systemd --user unit nexus-daemon
    :8080  nothing listening
    POST :8000/nexus/route  → 404        POST :8000/route → 200
    POST :8000/nexus/search → 404        GET  :8000/health → 200

`server/unified.py` defines `/nexus/route` and `/nexus/search` (lines 347, 357) and is **not
running**. The daemon that *is* running exposes `/route`, `/find_skills`, `/health`,
`/ide/list`, `/ide/register`.

Was: "the daemon's 4-field `/health` clients break" under option A. That was a prediction
sitting under a heading that claims everything was probed. No client is identified anywhere
in §7. It stays a prediction, marked as one.

### Options (for the record)

**B — daemon stays, `/nexus/*` aliases added.** (ponytail pick, adopted)
Thin alias handlers calling the same functions. One process, one port, one owner. Cost: a
~40-line alias module, and `/nexus/*` and `/route` must be proven to return identical bodies
or they become two routers.

**A — `unified.py` becomes production.** Retire the daemon, run `uvicorn server.unified:app`.
Gets the documented surface. Cost: the `/v1/*` execution-plane contract, the `/health` field
set and the systemd unit all move; C1's `create_task_run` caller at `unified.py:457` becomes
live code. *Predicted* breakage of unknown `/health` clients.

**C — leave both, do nothing.** Rejected: the steps cannot be verified against a route that
does not exist.

---

## 2. Ponytail triage — what actually gets built

14 changes were planned. Building all 14 adds roughly 4 modules, 3 endpoints, 1 table, 1
supervisor topology and 5 test files, on a codebase whose router is already 1,424 lines
against a 500-line convention.

| # | Change | Verdict | Why |
|---|---|---|---|
| A1 | single router | **cut to deletion** | `router_cutover.ROUTER_MODES` already exists (`:49-53`). A1.2 already done (`broker_runtime.py:475`). |
| A2 | one outcome API | **keep, halve** | Non-terminal guard already at `learning.py:365-392`. Real work is the second writer. |
| A3 | real verifier | **keep, shrink** | Drop `bandit` (not installed) and `devops`. Keep `coding` + `general`. |
| A4 | gate dashboard | **shrink to one endpoint** | `get_router_status()` already returns the keys. |
| B1 | project scope | **keep, needs migration 022** | Must land in `_v5_rerank`; `project_resources` does not exist (§4 S7). |
| B2 | project memory | **keep, minimal** | One query + one cache. |
| C1 | preview default | **keep** | Note: **three** call sites, not two (§4 S5). |
| C2 | real dispatcher | **cut to gating only** | No command execution. `not_executed` for everything. Blocked-set fix is the deliverable — but it must gate the policy table, not `tool_traces` (§4 S6). |
| C3 | IDE sessions | **descope unless one check passes** | `projects` has no pid/heartbeat column and no `ide_sessions` table exists, so the planned table is infeasible. Requires `project_continuity` to exist (§4 S10). |
| D1 | harness eager | **keep, retarget** | Real gate is `router_cutover.py:277`, not the two sites named in v1 (§4 S9). |
| D2 | harness prefetch | **defer** | Toolset names match 0 of 28 resource names. |
| D3 | harness ranking | **drop** | `router.py:786-792` already sorts preferred names ahead of score. |
| E1 | supervisor topology | **drop** | systemd `--user` already enforces the singleton. |
| E2 | boot sanity | **keep, minimal** | `/health/deep` `mode=ro` plus a startup check. Do not touch the two existing `/health` bodies. |

Net: **8 build · 3 shrink/retarget · 4 drop/defer.** "Execute all 8 phases" means the eight
build items; the plan runs them as 12 steps because S0, S3, S9 and S10 are scaffolding around
them (see §3).

---

## 3. Execution order

Each step is one commit, one test run, one rollback point.

    S0  test isolation            ← blocks everything, no decision needed
    S1  A2  one outcome writer
    S2  A3  verify registry (coding, general)
    S3  A1  delete duplicate router entry points      [+ G0 aliases under B]
    S4  A4  router status endpoint
    S5  C1  preview defaults (3 sites)
    S6  C2  side-effect gating + not_executed
    S7  B1  migration 022 + project boost in _v5_rerank
    S8  B2  project memory injection
    S9  D1  harness eager flag at the real gate
    S10 C3  IDE session listing (check first — may descope)
    S11 E2  /health/deep + startup sanity

Rationale:

- **S0 first.** No plan test is isolated. `mock_rw_conn` patches only
  `context_harness.cache._get_rw_conn`; `learning._get_conn` and `broker_runtime._get_conn` are
  untouched, and the leak is not theoretical: `routing_log` already holds **71 rows with
  `ide='pytest'`**. Every gate depends on this.
- **A2 before A3.** A3 changes what writes `outcome`; two disagreeing writers first produces a
  third interpretation.
- **C1 before C2.** C2 gates rows that C1 marks `previewed`.
- **A1 at S3, not S1.** A1 deletes entry points; doing it first loses the diff that proves the
  bugs were fixed.
- **S7 before S8.** Both need project scope; S8's injection point assumes `project` is already
  a first-class argument on the route path.

### 3.1 Restart protocol — v1 omitted this, and every gate in it was testing stale code

The daemon is a systemd unit; Python does not hot-reload. After any edit to `server/*.py`:

    systemctl --user restart nexus-daemon && sleep 1 && curl -s localhost:8000/health

`systemctl --user restart` is required — the system-scope unit reports `inactive` and controls
nothing.

MCP processes are **IDE-owned** (4 live: 289293, 289840, 311553, 318953). `CLAUDE.md:128`
forbids flipping `autostart=true` or killing them, so an edit to
`nexus-mcp-enhanced.py` does **not** take effect in those processes until the Owner restarts
the IDE. Therefore:

- Gates with an HTTP leg: verify live, after the restart above.
- Gates with an MCP leg: verify by importing the function in a fresh pytest process. Record the
  live-MCP leg as **pending owner restart** in the commit body — never as passing.

A gate that curls a process which has not been restarted is a gate on the previous commit.

---

## 4. Per-step contract

Format: owner · inputs · outputs · verification · rollback.

### S0 — test isolation — **EXECUTED 2026-11-10**
- **Owner** `server/learning.py:24`, `server/context_harness/cache.py:29`, `tests/conftest.py`
- **Inputs** `server/db.py:39 resolve_db_path()` already honours `NEXUS_BROKER_DB_PATH` /
  `NEXUS_DB_PATH`; `:86 get_db_path()` caches it lazily. Nothing called either.
- **Outputs** (a) both module-level `DB_PATH` constants become a call to `resolve_db_path()`;
  (b) a conftest session fixture points `NEXUS_DB_PATH` at a scratch copy **before** first
  resolution; (c) an autouse guard asserting the resolved path is not the production path.
  Three private-function monkeypatches are the wrong shape — a child process re-imports and
  bypasses all three.
- **Verification** (a) `pytest tests -q` still 490 passed / 24 skipped; (b) `SELECT COUNT(*) FROM
  routing_log WHERE ide='pytest'` is unchanged across the run — **not** `md5sum`, which is
  meaningless on a 624 MB WAL database with a live writer; (c) the suite passes in reverse file
  order: `pytest $(find tests -name 'test_*.py' | sort -r) -q`. No reverse-order plugin is
  installed (pytest 9.0.3, pytest-asyncio 1.4.0 only), so the shell `sort -r` is the mechanism.
  **The `ls` form in the original text was wrong**: `ls tests/test_*.py` is 38 files, `find` is 45,
  because seven test modules live in subdirectories (`tests/arch`, `tests/comm`, `tests/conn`,
  `tests/int`, `tests/perf`). The `ls` form collected 464 tests and reported "pass" while silently
  grading a subset — a gate that cannot fail is not a gate.
- **Rollback** revert the fixture and the constant changes.
- **Note** 71 leaked `routing_log` rows and fixture `task_runs` rows already exist in
  production. Deleting audit rows is not in scope; recorded in §7 for the Owner.
- **Result — measured, not predicted** The change is **36 call sites in 36 files**, not the two
  the plan named: 29 modules under `server/`, `core/learner.py`, and 6 test files. Four things the
  plan did not anticipate, each found by measurement rather than reading:
  1. **`core/learner.py:140`** — `self.db_path = Path(db_path) if db_path else NEXUS_DB`. A
     constructor default, invisible to any module-constant sweep, and the writer of the last two
     leaked rows through `NexusLearner()` at `server/router.py:144`, `:343`, `:1334`. Found by
     wrapping `sqlite3.connect` and printing a stack trace, after two grep passes missed it.
     A grep-based inventory of hardcoded paths is not a proof of isolation.
  2. **Import placement is load-bearing** — inserting `from server.db import ...` above a
     `from __future__ import annotations` line makes the module unimportable. Nine modules carry
     that line. The insertion point must be computed with `ast` (after the docstring, after the
     `__future__` block) and the result re-parsed before writing.
  3. **The isolation costs ~596 MB and leaked 4.38 GB** — one snapshot per run, seven runs, no
     cleanup. `atexit` removal is now registered in `conftest.py`; a run ends with zero leftover
     directories. Measured snapshot cost 3.9 s (160 MB/s); suite 104.44 s forward / 135.84 s
     reverse against a 101.30 s pre-S0 baseline, i.e. isolation is inside run-to-run noise. A
     195.72 s reading taken while other disk work ran concurrently is contamination, not cost.
  4. **`tests/conftest.py` declared `resource_policies(resource_id, policy)`** — two columns,
     against a live table of eleven with `side_effect_level TEXT NOT NULL`. `CREATE TABLE IF NOT
     EXISTS` made it a silent no-op against a copied database, so no test could ever have seen the
     side-effect column. Corrected to the live DDL; this is a precondition for S6 being meaningful.
- **Deliberate exception, recorded** 15 operator scripts under `scripts/` and `server/scripts/`
  still hardcode the production path. They are run by an operator against production on purpose;
  repointing them would silently change where a migration writes. Left as-is, listed in §7.

### S1 — A2 one outcome writer
- **Owner** `server/learning.py`, `server/broker_runtime.py`, caller `unified.py:478`
- **Inputs** confirm with one grep before editing: `learning.py:172` `TERMINAL_OUTCOMES`
  reportedly includes `skip`; `broker_runtime.py:1105` holds the review-verdict terminal set
  `{correct, wrong, partial}`. These are two different sets with the same name — resolve which
  one survives and write the constant's post-change value into the commit body.
- **Outputs** one function writing outcome + counters. `event_bridge.record_outcome` delegates
  or is removed after checking its two callers.
- **Verification** G2 as concrete SQL: a non-terminal outcome leaves `resources.times_*`
  unchanged; a terminal outcome increments `times_correct`. Assert row values, not source text.
- **Rollback** revert; the old path is one function.

### S2 — A3 verify registry
- **Owner** new `server/verify_registry.py` (<150 lines), `server/router.py:391` rename,
  `server/broker_runtime.py:1080`
- **Inputs** measured suite cost **89.46 s** (490 passed, 24 skipped)
- **Outputs** `verify(task_type)` → `correct|wrong|skip` from a real exit code. `coding` runs a
  subset; `general` runs `compileall`. Timeout 180 s. No bandit, no supervisorctl.
- **Verification** G5 — `verify('general','.')` equals a hand-run `compileall` exit code.
  G4 — **both** `_evaluate_outcome` call sites are gone: `router.py:995` (`route_task`) and
  `router.py:1265` (`_log_decision`). v1 checked only `route_task` source, so the second
  self-grade survived while the gate was green. Assert an `outcome_source` value on the written
  row, not the absence of a string.
- **Rollback** revert; `_evaluate_outcome` survives as `_evaluate_outcome_ab_only`.
- **Note** the old "120 s timeout always fires" premise is false: 120 s > 89 s. The subset is
  justified by cost, not by a timeout that never happens. Golden routing pass rate is expected
  to move; record old and new with intervals (§6).

### S3 — A1 one router path
- **Owner** `server/router_cutover.py`, `server/unified.py`, `server/nexus_http_daemon.py`
- **Inputs** `ROUTER_MODES` (`router_cutover.py:49-53`)
- **Outputs** one function behind HTTP, MCP and broker. Under B the daemon gains `/nexus/route`
  and `/nexus/search` aliases; duplicate entry points removed.
- **Verification** G1 — HTTP `/route`, MCP `find_skills` and broker `route_for_task` return the
  same top-3 for 10 fixed tasks. Assert `result["skills"]` — `router_cutover.py:84,115,154`
  return `"skills"`, so the existing test's `result["results"]` compares two empty lists.
- **Rollback** revert; aliases are additive under B.

### S4 — A4 status endpoint
- **Owner** `server/unified.py` or the daemon (per G0 = daemon)
- **Outputs** one `GET` returning `get_router_status()` + `get_feedback_coverage()` under their
  real key names. v1 gated it on `get_learning_config().is_enable_dashboard()`; no such accessor
  exists at `core/config.py:48` — add the accessor and name the route path, or drop the gate.
- **Verification** 200 with an exact key set. A 500 must fail the test: assert keys, never
  `!= 404`.
- **Rollback** remove the route.

### S5 — C1 preview defaults
- **Owner** **three** sites, not two: `server/broker_runtime.py:555`, `server/nexus-mcp-enhanced.py:806`,
  `server/unified.py:156`. `orchestrator.py:357` already defaults `False`; leave it.
- **Outputs** `auto_execute` defaults `False` everywhere. Callers passing it explicitly
  (`unified.py:457`, `nexus-mcp-enhanced.py:809`) keep working.
- **Verification** G8 — `POST` to the autopilot route **with no `auto_execute` field** returns
  `status: previewed` and creates zero new `tool_traces` rows. The MCP leg is pending owner
  restart (§3.1).
- **Rollback** flip all three back.

### S6 — C2 side-effect gating
- **Owner** `server/broker_runtime.py` — `_resource_row:433`, `_policy_allows:447`. v1 named
  `server/dispatch.py` and `_invoke_tool`; dispatch is a hook bus and `_invoke_tool` exists
  nowhere in the tree.
- **Inputs** the **policy** table, not the audit log. Measured:

      resource_policies.side_effect_level   read 2134 · local_write 482 · operational 8 · write 0
      resources 3254; 630 have no policy row at all
      tool_traces.side_effect_level         read 147 · write 6 · local_write 2   (155 historical traces)

  v1 saw `write 6` in `tool_traces` and wrote `BLOCKED = {write, local_write}`. `write` occurs
  **zero** times in the table the gate actually runs over, and `operational` — 8 rows — is not
  in that set, so it passes. The gate as written admits the only non-read policy class that
  exists.
- **Outputs** `ALLOWED = {"read"}`; everything else denies, including NULL and unknown. The 630
  policy-less resources therefore deny — correct for a change whose deliverable is "nothing
  executes". `_policy_allows` already returns `False` for a falsy row; keep that and make the
  NULL case explicit rather than relying on `mode_allowance or "both"`.
  Commands return `{"status": "not_executed", "reason": "dispatch_not_implemented"}`. No
  `subprocess`, no `echo` stub.
- **Verification** new `tests/test_side_effect_deny.py` — a `local_write` row is denied, an
  `operational` row is denied, a resource with no policy row is denied, and only `read` is
  allowed. Plus a startup assertion over
  `SELECT r.id FROM resources r LEFT JOIN resource_policies p ON p.resource_id=r.id WHERE
  p.side_effect_level IS NULL OR p.side_effect_level <> 'read'` that logs the deny count to
  stderr — it must not raise, or the service will not boot with 3238 denials.
- **Rollback** restore the allowed set; nothing executes either way.

### S7 — B1 migration 022 + project boost
- **Owner** `db/migrations/022_project_resources.sql` (new), `router_v2/v5.py:308` `_v5_rerank`,
  `:408` `unified_route_v5` (signature), `router_cutover.py:130` (call site)
- **Inputs** `B1-project-scope.md:26-33` supplies the DDL; v1 referenced the table without ever
  creating it and §7 records `021` as the last migration.
- **Outputs**

      CREATE TABLE IF NOT EXISTS project_resources (
          project      TEXT    NOT NULL,
          resource_id  INTEGER NOT NULL REFERENCES resources(id),
          priority     INTEGER NOT NULL DEFAULT 50,
          PRIMARY KEY (project, resource_id)
      );
      CREATE INDEX IF NOT EXISTS idx_project_resources_project ON project_resources(project);

  Applied via the repo's existing migrator, after `db/nexus.db.backup`. Bounded additive boost
  on project-matched candidates only. No hard filter. Wrapped in `except sqlite3.Error` so a
  missing table degrades to no boost rather than a 500.
- **Verification** G7 non-vacuous: seed 3 project rows and 3 non-project rows, assert project
  rows outrank lower-scoring non-project rows and the list is not truncated. G6 — `project="default"`
  returns 400 on the HTTP path. **Revision:** v1 required 400 on `default`, but `default` is the
  live default at `unified.py:145`, `nexus_http_daemon.py:300`, `nexus-mcp-enhanced.py:79` and
  `:806`, and tests at `:419`/`:449` pass it. Rejecting it breaks those. G6 now asserts 400 only
  for an **empty or unknown** project, and `default` keeps working.
- **Rollback** set the boost constant to 0; the table is additive and harmless.

### S8 — B2 project memory
- **Owner** `server/learning.py`
- **Outputs** `get_project_context(project)` — two queries, `mode=ro`, 4 KB hard cap, 60 s
  per-project cache. v1 injected after "`repo_context` normalisation at `broker_runtime.py:560`";
  `route_for_task(task, project, max_results, *, flag, open_session, ide)` has no `repo_context`
  and `:560` is not that. Inject inside `route_for_task` (`broker_runtime.py:475`) where `project`
  is already bound.
- **Verification** payload never exceeds 4 KB with 10 maximal notes (current worst case ~18 KB);
  route latency +<5 ms p95 warm.
- **Rollback** return `""`.

### S9 — D1 harness eager
- **Owner** `server/router_cutover.py:277` — `eager = os.environ.get("NEXUS_HARNESS_EAGER", "false")`
  inside a bare `except Exception: pass`. v1 named `core/config.py` `is_harness_eager()` (does not
  exist) and `execution_plane.py:183` (an env echo inside `health_v1()`, not a gate).
- **Outputs** a real `is_harness_eager()` in `core/config.py` read by `:277`, default **true**
  per D1, and the bare `except: pass` narrowed so a failure to attach is visible rather than
  silent. Response already carries `harness_plan`, `toolsets`, `harness_tools`,
  `harness_channels`, `harness_intent` — G9 asserts those names; there is no `harness` key and
  there never was.
- **Verification** G9 must assert an artifact **produced by this request** — the keys are
  attached unconditionally via `attach.py:24-28`, so the gate is green today and would stay
  green after a revert. Benchmark with `open_session=False` against a copied DB.
- **Rollback** flip the default to false.

### S10 — C3 IDE sessions — check before building
- **Owner** `server/ide_manager.py`, `/ide/list`
- **Blocker** `projects` is `(name, git_root, last_ide, last_active_at, note)` — no pid, no
  heartbeat — and `attach()` UPSERTs per project, so two MCP processes cannot both appear and a
  killed one never expires. `ide_sessions` does not exist. v1's table was infeasible.
- **Outputs** either (a) a union with whatever `project_continuity` actually persists, if that
  table exists — verify with one `sqlite_master` query first; or (b) **descope C3**, because a
  four-store approach is pure cost.
- **Verification** (a) two MCP processes both appear and a killed one expires within the
  staleness window; (b) n/a.
- **Rollback** remove the union.

### S11 — E2 boot sanity
- **Owner** new `run_sanity()` in `server/health.py`, route on the daemon (G0 = B)
- **Outputs** `GET /health/deep` → `{all_pass, checks[]}`, 200 pass, **503** fail. Startup check
  in `nexus-mcp-enhanced.py` before `mcp.run` (`:1759`) writing to **stderr** — stdout is the
  stdio channel. DB opened `mode=ro`.
- **Verification** G10 — `all_pass: true`; and 503 with a forced-false check. Existing `/health`
  on both servers unchanged; `tests/test_unified.py:244-248` green.
- **Rollback** delete the route and the startup call.

---

## 5. Gates that had to be rewritten

The README's G1–G10 were not all safe as written. These passed without checking anything:

    G1  reads result["results"]; route_for_task returns "skills"   → two empty lists compare equal
    G7  `assert a <= b if "X" in base else True`                   → passes when X is absent
    A4  `assert status != 404`                                     → passes on a 500
    G9  asserts keys attached unconditionally by attach.py:24-28    → green before and after the change
    G4  checks only route_task source; _evaluate_outcome also runs at router.py:1265
    G2  would assert the name of a constant that is being changed, not its effect

Rule for this plan: **every gate asserts a value produced by the change, never the absence of
an error code.** A gate that can pass on an empty result, or that is already green before the
change, is not a gate.

---

## 6. Baseline and benchmark gates

    suite            490 passed, 24 skipped, 89.46 s      (measured 2026-11-10)
    golden routing   pass 0.600 · skill 0.500 · cluster 0.750   (105 cases, at c8782ad and 6a3abaa)
    ops              cluster_divergence FAIL (1326 vs 0), process_topology FAIL (0.0) — pre-existing

Per-commit gates:

1. `pytest tests -q` — never below 490 passed.
2. **No new production writes from the suite.** Before and after each run compare
   `SELECT COUNT(*) FROM routing_log WHERE ide='pytest'`. Unchanged, not "small". v1 used
   `md5sum db/nexus.db`, which cannot work: the database is 624 MB in WAL mode with a live
   daemon writing to it, so the hash moves regardless of what the tests do.
3. Golden eval re-run. **Movement under ~9 points is noise.** 0.600 on n=105 has a 95% Wilson
   interval of [0.504, 0.689], half-width 9.2 points (Miller 2024, arXiv:2411.00640). The first
   score whose *lower* bound clears 0.600 is 73/105 = 0.695, i.e. +10 cases. A shift ≤9 points
   must not block a commit or be called a regression. Always report the interval.
4. `ops` gates stay at their pre-existing failures — no new failure, and no old one "fixed" by
   accident and claimed as progress.

Evidence: `RESEARCH-primary-sources.md` in this directory. The load-bearing arXiv IDs were
re-resolved against `arxiv.org/abs` on 2026-11-10 and match. Gate 3 → §1.3. Gate 1 → §3.4:
one pytest run is not evidence of correctness — 59% of Python flaky tests are order-dependent
(Gruber, Lukasczyk, Kroiß & Fraser, ICST 2021, arXiv:2101.09077). Gate 4 → §2.1: no non-trivial
proxy is unhackable (Skalse et al., NeurIPS 2022, arXiv:2209.13085), which is why the golden set
cannot be the only measure.

---

## 7. Evidence appendix — probed, not assumed

| Claim | Probe | Result |
|---|---|---|
| Daemon owns :8000 | `ss -ltnp` | PID 288888, `nexus_http_daemon.py --port 8000` |
| :8080 is dead | `curl :8080/health` | 000 |
| `/nexus/route` missing | `curl -X POST :8000/nexus/route` | 404 |
| `/route` works | `curl -X POST :8000/route` | 200 |
| systemd owner | `systemctl --user is-active nexus-daemon` | active (system scope: inactive) |
| suite cost | `pytest tests -q` | 490 passed, 24 skipped, 89.46 s |
| bandit absent | `python -m bandit --version` | No module named bandit |
| `plan_toolsets` shape | source | `-> list[str]` |
| migrations dir | `ls` | `db/migrations` (021 last); no `server/migrations` |
| MCP processes | `ps` | 4 live: 289293, 289840, 311553, 318953 (change files cite stale PIDs) |
| `/health` sites | grep | `unified.py:1008`, `nexus_http_daemon.py:352`, `core/server.py`; none in `execution_plane.py` |
| plan test files | `ls tests/` | all 5 missing |
| MCP autostart rule | `CLAUDE.md:128` | present |
| CLAUDE.md port | `CLAUDE.md:38,66,123` | says :8080 — contradicts `supervisord.conf:48` (`--port 8000`) |

Probed during the Stage 9 review:

| Claim | Probe | Result |
|---|---|---|
| `project_resources` | `sqlite_master` | **absent** |
| `ide_sessions` | `sqlite_master` | **absent** |
| `tools` | `sqlite_master` | **absent** |
| policy side-effect classes | `GROUP BY` | read 2134 · local_write 482 · operational 8 · **write 0** |
| resources without a policy row | `LEFT JOIN` | **630** of 3254 |
| `tool_traces` classes | `GROUP BY` | read 147 · write 6 · local_write 2 (historical audit log) |
| suite already leaked to prod | `routing_log WHERE ide='pytest'` | **71 rows** |
| `_invoke_tool` | grep | 0 hits |
| `is_enable_dashboard` | grep | 0 hits |
| `is_harness_eager` | grep | 0 hits |
| `isolated_home` | grep | 0 hits |
| eager gate | source `router_cutover.py:277` | inline `NEXUS_HARNESS_EAGER`, default `false`, inside `except: pass` |
| `route_for_task` signature | source `broker_runtime.py:475` | `(task, project, max_results, *, flag, open_session, ide)` — no `repo_context` |
| policy functions | source | `_resource_row:433`, `_policy_allows:447` |
| pytest plugins | `pip list` | pytest 9.0.3, pytest-asyncio 1.4.0 — no timeout, no random-order |
| `_evaluate_outcome` callers | grep | `router.py:995`, `router.py:1265` |
| `auto_execute` defaults | grep | `broker_runtime:555`, `mcp:806`, `unified:156`, `orchestrator:357` (already False) |
| `projects` schema | `PRAGMA table_info` | `(name, git_root, last_ide, last_active_at, note)` — no pid/heartbeat |

Probed during S0 execution, after the change:

| Claim | Probe | Result |
|---|---|---|
| hardcoded-path inventory | sweep every `^DB_PATH =` | 37 resolver-backed, 19 still hardcoded |
| are those 19 real leaks | path inspection | 15 operator scripts + 4 repo-root scratch tests not collected by `pytest tests` |
| root scratch tests | patch + `ast.parse` | repointed anyway — a bare `pytest` at repo root would collect them |
| isolation holds under trace | `sqlite3.connect` wrapper | `=== production opens: 0 ===` |
| forward suite, idle disk | `pytest tests -q` | 490 passed, 24 skipped, **104.44 s** (wall 110 s) |
| reverse suite, `find` order | `pytest $(find tests -name 'test_*.py' \| sort -r)` | 490 passed, 24 skipped, **135.84 s** |
| gate 2 | `SELECT COUNT(*) FROM routing_log` | **10359 before and after** — unchanged |
| gate 2, second signal | `WHERE ide='pytest'` | **71**, unchanged since before S0 |
| residue already in prod | `10350 → 10355 → 10357 → 10359` | 9 leaked rows, none deleted |
| snapshot cost | one `backup()` to scratch | **3.9 s**, 624 MB (160 MB/s) |
| snapshot leak | `du -sh` on 7 run dirs | **4.38 GB** — `atexit` cleanup added; verified 0 dirs after a run |
| conftest schema drift | `PRAGMA table_info` vs conftest DDL | 11 columns live, **2 declared** — corrected |
| reverse-order collection | `ls` vs `find` | 38 vs **45** files, 464 vs **490** tests |

The nine leaked rows are test traffic written before isolation existed. They are audit rows in a
live table with a concurrent systemd writer; deleting them is an Owner decision, not a cleanup
step. The rollback path is the 624 MB snapshot, not a `DELETE`.

Rollback point: `~/.hermes/cache/scratch/nexus.before-pytest.db` (624 MB, 2026-11-10 19:18).
That directory is pruned after 24 h idle — copy it to `db/backups/` before S7 runs the
migration. `db/backups/` is also where `db/nexus.db.backup` belongs.

---

## 8. Risks

1. **G0 is adopted, not ratified.** §1 records it as a reversible default. If the Owner wants
   A, S3 changes and nothing else does — which is why S3 runs late.
2. **A3 moves the golden number, and the move will probably be invisible.** Removing
   self-grading will likely lower 0.600. At n=105 the interval is ±9.2 points, so anywhere from
   0.51 to 0.69 is the same measurement. Say so in the commit or the next session calls noise a
   regression and restores the self-grade.
3. **S6 will deny ~3,238 resources.** That is the intended fail-safe direction, but it is a
   behaviour change large enough to look like an outage. Land it with the deny count in the
   commit body.
4. **B1's boost is invisible until `project_resources` is seeded.** Seeding is in scope.
5. **C2 is a gate, not an executor.** It will read as a regression to anyone expecting dispatch
   to work. Put it in the commit title.
6. **Router size.** `router.py` 1,424 lines, `v5.py` 596, `fusion.py` 591 — all over the
   500-line convention, and A1/B1 touch all three. Splitting them is a separate plan; this plan
   records the exception rather than silently ignoring it.
7. **The suite has written to production.** 71 `routing_log` rows and fixture `task_runs` rows
   are in `db/nexus.db`. S0 stops it; removing what is already there needs an owner decision.
