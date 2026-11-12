# Stage 9 — Implementation Readiness Review (Goldfish)

**Reviewer:** egm-implementation-readiness Goldfish
**Target:** `docs/plans/remediation/EXECUTION.md` (20,570 chars, all sections S0–S11)
**Rubric:** `resources/document_standards/implementation-readiness-standard.md` (Zero-Decision Rule, target scope, actionable steps, test plan) + `agents/implementation_readiness.md` (REVIEW mode)
**Date:** 2026-11-10
**Repo state at review:** branch `second-brain-v2`, HEAD `6a3abaa`, 174 dirty entries (79 untracked, 64 deleted, 31 modified). Live daemon PID 288888 (`server/nexus_http_daemon.py --port 8000`), four `server/nexus-mcp-enhanced.py` processes, `db/nexus.db` 624 MB, `journal_mode=wal`, `pytest --collect-only` = 514 tests.

---

## Verdict up front

The plan is **not executable as written**. The structure is sound — verified targets, real measured baselines in §7, honest acknowledgement of prior gate defects, one-commit-per-step discipline — but four of the eleven steps are addressed to the wrong symbol or the wrong file, and five gates pass without the change being made. The failure mode this plan is most exposed to is exactly the one it is trying to cure: *a gate that passes while the behaviour is unchanged.*

Specifically:

- **S5** flips a default that both live callers override. Gate G8 cannot reach the flipped code.
- **S9** edits two sites that are inert. Gate G9 **already passes today** with the flag false.
- **S6** names a file that contains no enforcement, and gates on a test that does not exist.
- **S7** depends on a table that does not exist and a migration that is not in the plan.
- **S10** cannot be built on the data source it names.
- **S2** removes one of two self-grading call sites, so G4 passes with self-grading still in place.

Ten BLOCKERs, eighteen MAJORs. Do not start S0 until B1–B10 are resolved; most are 1–5 line edits to the plan text, not to the design.

---

## Method

Every factual claim in the plan that could be checked was checked against source, the live database (read-only URI `file:/home/roshan/nexus/db/nexus.db?mode=ro`), the live process table, and the installed package set. Line numbers in the plan were re-read against current files (the plan text changed size during the review, 20,376 → 20,570 chars, so all citations were re-validated). The two arXiv IDs were resolved: 2101.09077 = *An Empirical Study of Flaky Tests in Python*; 2411.00640 = Miller, *Adding Error Bars to Evals* — both correct.

---

## BLOCKERS

### B1 — S5 flips a default that both live callers override; G8 cannot pass as specified
**Location:** EXECUTION.md S5 (lines 170–175), gate G8.
**Evidence:** Three defaults exist, not one. `server/unified.py:156` — `auto_execute: bool = True` on `AutopilotRunRequest`, forwarded unchanged at `server/unified.py:464`. `server/nexus-mcp-enhanced.py:806` — its own default `True`, forwarded at `:816`. `server/broker_runtime.py:555` — `auto_execute: bool = True`, reachable only when the caller omits the field. The plan names `broker_runtime.py:555` and `mcp:806`, and omits `unified.py:156` — the default that actually governs `POST /nexus/autopilot`.
**Why it matters:** flip only the two named sites and the HTTP path still auto-executes. G8 ("both surfaces create the run and do not execute") will then fail on the HTTP call and pass on MCP, and the implementer has no rule for which one is authoritative. This is the same class of defect §5 was written to catch.
**Fix:** list all three sites with line numbers, including `unified.py:156`. State that `broker_runtime.py:555` is defence-in-depth (unreachable once both callers pass `False` explicitly). Write G8 as: `POST /nexus/autopilot {"task": "...", "project": "default"}` with **no** `auto_execute` field → 200, `task_run` row created with `status='pending'`, and zero `tool_traces` rows added for that run.

### B2 — S9's two named sites are inert; G9 already passes today
**Location:** EXECUTION.md S9 (lines 213–219), gate G9.
**Evidence:** `server/execution_plane.py:183` is an entry in the `flags` dict of `health_v1()` (defined `:157`, docstring "GET /v1/health payload") — it reports `os.environ.get("NEXUS_HARNESS_EAGER", "false")` read-only. `core/config.py` contains **no** `is_harness_eager` (grep over `core/` and `server/` returns nothing; `LearningConfig` carries `thompson_routing`, `cluster_routing`, `reflection_enabled`, `mcp_sync`, `auto_invoke_enabled`, `min_samples_thompson` — no harness flag). The live gate is `server/router_cutover.py:277`: `os.environ.get("NEXUS_HARNESS_EAGER", "false").lower() == "true"`. Separately, `server/harness_engine/attach.py:24–28` sets `harness_plan`, `harness_tools`, `harness_toolsets`, `harness_channels`, `harness_intent` **unconditionally** — the `if eager:` guard at `:30` only adds extra plan detail.
**Why it matters:** G9 asserts five result keys are present. They are present in every response today, with eager off. The gate is green before the first edit and stays green if the edit is skipped or wrong. It measures nothing.
**Fix:** name `router_cutover.py:277` as the decision site; add `is_harness_eager()` to `core/config.py` (signature, env var, default) and call it there. Rewrite G9 to assert a *behavioural* difference: with eager on, the response contains at least one `harness_*` artifact whose `fetched_at` is from this request (e.g. `len(result["harness_tools"]) > 0` or a non-null artifact id); with eager off, the same field is empty. Key-presence is not a gate.

### B3 — S6 names a file with no enforcement and a test that does not exist
**Location:** EXECUTION.md S6 (lines 177–190), gate C2/G6.
**Evidence:** `server/dispatch.py` is a hook bus (`register`/`emit`/`waterfall`/`serial`); its test `tests/test_dispatch.py` asserts pass-through identity of a dict through the hook chain. There is no `_invoke_tool` anywhere in the repo. `grep -rn "policy_allows\|side_effect" tests/*.py` returns **nothing** — no test exercises `_policy_allows` or side-effect gating. `external_write` appears in exactly one place, `server/broker_runtime.py:55` (the godmode allow-list); no test constructs it.
**Why it matters:** the step says "the current test passes only because it constructs a value (`external_write`) that cannot occur." That test does not exist. An implementer arrives at `dispatch.py`, finds a hook bus, and must invent where enforcement lives — a design decision, which the Zero-Decision Rule forbids. The gate C2 then has nothing to turn red.
**Fix:** name the real owner: `server/broker_runtime.py` `_policy_allows` (~`:455–465`) and `_resource_row` (~`:430–450`). Give the exact diff: replace `side_effect or "read"` with an explicit deny on `NULL`; define the blocked set as `{external_write, write}`. State where the boot assertion lives (module-level in `broker_runtime`, imported by both daemon and MCP) and name the new test file (`tests/test_side_effect_deny.py`) with the three cases: NULL denied, `write` denied, `read` allowed.

### B4 — S6's default-deny misses 630 resources entirely
**Location:** EXECUTION.md S6 step 3 ("default deny") and step 4 (boot assertion).
**Evidence:** read-only query — `resources` = 3,254 rows; `resource_policies` = 2,624 rows; `resource_policies.side_effect_level IS NULL` = 0; distribution `read` 2,134 / `local_write` 482 / `operational` 8 — and **no `write` row and no `external_write` row at all**. `_resource_row` joins policies, so 630 resources (3,254 − 2,624) resolve to a NULL `side_effect_level`, and `_policy_allows` currently coerces NULL to `"read"`.
**Why it matters:** the step asserts "every tool row has non-NULL side_effect_level, so a NULL → deny is safe." Against `resources` that is false by 630 rows; against `resource_policies` it is true. Reading the plan literally, the implementer writes the assertion against `tool_traces`, sees it pass, and ships a default-deny that leaves 19% of resources admitted as read.
**Fix:** state the assertion against `resources LEFT JOIN resource_policies WHERE p.side_effect_level IS NULL` and give the required action for the 630: either a migration row per resource (`local_write` default) or an explicit documented deny. Pick one; do not leave it to the implementer.

### B5 — S6's input measurement is taken from the wrong table
**Location:** EXECUTION.md S6, "Measured: tool_traces.side_effect_level read 147 / write 6 / local_write 2".
**Evidence:** those numbers are `tool_traces` — 155 historical traces. The policy table that feeds `_policy_allows` has 2,624 rows and no `write` level. The `write 6` in traces cannot be produced by any current policy row.
**Why it matters:** the plan sizes a security change off 155 execution records while the gate it writes runs over tool configuration. The counts disagree with the DDL they describe, so the "measured" evidence invites the implementer to trust the wrong surface.
**Fix:** report both: traces 147/6/2 (historical behaviour) and policies 2,134/482/8 (current configuration), and state that the gate runs on the second.

### B6 — S2 leaves one of two self-grading call sites in place; G4 passes anyway
**Location:** EXECUTION.md S2 (lines 137–149), gate G4 ("`route_task` source contains no `_evaluate_outcome(`").
**Evidence:** `_evaluate_outcome` is called from two places in `server/router.py`: `:995` inside `route_task` (def `:910`) and `:1265` inside `_log_decision` (def `:1252`). `_log_decision`'s own docstring lists `correct/wrong/partial/skip` as the terminal values it writes.
**Why it matters:** G4 inspects one function's source text. The self-grade at `:1265` survives untouched, so "no self-grading" (A3) is not achieved while the gate is green — precisely the "appears to succeed while doing nothing" failure. It also leaves two writers, which S2 explicitly forbids.
**Fix:** name both call sites and both removals in the step. Replace G4's string check with a value assertion: after one routed task, `SELECT outcome_source FROM <table> WHERE <key>` must equal the constant defined in §5 (e.g. `'router'`) and there must be exactly one row per task.

### B7 — S1 and S2 disagree about `skip`, so G2 tests the wrong thing
**Location:** EXECUTION.md S1 (terminal set `{correct, wrong, partial}`) and S2 (`verify()` returns `skip`), gate G2.
**Evidence:** `server/learning.py:172` — `TERMINAL_OUTCOMES = {"correct", "wrong", "partial", "skip"}`. The plan's three-value set is `broker_runtime.py:1106`, which is the *review verdict* set, not the learning terminal set. `learning.py:229` special-cases `skip` only inside `_count_cooccurrence`, i.e. `skip` is terminal and does touch other counters.
**Why it matters:** S2 emits `skip`, S1 says it is non-terminal. Either `skip` mutates beliefs (contradicting G2's "non-terminal leaves counters unchanged") or it is silently dropped (contradicting `learning.py:172`). The implementer must choose, and the gate cannot distinguish the two outcomes.
**Fix:** define `skip` explicitly in S1 — state the post-change value of `TERMINAL_OUTCOMES` — and write G2 as a concrete SQL assertion: `resources.times_*` for the target resource unchanged after a `verify()` that returns `skip`, plus the same assertion for `partial` with the expected delta.

### B8 — S10 cannot be built on the data source it names
**Location:** EXECUTION.md S10 (lines 221–227), "No `ide_sessions` table — reuse `project_continuity`".
**Evidence:** `projects` is `name TEXT PRIMARY KEY` with `git_root`, `last_ide`, `last_active_at`; `attach()` UPSERTs on the project name. Five projects at `~/.hermes/sessions/*` map to one row each. No pid, no port, no session id, no heartbeat. `IDESession` has a `pid` field, but `IDEManager` holds sessions in an in-process dict, so `/ide/list` (daemon `:872` → `get_dashboard`) can only see sessions of *its own* process.
**Why it matters:** the verification is "start two MCP processes, both appear" and "kill one, it disappears within the staleness window." Neither is representable: two processes on the same project collapse into one row (last writer wins on `last_ide`), and nothing expires because nothing heartbeats. The plan forbids the only workable design ("No `ide_sessions` table") while claiming reuse.
**Fix:** either (a) add a table — `ide_sessions(session_id TEXT PK, project TEXT, pid INTEGER, started_at, heartbeat_at)` written on `ide_event` and read with `heartbeat_at > now-90s`, and change the verification to two distinct pids on one project; or (b) drop S10 and keep `/ide/list` as a single-process view. State which.

### B9 — S7 depends on a table that does not exist, and the migration is missing from the step
**Location:** EXECUTION.md S7 (lines 192–202), line 195 "`project_resources` — currently unpopulated".
**Evidence:** `sqlite_master` has no `project_resources`. The plan's own source file says so: `docs/plans/remediation/B1-project-scope.md:8` — "No `project_resources` table exists" — and `:26–33` gives `db/migrations/022_project_resources.sql`. §7 line 293 records `db/migrations` with "021 last" (actual tail: 020, 021, BASELINE.sql), so no step in EXECUTION creates 022.
**Why it matters:** the implementer follows S7, writes `SELECT ... FROM project_resources`, and `_v5_rerank` raises on every route — an uncaught exception in the hot path, not a degraded boost. Also S7 says "seeding is part of this change" for a table with no schema and no seed source: a design decision.
**Fix:** add an explicit step before the code change: write `db/migrations/022_project_resources.sql` (DDL quoted inline from B1:26–33), back up with `sqlite3 db/nexus.db ".backup ..."`, apply, verify the table exists and is empty. State the seed source (where the project→resource rows come from) or say "table stays empty; boost is a no-op until seeded." Wrap the query in `except sqlite3.Error: return scores` so a missing table degrades instead of 500-ing.

### B10 — G6's 400 contradicts the design and the existing callers
**Location:** EXECUTION.md S7 gate G6 and §5's gate-defect analysis.
**Evidence:** `project="default"` is the default in `AutopilotRunRequest` (`unified.py:145`), `RouteRequest` (`daemon:302`), MCP `find_skills` (`:79`) and `autopilot_run` (`:806`); `tests/test_unified.py:419,449` pass it explicitly. S7's own outputs say "No hard filter — ranking only".
**Why it matters:** G6 says a request with `project="default"` must return 400. That breaks every default caller and directly contradicts S7's outputs. It also breaks S5's verified path (G8 uses the default project), so the plan would go green at S5 and red at S7 with no rule that earlier gates must be re-run.
**Fix:** delete G6 or restate it as `project=""` → 400 with body `{"error":"project required"}`. If the latter, add that validation rule and status code to S7's outputs, and add `project="all"` handling too (currently undefined).

### B11 — S3's "identical bodies" is unachievable with the two request models
**Location:** EXECUTION.md S3 (lines 151–160), §1 line 38.
**Evidence:** two different models — `unified.RouteRequest{task, project, max_results}` (`:143–146`) vs `daemon.RouteRequest{task, project, use_embedding}` (`:300–303`). The live `/route` calls `route_for_task(..., max_results=10)` and appends `latency_ms` / `router_flag` (`router_cutover.py:270–271`); `/nexus/route` passes `req.max_results` and returns the broker result alone. `/nexus/search` is not a router at all — `unified.py:357–372` calls `find_matching_skills` and returns `{"query", "method": "keyword_match", "results": [...]}`.
**Why it matters:** "both paths return identical bodies for the same task, project and mode" cannot hold across two models with different fields and different post-processing. The implementer must choose which body wins — a design decision — and the `/nexus/search` alias, if kept, reintroduces the very `results` vs `skills` key trap §5 warns about.
**Fix:** specify the alias contract field by field (one request model, chosen fields, whether `latency_ms`/`router_flag` survive), and either drop `/nexus/search` from the alias set or give it its own row that maps to `/find_skills` + `find_matching_skills` with its documented response schema.

### B12 — S3's "duplicate entry points removed" has no list, and the obvious candidates are load-bearing
**Location:** EXECUTION.md S3 step 2.
**Evidence:** `ROUTER_MODES` (`router_cutover.py:45–53`) maps `"0" → "route_task"`, and `get_router_flag()` falls back to `"0"`. `route_task` has live callers at `tool_session_broker.py:289`, `rl_feedback_loop.py:383`, `orchestrator.py:103` and `:294`, `router_cutover.py:82` (the retry path), and `scripts/eval_faiss.py:35,48`. `unified_route_v5` and `route_for_task` are separate again.
**Why it matters:** read literally, "remove duplicates" deletes a function with six callers and the mode-0 rollback path the same plan preserves; read conservatively, the step does nothing. Either way the implementer has made an architectural choice.
**Fix:** enumerate the exact symbols to delete with `file:line`, and for each caller state its replacement. If the intent is only to remove the `/v1/*` shims, say so and list the shim paths.

### B13 — No phase restarts the daemon or the MCP processes, and every phase edits import-time code
**Location:** all of S3–S6, S9, S11; §6 rollout.
**Evidence:** systemd unit runs `venv/bin/python3 server/nexus_http_daemon.py --port 8000 --host 127.0.0.1`, `WorkingDirectory=/home/roshan/nexus`, `Restart=always`, `RestartSec=5`, `StartLimitInterval=60s`, `StartLimitBurst=3`, no `--reload`. Live PID 288888, uptime 6,474 s. Four `nexus-mcp-enhanced.py` processes are serving IDEs.
**Why it matters:** FastAPI routes and module-level constants are bound at import. Every verification curl in the plan hits the *old* code until the unit is restarted, so gates G8/G9/G10 will fail against code that is correct on disk — and the natural debugging response is to "fix" correct code. Conversely a stale green is possible for gates that read files.
**Fix:** add to each phase that changes server code: `systemctl --user restart nexus-daemon`, assert `systemctl show -p MainPID` changed and `/health` `uptime_seconds` reset, then run the gate. Add a note that each of the four MCP clients must be restarted by its IDE to pick up `nexus-mcp-enhanced.py` changes, and that the MCP-side gate must be run after that.

### B14 — S6's boot assertion plus `Restart=always` is a crash-loop hazard
**Location:** EXECUTION.md S6 step 4 ("assert at boot that every tool row has non-NULL side_effect_level"), S6 rollback.
**Evidence:** the unit restarts on any exit; `StartLimitBurst=3` within 60 s moves it to `failed`. If the assertion raises at import, the daemon retries three times and then stays down — taking out every HTTP route and every IDE tool server.
**Why it matters:** a data condition in a 3,254-row table becomes an availability incident. And S6's rollback ("set the blocked set back") does not disable the assertion, so the rollback path leaves the system unstartable.
**Fix:** state the fail behaviour explicitly. Preferred: log the violation to stderr and continue, with the count exposed on `/health`. If a hard failure is required, gate it behind `NEXUS_BOOT_STRICT_TOOLS=1` defaulting off, and add "remove the assertion" to S6 rollback.

### B15 — The suite-mutates-production leak is real, and gate 2 cannot detect it
**Location:** EXECUTION.md §6 gate 2 (md5 of `db/nexus.db` before/after) and S0's isolation fixture.
**Evidence:** the production DB already contains 71 `routing_log` rows with `ide='pytest'` and 8 `task_runs` rows holding fixture text (`audit routing quality`, `build a landing page`, ...). Tests have been writing to production for some time. `journal_mode=wal`; the daemon writes `routing_log` on every `/route`. `tests/conftest.py` patches `server.learning._get_conn` and friends **in-process**; `server/learning.py:24` hardcodes `DB_PATH` from `server.db`, so an env var alone does not redirect it.
**Why it matters:** md5 on a WAL database with a live writer is a two-sided false signal — a daemon checkpoint changes the hash and fails S0 for an unrelated reason; a small insert that lands in `-wal` and is checkpointed after the run may leave the main file's hash unchanged. The gate that is supposed to prove isolation is the gate that failed to catch the leak that already happened.
**Fix:** redirect the whole run: a session-scoped autouse fixture that sets `NEXUS_DB_PATH` and `NEXUS_BROKER_DB_PATH` to a temp file (subprocesses inherit env), plus the existing in-process patches for `learning._get_conn`. Replace the md5 gate with per-table counts read over `mode=ro`: `routing_log`, `task_runs`, `resources.times_*`, `user_learnings` unchanged. Clean the 71 pytest rows as a separate, explicit step if they are to go.

---

## MAJOR

### M1 — §7's evidence row contradicts S9
`§7 line 295` says "`/health` sites ... none in `execution_plane.py`"; S9 itself cites `execution_plane.py:183`, and that line is inside `health_v1()` at `:157`, whose docstring is "GET /v1/health payload". Fix the row to "`execution_plane.py:157 health_v1()` (payload builder), `:183` flag echo".

### M2 — §1's "4-field `/health`" is wrong in both places
The daemon's live payload has 12 keys (`status`, `uptime_seconds`, `embedding_model`, `embedding_load_time_s`, `db_available`, `total_requests`, `cache_hit_rate`, `cache_stats{4}`, `timestamp`); the unified app's is 2 (`status`, `version`, `unified.py:1008–1010`). Option A is costed against a payload shape that does not exist. Restate both shapes and name which fields a client could depend on before deciding A vs B.

### M3 — The isolation gate is the only guard against the DB-write leak and it is unreliable
Covered inside B15; listed separately because the fix is a design change, not a wording change. If the orchestrator accepts md5 as-is, the plan has no real isolation guarantee. Minimum acceptable: `PRAGMA wal_checkpoint(TRUNCATE)` before the before-hash and after the after-hash, daemon quiesced for the duration, plus per-table counts.

### M4 — S0's fixture does not cover subprocesses, and S2 introduces one
`monkeypatch` in `tests/conftest.py` is in-process. S2's `verify('coding')` runs the test suite and `verify('general')` runs `compileall`; whichever mechanism, a child process re-imports unpatched modules and writes the production DB. S0 must state that subprocess-spawned code inherits only the env redirection, and S2 must give the exact command with `env=`.

### M5 — S0's reverse-order verification is not runnable as written
`venv` has `pytest 9.0.3` and `pytest-asyncio` only — no `pytest-randomly`, no `pytest-random-order`; core pytest has no `--reverse`. "The same suite passes with the tests run in reverse order" needs either a dependency the plan does not name or a collection hook it does not provide. Fix: add `pytest-random-order==1.1.1` to the plan's dependency section with the exact command, or add a 10-line `pytest_collection_modifyitems` reverse hook in `tests/conftest.py` and name it as a deliverable.

### M6 — G7 is still vacuous — the defect §5 claims to have fixed
S7's gate says "assert every project row outranks every non-project row that scores lower." A row that scores lower is outranked by score alone; the boost is never exercised. Fix: seed a non-project row whose base score is strictly higher than a project row's base score but inside the boost bound, assert the order flips; and a second row outside the bound, assert the order holds.

### M7 — S7 leaves truncation, top-k and per-request cost undefined
"assert the list is not truncated" has no expected length and no `top_k`. The boost queries the policy table on every route with no cache (S8 specifies a 60 s cache for project context; S7 specifies none) — 2,624 rows per request in the hot path. State `top_k`, the exact query, the cache TTL, and the fallback when the table is missing.

### M8 — S2's signature contradicts itself and the commands are unspecified
Outputs say `verify(task_type)` returning one of three values; verification calls `verify('general', '.')`. "coding runs a subset" names no file selection, command, cwd or timeout, and does not say whether it is `subprocess` (which S6 forbids elsewhere) or `pytest.main`. State the signature, the exact command string per task type, cwd, env, and the exception→return-value mapping.

### M9 — `compileall` over `.` walks the venv and dirties the tree
`compileall` on the repo root traverses `venv/lib/python3.12/site-packages` and every vendored dir; any syntax error there makes the exit code 1, so `verify('general')` reports `wrong` permanently. It also writes `__pycache__` into the tree that "one commit per step" depends on being clean. Fix: name the paths (`server core evals`), pass `-q`, set `PYTHONPYCACHEPREFIX` to a temp dir, and state the exit-code mapping.

### M10 — S4 gates on a symbol that does not exist
`core/config.py` has no `is_enable_dashboard` (grep over `core/` and `server/` returns nothing). The plan neither adds the flag nor states the off-state behaviour (404? 200 with `enabled: false`?). Also `get_feedback_coverage(conn=None)` (`rl_feedback_loop.py:49`) opens its own connection — the endpoint must hand it a `mode=ro` connection, which the plan does not say. Fix: name the flag, env var, YAML key and default; state the off-state response; state the connection mode.

### M11 — S8's injection point is stated in two different functions
"in `route_for_task` ... after `repo_context` normalisation at `broker_runtime.py:560`" — `route_for_task` has no `repo_context` parameter (signature: `task, project, max_results, open_session, ide`); `repo_context = repo_context or {}` at `:560` is inside `create_task_run`. Fix: choose one insertion point and name the result key that carries the text.

### M12 — S8 leaves the queries, cache and measurement undefined
Two queries against which tables, which columns, what truncation point, what cache key/invalidations/thread-safety — none stated. The "current worst case ~18 KB" cannot be reproduced: `user_learnings` is 577 rows, max note 6,882 chars, 154 KB total; `project_notes` is 8 rows. §7 lists no probe for this number, unlike the other measured claims. Fix: write the two SQL statements, the truncation rule, the cache dict + lock + TTL, and the exact command that produced 18 KB.

### M13 — S8's latency gate is invalidated by S9, and no phase re-checks it
S8's gate is "route latency increase under 5 ms p95 with a warm cache", measured at S8. S9 then turns eager harness research on in the same path, which does network work. S8's number is not re-measured. This is a hidden ordering dependency: S8's gate is only valid in the window S8→S8. Fix: re-run the S8 measurement as part of S9's verification, or move S9 ahead of S8.

### M14 — S7-after-S5 breaks an earlier gate (see B10)
G6's 400 kills the default-project path that G8 verified at S5. Independent of the 400 question, the plan has no rule that each phase re-runs the prior phases' gates, so cross-phase regressions are invisible until the end. Add a "re-run gates for all completed phases" line to every phase's verification.

### M15 — S1's "check its two callers" leaves a decision, and the counters are unnamed
`event_bridge.record_outcome` exists, but no caller of `event_bridge.record_outcome` was found (only `get_bridge` imports), so "two callers" is unverified. G2/G3 name neither the table nor the columns (`resources.times_correct` vs some `resource_counters` table) nor the assertion. Fix: state keep-or-delete for `event_bridge` explicitly, list call sites with `file:line`, and give the exact SQL for each gate.

### M16 — S11 creates a module with no checks and a name that already exists elsewhere
`server/health.py` does not exist; `execution_plane.health_v1()` and the `/health` routes on both apps already exist. `checks[]` enumerates no checks and no thresholds; "503 when a check is forced false" names no forcing mechanism. Fix: enumerate each check with its pass criterion (resources > 0; zero NULL policy levels; router flag ∈ `ROUTER_MODES`; DB opens `mode=ro`; backup newer than 48 h), name the forcing mechanism (`NEXUS_HEALTH_FORCE_FAIL=<name>`), and place the module at `server/health_deep.py` to avoid ambiguity.

### M17 — S11's MCP startup check sits under `if __name__ == "__main__":`
`server/nexus-mcp-enhanced.py:1758–1759`. A server started by console script or `python -m` skips it. Fail behaviour (exit vs warn) is undefined, and exiting takes down the IDE's tool server. Fix: state the hook and that failure logs to stderr without exiting.

### M18 — S11's named regression test cannot fail
`tests/test_unified.py:244–248` asserts only that `"/health"` is in `app.routes` of the unified app; S11 adds `/health/deep` to the daemon. The test is invariant under the change. Fix: assert the route table of the process actually serving `:8000` contains both `/health` and `/health/deep`, and compare the `/health` body keys before and after.

### M19 — Rollback point is a `cp` of a WAL database, held where the harness prunes
`~/.hermes/cache/scratch/nexus.before-pytest.db` is 624,033,792 bytes taken at 19:18 while `db/nexus.db` mtime is 19:19; `journal_mode=wal`. A plain copy of the main file without `-wal` loses committed-not-yet-checkpointed pages, and a checkpoint during the copy can produce a torn image. The scratch cache prunes entries idle 24 h, so the only rollback artifact expires. Fix: `sqlite3 db/nexus.db ".backup 'db/backups/nexus-<sha>.db'"` (or `VACUUM INTO`), store under the repo not the scratch cache, record `sha256sum`, and re-take immediately before S7's migration.

### M20 — "Each step is one commit" cannot work on this working tree
HEAD is `6a3abaa`; the tree has 174 changes including 64 deletions and 79 untracked files, on branch `second-brain-v2`. Committing per step sweeps unrelated deletions into the step commits, and per-step `git revert` then removes unrelated work. Fix: state the base (clean commit or branch point), require the 174 changes to be committed or stashed first, and record the base SHA in §7.

---

## MINOR

- **N1 — §7 MCP PIDs are stale.** Plan lists `289293, 289840, 311553, 318953`; live now `289293, 289840, 318953, 342420` — `311553` is gone, `342420` is new (uptime 1,465 s). PINs in a checklist go stale; use `pgrep -f nexus-mcp-enhanced.py` and re-capture at execution time.
- **N2 — §2 line 64 "`find_matching_skills` is not on the production path" is false.** `unified.py:371` calls it for `/nexus/search`, which is part of the surface S3 wants to make authoritative. If `/nexus/search` survives as an alias, B1's retargeting claim weakens.
- **N3 — §7 "all 5 missing" test files.** README names four: `test_router_unified.py`, `test_learning_loop.py`, `test_no_fake_verify.py`, `test_project_boost.py` — all four confirmed absent. Name the fifth or say four.
- **N4 — S1's "callers at `unified.py:478`"** is `async def nexus_task_run_review` (the HTTP review route), not a `record_outcome` caller. The actual caller is `review_task_run` (`broker_runtime.py:1100`).
- **N5 — S0's "one autouse-capable fixture"** is a hedge; it is either `autouse=True` or it is not. State it as `autouse=True, scope="session"`.
- **N6 — `tests/pytest.ini` and `pyproject.toml` both define `testpaths`/config.** Running `pytest tests` from the repo root resolves rootdir to `pyproject.toml` and silently ignores `tests/pytest.ini`; if markers differ, the plan's `-m` selections behave differently than documented. Pin the invocation (`-c pyproject.toml` or `-c tests/pytest.ini`) in S0.

---

## Gate audit — would each gate actually fail if the change were wrong?

| Gate | Claim | Fails when change is wrong? | Defect |
|---|---|---|---|
| S0 isolation | prod DB unchanged | No | WAL + live writer; md5 unreliable (B15/M3) |
| S0 reverse order | suite passes | Not runnable | no plugin (M5) |
| G1 | three surfaces agree | Partly | different models/params, `results` vs `skills` in search (B11) |
| G2 | non-terminal leaves counters | No | `skip` semantics undefined (B7) |
| G3 | one writer | Partly | second call site not removed (B6) |
| G4 | no self-grade | No | source-string check on one function (B6) |
| G5 | golden ≥ 0.600 | Yes | — (0.600 baseline confirmed, 105 cases) |
| C2 | no unclassified tool rows | No | assertion over wrong table (B5); 630 NULLs (B4) |
| C2b | default deny | No | NULL→read coercion survives (B4) |
| G6 | project filter | Passes for wrong reason | 400 breaks callers (B10) |
| G7 | project boost | No | lower-scoring comparison is vacuous (M6) |
| G8 | autopilot defers | Fails on HTTP | one of three defaults omitted (B1) |
| G9 | harness keys present | **Already green** | keys unconditional (B2) |
| G10 | health/deep | No | test asserts route table of wrong app (M18) |

Nine of fourteen gates are green under a wrong implementation. That is the core readiness failure: the plan's error-detection power is roughly one third of what its structure implies.

---

## Ordering analysis (S0→S11)

Dependencies that hold: S0 must be first (nothing else can trust the suite); S1 before S2 (single writer before verify writes); S5 independent of S1–S4; S7 before S8 (boost table before context injection reads it); S8 before S9 (context before eager harness); S11 last.

Three violations:

1. **S5 → S7 (B10/M14).** S5's gate uses `project="default"`; S7's G6 rejects it. A later phase silently breaks an earlier green gate.
2. **S8 → S9 (M13).** S9 changes the performance of the path S8 gated, and S8's p95 number is never re-measured. The gate is valid only in the S8→S8 window.
3. **S7 → S6 (B9).** If S7 lands `project_resources` and S6's boot assertion sweeps "every tool/resource row," the new table's rows must satisfy S6's invariant. S6 runs before S7, so S7 introduces rows that S6's assertion was written without knowledge of. Either S6's assertion must be scoped to `resources` only (and say so), or S7 must re-run C2.

Non-violating but under-specified: S10 depends on nothing and is depended on by nothing — a signal it may be droppable, which B8 also concludes.

---

## Rollback audit

| Phase | Stated rollback | Adequate? | Gap |
|---|---|---|---|
| S0 | delete fixture | Yes | — (but the DB copy it relies on expires, M19) |
| S1 | revert terminal set | Partly | `TERMINAL_OUTCOMES` already contains `skip`; "revert" to the three-value set is a *behaviour change*, not a revert (B7) |
| S2 | keep old `_evaluate_outcome` for one release | Yes | but `:1265` was never removed, so nothing to revert (B6) |
| S3 | `USE_UNIFIED_ROUTER=0` | Yes, if implemented | flag is read at import; requires restart, unstated (B13) |
| S4 | remove the route | Yes | requires restart (B13) |
| S5 | restore the default | Yes | one of three defaults; rollback of `unified.py:156` missing (B1) |
| S6 | restore the blocked set | **No** | boot assertion still raises → crash loop (B14) |
| S7 | drop the boost | No | migration 022 is not in the plan, so there is nothing to roll back; the DB itself needs `.backup` (B9/M19) |
| S8 | return early | Yes | — |
| S9 | eager flag off | Yes | but the flag is inert today, so "off" changes nothing (B2) |
| S10 | not stated | **No** | no rollback line at all |
| S11 | remove the route | Yes | requires restart (B13) |

---

## What the plan gets right

- §2 (already-done) is accurate where it was spot-checked: `learning.py:365–392` non-terminal guard exists; `broker_runtime.py:475` calls `route_for_task`; `router.py:785–791` sorts preferred names ahead of score; `execution_plane.py:57-64` health route table; `cache.py:161-167` `_get_rw_conn`; `learning.py:373` raw `conn.execute`. The `learning._get_conn` "already done" claim holds (`:43-46` `_open_conn`). The "DB_PATH×3, one patched" finding is substantially right: `learning.py:24` and `broker_runtime.py:28` each define their own path from `server.db`, `cache.py:160` opens rw, and the fixture patches `learning._get_conn` and `broker_runtime._get_conn` but the isolation still leaks (71 pytest rows prove it).
- §5's gate-defect analysis (G1 `results` vs `skills`, G7 vacuity, A4 surface mismatch) is correct and was worth writing. Its one miss is that G7's fix is not a fix (M6).
- §7's measured baselines that were reproducible: `resources` 3,254; `resource_policies` 2,624; 630-resource gap; 0 NULL policy levels; `routing_log` 173; `task_runs` 8; golden 105 cases at 0.600; HEAD `6a3abaa`; 514 collected. Those numbers are trustworthy, which makes the ones that aren't (18 KB, 4-field `/health`, MCP PIDs, "5 missing tests") stand out.
- One-commit-per-step, per-step rollback, and gate-before-next-step are the right disciplines. The plan's problem is not its discipline; it is that four steps point at the wrong code and nine gates cannot tell right from wrong.

---

## Required changes before execution

1. Fix B1 (three defaults), B2 (real decision site + behavioural G9), B3 (owner file + real test), B4/B5 (correct table + 630 NULLs).
2. Fix B6 (both call sites), B7 (`skip` semantics), B8 (decide: session table or drop S10), B9 (migration 022 + seed decision), B10 (delete or restate G6).
3. Fix B11 (alias contract field by field), B12 (enumerated deletions), B13 (restart steps), B14 (assertion fail behaviour).
4. Replace the md5 isolation gate with env redirection + per-table counts (M3/B15), and replace the `cp` backup with `.backup` outside the scratch cache (M19).
5. Add the re-run-previous-gates rule (M14) and re-measure S8's p95 after S9 (M13).
6. Re-derive G2, G3, G4, G7, C2, G9, G10 as behavioural assertions with concrete SQL or concrete before/after values. A gate that cannot fail is worse than no gate.
