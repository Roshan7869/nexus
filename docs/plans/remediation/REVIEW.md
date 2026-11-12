# EGM Review of the Remediation Plans

Date: 2026-11-10 · Branch: `second-brain-v2` · Method: EGM Goldfish review (`elephant-goldfish-model/agents/design_critic.md` + `implementation_readiness.md`), run as read-only reviewers in fresh context. Every reviewer citation was checked against the repo. The probes below were run by the main agent.

Status of the 15 plan files (README + 14 changes): **none is implementable as written.** Each has blockers listed here. Most blockers are factual errors that can be fixed now. Several need a decision from you (section 4).

## 1. Facts the reviewers and I verified

| Fact | Evidence | Plan assumed |
|---|---|---|
| `/nexus/route` returns **404** on port 8000 right now | `curl -X POST :8000/nexus/route` → 404; `GET :8000/health` → 200 | Working on `:8000` (unified) |
| Port 8000 is served by `nexus_http_daemon.py` (systemd `nexus-daemon.service`, `--port 8000`) | `supervisord.conf:48`; systemd unit; reviewer found PID 288888 on :8000 | Daemon on `:8080` |
| Nothing listens on `:8080` | `curl :8080` → 000 | — |
| `unified.py` is not running | Its routes are not on :8000 | Used for `/nexus/route`, `/nexus/search` |
| `_run_sync` is defined only in `nexus_http_daemon.py:64`; `unified.py:351` calls it with no import | grep | `/nexus/route` works |
| `tests/conftest.py:179` `mock_rw_conn` patches `server.context_harness.cache._get_rw_conn` only | conftest read | Isolates learning/broker DB writes |
| `learning.py:24` `DB_PATH` is hard-coded to `REPO_ROOT/db/nexus.db`; ignores `NEXUS_DB_PATH` | reviewer read | Test DB can be redirected |
| `LearningConfig` has no `load()`; config is `get_learning_config()` (`core/config.py:130`); flags are `is_*()` methods (`:77-98`) | read | `LearningConfig.load().enable_dashboard` |
| `broker_runtime.py:1080` is inside `_close_routing_outcomes` (timeout sweeper, def `:985`), not `execute_task_run` | read | Hardcoded outcome in `execute_task_run` |
| `router.py:995` sets `outcome` used by `intent_log`; `:1265` uses it in `_log_decision` | read | Outcome is only used for A/B |
| `_evaluate_outcome` is in `route_task` (def `:910`) and `_log_decision` (def `:1252`) | read | Only a fake outcome to delete |
| Live `tool_traces.side_effect_level` values: `read` 147, `write` 6, `local_write` 2 | `mode=ro` query | Blocked set `{external_write, operational}` |
| Live `resources.resource_type`: skill, topic, mcp, command, doc, agent, design_system, spec, cli_tool, hook, craft | `mode=ro` query | Plan's list |
| Harness toolset names match 0 of 28 `resources.name` values | reviewer `mode=ro` query | Names feed `preferred_names` |
| `v5.py` `_v5_rerank` (`:308-358`) computes `final_score`; `unified_route_v5` (`:408`) has no `preferred` parameter | read | Threaded through v5 |
| Sort key already ranks preferred names first (`router.py:786-792`); `router.py:673-689` injects preferred rows at a fixed score | read | Score boost changes order |
| Existing `/ide/list` (`unified.py:964`) and `/ide/register` (`:897`) already expose IDE sessions via `get_manager()` singleton (`ide_manager.py:320`) | read | New `/nexus/ide/sessions` |
| `project_attach` MCP tool (`nexus-mcp-enhanced.py:111`) already persists IDE attach via `project_continuity.py` | read | New `ide_sessions` table |
| Migrations: last is `021_experiments.sql`; plan's `022` is free | `ls db/migrations` | — |
| `/health` in `execution_plane.py` does not exist (only `/v1/health` in `nexus_http_daemon.py:153`) | grep | Change execution_plane `/health` |
| Supervisord HTTP and MCP programs are `autostart=false` (`supervisord.conf:50, :69`); live env for MCP comes from `.mcp.json`; daemon from systemd | read | Supervisord env edits change behaviour |
| `execution_plane.py:183` reports `NEXUS_HARNESS_EAGER` default `"false"` | read | Not mentioned |
| `attach_harness_to_result` returns `harness_plan`, `toolsets`, `harness_tools`, `harness_channels`, `harness_intent` (`attach.py:24-28`) — no `harness` key | read | G9 checks a `harness` key |
| CLAUDE.md does **not** contain the "nothing that must survive… MCP process memory" sentence; it is in `server/project_continuity.py:4` and `docs/UNIFIED_MCP_DESIGN.md:36` | grep | Cited CLAUDE.md |
| `router.py` is 1,424 lines; `v5.py` 596; `fusion.py` 591 — all over the 500-line convention | `wc -l` | Not mentioned |

## 2. Blockers by plan file

Severity: **B** = blocker (implementation fails or the gate passes without checking anything); **M** = major.

### README
- B: port table is wrong (`:8080` is not served; `:8000` is the daemon). Corrections 1, 2 are wrong.
- M: baseline "490 passed" is cited in some docs as 470 and 442 elsewhere — re-run at a stated SHA.
- M: E2 shown as "Open" but gate G10 depends on E1; E2 is actually independent (see E2).
- M: golden-eval numbers have no archive. The two archives I named do not exist (`evals/results/…phase1-*` not found).

### A1 — single router
- B: `/nexus/route` on `:8000` is 404; the "live verify" curl and the test's `route` call both fail.
- B: `test_search_and_route_agree` reads `result["results"]`; `route_for_task` returns `"skills"` (`router_cutover.py:84, :115, :154`). Both lists are `[]`, so the test passes without checking anything.
- M: `/nexus/search` pseudocode omits `open_session`, which defaults to True (`router_cutover.py:245-250`) — every search writes `routing_log`.
- M: step 4 "add `ROUTER_MODES`" — it already exists (`router_cutover.py:49-53`).
- M: audit misses `router_v2/v5.py:210` (v5 L1 calls v4 `route_task` — circular), `unified.py:189`, `server/scripts/eval_faiss.py`.
- G1 says "HTTP, MCP, broker"; the test covers HTTP only.

### A2 — record_outcome
- B: test's `mock_rw_conn` does not patch `learning._get_conn`; the test writes the live DB. `make_db_conn` does not exist (only `make_db`).
- M: the non-terminal guard already exists (`learning.py:365-392`, P0 early return). The plan's `_insert_routing_log_only` is undefined. Delete the step; test the existing guard.
- M: `review_task_run` pseudocode drops behaviour: `_close_routing_outcomes` call (`broker_runtime.py:1145`), `normalize_legacy_ide` (`:88`), verdict mapping (`:1131`), and it adds `skip` to a set that today is `{correct, wrong, partial}` (`:1106`). Caller at `unified.py:478`.
- M: `event_bridge.record_outcome` deletion needs caller analysis: `get_bridge` used at `rl_feedback_loop.py:492` and `unified.py:740`.
- M: `skip` semantics conflict: `learning.py:172` includes `skip` in terminal, `:229` excludes it from pairs.

### A3 — real verifier
- B: `broker_runtime.py:1080` is in the timeout sweeper, which has no `phase` or `run`. The pseudocode references variables that do not exist there.
- B: deleting the `_evaluate_outcome` outcome lines removes the value `intent_log` (`router.py:995`) and `_log_decision` (`:1265`) need. No replacement is given.
- M: `coding` verifier's "~4 min" and 120 s timeout are unmeasured; FAST_TESTS are placeholders (the two named files exist).
- M: `quality_security` uses bandit — not installed (`venv/bin` has supervisorctl only).
- M: `test_production_route_never_returns_correct` checks for `_evaluate_outcome(`; the new call name is not specified.

### A4 — dashboard gate
- B: `LearningConfig.load()` does not exist; use `get_learning_config()` and an `is_enable_dashboard()` method following `core/config.py:77-98`.
- M: daemon `/stats` (`nexus_http_daemon.py:370`) and `/nexus/stats` (unified) are different paths; there is no duplicate in the daemon.
- M: `coverage_ratio`, `terminal_outcomes`, `non_terminal_outcomes` are not keys of `get_feedback_coverage` (`rl_feedback_loop.py:49-85`). `get_router_status` keys verified (`router_cutover.py:62-75`).
- M: `test_operator_routes_stay_on` asserts `!= 404`, which passes on a 500; the route writes `routing_log`.

### B1 — project scope
- B: production routing never calls `find_matching_skills`. Flag `"2"` (`router_cutover.py:23`) sends `/nexus/route` and MCP `find_skills` to `_shape_v5` → `unified_route_v5` (`router_cutover.py:126-130`). `find_matching_skills` (`router.py:415`) runs only on flag 0 and `/nexus/search`. The boost must go in v5 (`v5.py:308` `_v5_rerank`, `:408` `unified_route_v5`), or G7 covers only the v4 path.
- B: `unified_route_v5` (`v5.py:408-416`) has no `project` parameter; its only project reference is a hard-coded `project="default"` at `v5.py:211`. `_shape_v5` calls it without project (`router_cutover.py:130`). Signature change and call-site change must be specified.
- M: pseudocode is dead code — `boost_ids` is assigned and never read; `project_boost` is undefined.
- M: step 4 targets routes not in the daemon. Daemon has `/route` (`nexus_http_daemon.py:404`) and `/find_skills` (`:619`); `/nexus/route` and `/nexus/search` are in `unified.py:347, :357`, and autopilot at `:453`.
- M: G7 test cannot use an in-memory fixture (`seeded_project_db` does not exist); `find_matching_skills` opens hard-coded `DB_PATH` (`router.py:36`).
- M: G7 assertion is vacuous (`… if "X" in base else True`).
- M: `project_resources` is never populated — no seeding step, so the boost is a no-op.
- M: migration runner unresolved. `schema_migrations` has only row 010; the only file runner is `scripts/run_migrations_007.py:11`.
- M: per-project golden eval cannot run — `evals/golden/routing_cases.yaml` has no `project:` keys.
- M: MCP `find_skills` (`nexus-mcp-enhanced.py:79`) calls `route_for_task` directly, so HTTP-only `_require_project` does not cover it.

### B2 — project memory
- B: `mock_ro_conn` (`tests/conftest.py:171`) patches `context_harness.cache.get_ro_conn`, which `get_project_context` never calls; `seeded_learnings_db` does not exist. Use `patch("server.learning._get_conn", return_value=conn)` as `tests/test_p0_p1_learning_contract.py:20,151` does.
- M: placement contradicts itself (README correction 5 and the B2 table say `learning.py`; the open decision says `server/project_memory.py`). `learning.py` is 1,492 lines, already over the 500-line rule.
- M: overlaps existing project memory: `project_continuity.py` via MCP `project_attach`/`project_resume` (`nexus-mcp-enhanced.py:111-133`); `tests/test_project_continuity.py` exists.
- M: hot-path cost not addressed — `route_for_task` runs on every route; injection opens a connection to the 624 MB DB and runs two queries. Add a latency gate or per-project cache.
- M: the 4 KB payload cap is not enforced; longest learning note is 1,778 chars, limit 10 → up to ~18 KB. Truncate in SQL or Python and test it.
- m: injection point: `route_for_task` has three branches (`router_cutover.py:262-266`); name the line. `create_task_run` is at `broker_runtime.py:547`; `repo_context` normalised at `:560`, so `setdefault` must come after it.
- Verified OK: `user_learnings` columns; `routing_log.skill_name` (10,067 of 10,270 non-null); outcomes `correct`/`wrong` exist; `learning._get_conn` sets row_factory (`learning.py:56`).

### C1 — preview default
- M: MCP autopilot path missed — `nexus-mcp-enhanced.py:806` has `auto_execute: bool = True`, passed through at `:809`. Flip it too, or scope G8 to HTTP.
- M: caller analysis: only two callers of `create_task_run` (`unified.py:457`, `nexus-mcp-enhanced.py:809`). The "dashboard run now" caller in the doc does not exist.
- M: test writes the live DB — `create_task_run` resolves path via `broker_runtime._resolve_db_path` (`:66-75`); `route_for_task` writes `routing_log` by default (`router_cutover.py:262, :287`). Monkeypatch both to a tmp file.
- m: config snippet shape wrong — `core/config.py` uses `NexusConfig`/`LearningConfig` methods (`is_*()`, e.g. `is_auto_invoke_enabled` near `:95`), not module-level fields.
- Verified OK: `broker_runtime.py:555` default `True`, `:643` `if auto_execute:`, `"previewed"` at `:596`, `:641`; `unified.py:156`, `:464`.

### C2 — real dispatcher
- B: `BLOCKED_SIDE_EFFECTS = {"external_write","operational"}` matches **zero** live rows; live writes are `write` and `local_write`. The gate fails open. Block `{write, local_write}` and default-deny unknown values.
- B: approve test writes the live DB (`learning._get_conn` and `broker_runtime._get_conn`, `broker_runtime.py:65`). Patch both or stub `_emit_outcome_for_trace`.
- M: the "outcome hardcoded at `:1080`" problem statement is wrong (see A3). `execute_task_run` sets `outcome = None` at `:704`.
- M: pseudocode marks every row `executed` and emits learning even when `_invoke_tool` returns `blocked`.
- M: `_select_phase_tools` is not called in `execute_task_run` (`:671-735` reads `plan_json` and `tool_traces`). `cwd` is not bound there.
- M: `resource_type` table wrong. `mcp`, `topic`, `hook` fall through to `not_executed`.
- M: C1 must change `auto_execute` default, or `approve` has no `previewed` rows to act on.

### C3 — IDE sessions
- B: `project_attach` (`nexus-mcp-enhanced.py:111`) already persists IDE attach; the doc adds a parallel store. Reuse `project_continuity` or extend `/ide/list`.
- M: "each endpoint creates a fresh IDEManager" is false — `get_manager()` singleton (`unified.py:900, :966`).
- M: `mock_rw_conn` is a context manager, not a fixture; patches the wrong module.
- M: stale-session test has no UPDATE SQL.
- M: migration number 023 vs README's 022 (022 is free; pick one).
- M: the CLAUDE.md quote is from `server/project_continuity.py:4`, not CLAUDE.md.

### D1 — harness eager
- B: `LearningConfig.load()` and `harness_eager` field do not exist (same as A4). Tests raise `AttributeError`.
- M: supervisord env edits do nothing live (programs are `autostart=false`; live env from `.mcp.json` and systemd). Also `execution_plane.py:183` default is not updated.
- M: benchmark uses `route_task` with `open_session=True` (`router_cutover.py:251`) — it writes logs and eager calls network (`attach.py`, `execute_research=True`). Use `open_session=False` and a copied DB.
- M: G9 `harness` key does not exist; name the real keys (`harness_plan`, `toolsets`, …).

### D2 — harness prefetch
- B: `_shape_v5` (`router_cutover.py:126`) and `unified_route_v5` (`v5.py:408`) do not accept `preferred_names`; the pseudocode raises `TypeError`. Threading must be an explicit step through `_v5_rerank` (`v5.py:308`).
- B: harness toolset names (`toolsets.py:9-38`) match 0 of 28 resource names. The feature is a silent no-op unless a mapping is defined.
- M: verification uses `:8000 /nexus/route` — served by `nexus_http_daemon` as `/route`, not `/nexus/route`.
- M: `fetch_harness_context` adds a second planner; `attach.py:7,23` already calls `plan_harness`. State which is authoritative.
- M: no impact-analysis step (CLAUDE.md requires it); list callers of `route_for_task` (`router_cutover.py:245`; `nexus_http_daemon.py:422`, `unified.py:350`).

### D3 — harness ranking
- B: the sort key (`router.py:786-792`) already puts preferred names before score. A score boost cannot change order; the proposed test passes at baseline. Decide whether to drop the tie-break.
- B: step 2 — the SQL at `router.py:673-689` neither filters nor boosts; it injects rows at fixed score `0.97 - rank*0.03` (`:686`).
- B: v5 snippet references undefined `cand_name` and `preferred`; `final_score` is in `_v5_rerank`.
- M: sweep "0.0/0.3/0.5" has no eval command; `ci_eval.sh` and `run_ops_eval.py` exist, golden eval is `evals/run_eval.py`. Constants in `fusion.py` conflict with the flag rule.
- M: `router.py` / `v5.py` / `fusion.py` are over 500 lines; record it.
- m: `min(bonus, 0.8)` never binds for 0.5 — dead code. Weights are at `fusion.py:310-334`, not the file header.

### E1 — supervisor topology
- B: HTTP daemon is on **:8000**, not :8080 (`supervisord.conf:48`, systemd unit). Other repo files say 8080 too: `process_supervisor.py:50,99`, `ide_event_client.py:35`, CLAUDE.md table.
- B: the systemd unit `nexus-daemon.service` is enabled and already owns :8000. Setting supervisord `autostart=true` creates a second owner on the same port. The step must first disable the systemd unit (out of session), or the decision is "systemd keeps it".
- B: `tests/test_singleton_lock.py` body is `...`; helper module unnamed; file does not exist.
- M: lock code truncates the PID file (`open(…,"w")`) before `flock` — a losing process erases the live owner's PID. Use `O_RDWR|O_CREAT`, lock, then `ftruncate` and write.
- M: stale PIDs in "Current state".

### E2 — boot sanity
- B: `execution_plane.py` has no `/health`; the step is dropped.
- M: `/health` replacement drops four existing keys (`nexus_http_daemon.py:352`: status, uptime_seconds, embedding_model, db_available); unified `/health` (`:1008`) is the documented liveness probe and a test asserts it (`tests/test_unified.py:244-248`).
- M: `logger` does not exist in `nexus-mcp-enhanced.py`; stdout is the stdio channel. Insertion point is before `mcp.run` at line 1759-1760.
- M: sanity opens the live DB read-write; use `mode=ro`.
- m: `integration` marker missing on the live-DB test.

## 3. Cross-cutting issues (these affect several docs)

1. **Port and process owner.** `:8000` = `nexus_http_daemon` (systemd). `/nexus/*` routes are in `unified.py`, which is not running. Every verification curl must name the process. Decide which server is the production target.
2. **Test isolation.** No plan test is isolated from the live DB. Rule for all plans: any test that calls `route_task`, `route_for_task`, `record_outcome`, or `execute_task_run` patches `learning._get_conn` **and** `broker_runtime._get_conn` (or stubs `_emit_outcome_for_trace`). `mock_rw_conn` does not do this.
3. **Config access.** Every plan that adds a flag must use `get_learning_config()` plus an `is_*()` method (`core/config.py:77-130`), not `LearningConfig.load()`.
4. **Duplicated concepts.** IDE sessions (C3) duplicate `project_attach` / `/ide/list`. Health (E2) duplicates existing `/health`. Harness planning (D2) duplicates `attach.py`'s planner.
5. **Ordering.** D1 depends on A1 (line numbers move). E1 changes which process owns :8000, which D2/D3 verify against. Run E1's owner change after D2/D3 checks, or make the owner explicit first.
6. **Impact analysis.** CLAUDE.md requires `impact` before editing a symbol. The GitNexus MCP was unavailable; no plan lists callers. Add a grep caller list per changed symbol.
7. **File size.** `router.py` (1,424), `v5.py` (596), `fusion.py` (591) exceed the 500-line rule. The plan adds code to all three.

## 4. Decisions needed from you

1. **Production server:** `nexus_http_daemon.py` on :8000 (what runs now) or `unified.py` (where the `/nexus/*` routes are, not running)? Every verification depends on this.
2. **E1 ownership:** keep systemd `nexus-daemon.service` as the sole owner of :8000 (recommended — no change), or move it to supervisord (requires disabling the systemd unit)?
3. **MCP:** keep `autostart=false` and per-IDE spawn (recommended, per CLAUDE.md), and drop the MCP singleton lock?
4. **C2 execution semantics:** commands return `not_executed` (recommended), or run allow-listed read-only commands?
5. **C3 store:** reuse `project_continuity` / `/ide/list` (recommended) instead of a new `ide_sessions` table?
6. **A1 search shape:** `/nexus/search` returns the `/nexus/route` shape (recommended) and `open_session=False`?
7. **D2/D3:** build the toolset→resource mapping (required for any effect), or drop harness ranking and keep only context prefetch?
8. **A3 sweeper:** what should the timeout sweeper emit for a task with no verifier — `skip` with `outcome_source="timeout"` (recommended), or leave it untouched?

## 5. Review status

All five reviewer groups have reported (A, B/C1, C2/C3/D1, D2/D3/E1, E2 + README). Findings are in §2. Cross-doc issues from the B group that are not yet in §3:
- B1 edits `find_matching_skills` (v4). Production runs v5 (flag `"2"`). A1 is likely to retire the v4 path, so B1's target depends on A1's decision.
- Gates G6 and G8 are HTTP-only in the docs, but the MCP path is open in B1 and C1. Declare gate scope in the README.
- B2 declares a dependency on B1, but its code does not use the boost.

## 6. Recommended order after decisions

1. Fix the test-isolation helper in `tests/conftest.py` (one shared fixture; decision: none needed).
2. A1 (after decision 1 and 6), then A2, A3 (decision 8), A4.
3. B1, B2, C1 (after review).
4. C2 (decision 4), C3 (decision 5), D1, D2 + D3 (decision 7), E2 (independent; decision 1 for the port), E1 last (decision 2–3).

No plan file should be executed until its blockers in §2 are resolved in that file.
