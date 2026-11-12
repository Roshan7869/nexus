# Stage 9 — Codebase Details review of `EXECUTION.md`

Reviewer: egm-codebase-details (Goldfish)
Target: `docs/plans/remediation/EXECUTION.md` (320 lines, 18,843 bytes)
Repo: `/home/roshan/nexus` @ working tree of 2026-11-10 19:19
Method: every row checked by reading the named file at the named lines, or by a read-only query against `db/nexus.db` (`file:...?mode=ro`). No file under `server/`, `tests/`, `db/` was modified; no service was started or stopped; the test suite was not run.

## Verdict

The plan's *diagnosis* of the system is mostly accurate — the port/owner evidence table (§7) survives a re-probe almost line for line, and the Wilson arithmetic in §6 is exact. The plan's *contracts* are not. Four steps name code that does not exist: `_invoke_tool`, a `project_resources` table, a `repo_context` parameter in `route_for_task`, and an `is_enable_dashboard()` flag. Two steps key their gate to the wrong table or the wrong variable, and one step's headline evidence ("every curl 404s", "11 of 12 steps") is refuted by the plan's own transcript.

Counts: **4 BLOCKER · 10 MAJOR · 10 MINOR**. The three load-bearing numbers in §6 (105 cases, Wilson interval, 73/105 threshold) are correct.

---

## BLOCKER

### B-1 · S6 gates on a table that does not decide anything, with a vocabulary that does not occur
`EXECUTION.md:179-181` · claimed: inputs are "live `tool_traces.side_effect_level`: `read` 147, `write` 6, `local_write` 2"; outputs `BLOCKED = {"write","local_write"}` plus "a boot assertion that every tool row has a non-NULL `side_effect_level`".

Actual, from `db/nexus.db` (`mode=ro`):

```
tool_traces.side_effect_level : read 147, write 6, local_write 2      ← TRUE, but this is an audit log of past runs
resource_policies (2624 rows) : read 2134, local_write 482, operational 8
resource_policies NULL/empty  : 0
there is no table named "tools" or "tool_policies"
```

`tool_traces` records what already happened (`server/broker_runtime.py:125-165` defines the table; rows are inserted per call). The policy that decides what *may* run is `resource_policies.side_effect_level`, and its vocabulary contains `operational` and **does not contain `write`**. So `BLOCKED = {"write","local_write"}` blocks 482 rows and zero rows respectively, and admits the 8 `operational` rows — the exact fail-open the plan says it is fixing. The boot assertion is also vacuous: 0 of 2,624 policy rows are NULL.

Fix: state the table. Gate on `resource_policies.side_effect_level`; `BLOCKED = {"local_write","operational","write"}` with unknown ⇒ deny; assert non-NULL on `resource_policies` (where a NULL is possible) and drop the `tool_traces` numbers or label them as observed traffic, not policy.

### B-2 · S6's owner does not exist
`EXECUTION.md:178` · claimed owner: "`server/dispatch.py` (or wherever `_invoke_tool` lives)".

Actual: `grep -rn "_invoke_tool\|def invoke_tool" --include=*.py .` → no matches anywhere in the repo. `server/dispatch.py` is the post-autocut waterfall event bus (`register`/`emit`, `core/config.py`-independent), imported by the router itself: `server/router_v2/v5.py:48` `from dispatch import waterfall as _dispatch_waterfall, POST_AUTOCUT as _POST_AUTOCUT`. It has no tool execution and no side-effect logic. `tests/test_dispatch.py` tests waterfall ordering, nothing else.

Fix: name the real execution seam — the function that writes a `tool_traces` row (`server/broker_runtime.py`, the insert seen at 125-165) or `server/nexus_plan_executor.py` — and delete the "(or wherever)" hedge. The parenthetical is an admission the author did not find the owner, and it is the only place in the plan where a hedge stands in for a file:line.

### B-3 · S7's input table does not exist
`EXECUTION.md:195` "`project_resources` — currently unpopulated, so seeding is part of this change" · `EXECUTION.md:314` "B1's boost is invisible until `project_resources` is seeded".

Actual: `select name from sqlite_master` → no `project_resources`. `grep -rn "project_resources"` over `*.py *.sql *.md` → only the plan documents themselves. The change file `docs/plans/remediation/B1-project-scope.md` is explicit that the table is new: it specifies `db/migrations/022_project_resources.sql`. "Currently unpopulated" describes a table that exists and is empty; this one is absent.

Consequence: §2 line 55 budgets "1 new table" without attributing it, and S7 lists no migration, no columns, no key, no idempotent-seed rule. That is a Zero-Decision violation on the one step that already has a table-shaped hole.

Fix: S7 inputs must read "new table `project_resources(project, resource_id, weight, source, created_at)` via `db/migrations/022_project_resources.sql`; seed script in the same commit"; and §2's "1 new table" should say *which* change owns it.

### B-4 · S8's injection point has no `repo_context` to normalise
`EXECUTION.md:206-208` · claimed: "Injected at the point in `route_for_task` that already has `project` bound, after `repo_context` normalisation at `broker_runtime.py:560`".

Actual: `server/router_cutover.py:245-253` —

```python
def route_for_task(task: str, project: str = "default", max_results: int = 5, *,
                   flag: str | None = None, open_session: bool = True, ide: str | None = None)
```

There is no `repo_context` parameter, and `repo_context` never crosses into `route_for_task`. It is a `create_task_run` local (`server/broker_runtime.py:470-477`, normalised at `:560`). A function cannot inject "after" a normalisation that happens in its caller and is not passed to it.

Compounding: S8's **Owner** is `server/learning.py`, but its **Outputs** describe an edit inside `route_for_task`, which lives in `router_cutover.py`. Two owners, one step.

Fix: choose one seam and name it — either inject inside `broker_runtime.create_task_run` after `repo_context` is normalised (owner `broker_runtime.py`, no signature change), or add `project_context: str = ""` to `route_for_task` and inject there (owner `router_cutover.py`). Do not name both.

---

## MAJOR

### M-1 · S0 cites a fixture pattern that does not exist
`EXECUTION.md:118-121` "copy the `isolated_home` fixture pattern from `tests/test_p0_p1_learning_contract.py:11-24`".

`grep -rn "isolated_home"` → no matches in the repo. Lines 11-24 of that file are `NEXUS_ROOT`/`sys.path` setup plus, inside a test method, `with patch("server.learning._get_conn") as mock_conn: conn = MagicMock()` — it replaces the connection with a mock. That is the opposite of what S0 needs (patch the *path*, keep the real connection so `md5sum db/nexus.db` can prove nothing was written).

The reusable pattern already exists thirty lines away: `tests/conftest.py:145` `make_db()`, `:170` `mock_ro_conn`, `:178` `mock_rw_conn`, `:193` `db` fixture, `:201` `engine` fixture composing them.

Fix: cite `tests/conftest.py:170-207`; say "extend `mock_rw_conn`'s `_NoCloseConnection` pattern to `server.learning._get_conn` and `server.broker_runtime._get_conn`".

### M-2 · "Every verification curl … targets a route that returns 404 today" is false
`EXECUTION.md:26`.

Counter-evidence, run against the live daemon:

```
GET  http://127.0.0.1:8000/health                  -> 200
POST http://127.0.0.1:8000/nexus/route             -> 404
POST http://127.0.0.1:8000/nexus/search            -> 404
GET  http://127.0.0.1:8000/nexus/stats             -> 404
GET  http://127.0.0.1:8000/nexus/feedback/coverage -> 404
GET  http://127.0.0.1:8080/health                  -> 000 (connection refused)
```

The change files curl `/health` in two places (`E1-supervisor-topology.md:88`, `E2-boot-sanity.md:142`, the latter with `# expect 200`), and `:8080` refuses rather than 404s. So the set is "some routes 404", not "every curl".

Fix: "Every `/nexus/*` verification curl 404s today; `/health` is live and one curl targets a port with no listener."

### M-3 · "11 of 12 steps name a route that 404s" is inflated
`EXECUTION.md:307`.

Step-by-step against the route each step's verification names: S0 (pytest + md5, no route), S1 (G2/G3 unit), S2 (compileall) — three steps with no HTTP. S3 targets `/route` which returns **200**. S11 targets `/health` (**200**) plus `/health/deep` (404). That leaves S4, S5, S6, S7, S8, S9, S10 = **7**, or 8 if S11 counts on the `/health/deep` half. Not 11.

Fix: "8 of 12 steps add or hit a `/nexus/*` route that does not exist; 3 are unit-only; S3's `/route` already works."

### M-4 · §5's headline broken gate is misdiagnosed
`EXECUTION.md:244` "G1 reads `result["results"]`; `route_for_task` returns `"skills"` → two empty lists compare equal" · repeated at `:158-159`.

`tests/test_router_cutover_phase7.py` contains no `["results"]` read. The two tests read:

```
:70  result = compare_routers("test query", top_k=3)
:71  self.assertTrue(result["top1_match"])
:72  self.assertEqual(result["v4_names"][0], "skill-a")
:73  self.assertEqual(result["v5_names"][0], "skill-a")
:83  result = run_ab_suite(queries=["q1","q2"], top_k=5)
:84-88  queries_run / top1_hits / top1_agreement / gate_pass / top1_target
```

and the mock at `:48`/`:68` returns `{"skills": [...]}`, i.e. the test is consistent with `route_for_task` returning `"skills"`. A `"results"` key does exist in this module — but as the A/B report's per-query rows, `server/router_cutover.py:394` — so "reads a key that does not exist" is wrong on both halves.

The gate may still deserve a rewrite (it asserts a mocked `top1_match` against a mock), but the stated reason is invented. A plan that fabricates its example of a broken gate cannot be trusted on the gates it does not show.

Fix: either quote the actual fixture that reads `["results"]` (if one exists elsewhere — `grep -rn '\["results"\]' tests/` returns nothing), or replace §5's G1 row with the real defect: "G1 patches `route_for_task` and asserts against the patch; it never exercises HTTP, MCP or the broker."

### M-5 · The new-test-file count is wrong by 3×
`EXECUTION.md:55` "5 new test files" · `EXECUTION.md:296` "plan test files | `ls tests/` | all 5 missing".

Every `tests/*.py` path named across the 14 change files, checked with `[ -f ]`:

```
MISSING (15): test_autopilot_preview, test_dashboard_gate, test_dispatch_gate,
  test_harness_boost, test_harness_eager, test_harness_hot_path, test_ide_sessions,
  test_learning_loop, test_no_fake_verify, test_project_boost, test_project_memory,
  test_project_required, test_router_unified, test_sanity_check, test_singleton_lock
EXISTS  (6): conftest.py, test_local_harness.py, test_p0_p1_learning_contract.py,
  test_project_continuity.py, test_site_task_binding.py, test_unified.py
```

"All 5 missing" is wrong twice: there are 15, and 6 of the named files exist. The undercount matters because §2 uses the number to argue the change set is small.

Fix: "15 new test files; 6 of the 21 named files already exist."

### M-6 · S9's flag has no effect at either site it names
`EXECUTION.md:214-215` · claimed: "`core/config.py` (`is_harness_eager()`), `server/execution_plane.py:183`" / "flag default true at both sites".

Actual:
- `grep -n "harness" core/config.py` → **no matches**. `LearningConfig` (`core/config.py:48-109`) exposes `is_thompson_routing`, `is_cluster_routing`, `is_reflection_enabled`, `is_mcp_sync`, `is_auto_invoke_enabled`. There is no harness flag to default.
- `server/execution_plane.py:183` is a row in a diagnostics payload: `"harness_eager": os.environ.get("NEXUS_HARNESS_EAGER", "false")`. It reports the variable; it decides nothing.
- The value that actually decides eagerness is read at `server/router_cutover.py:277`, inside `attach_harness_to_result`. That file is not in S9's owner list.

So the step as written flips a string in a debug dict and adds an accessor nobody calls. The behaviour does not change, and G9 ("assert those names") passes either way because the keys at `server/harness_engine/attach.py:24-28` are written unconditionally.

Fix: owner = `server/router_cutover.py:277` (the gate) + `core/config.py` (the accessor, per the class docstring at `:51-52`); drop `execution_plane.py:183` or list it explicitly as "diagnostics string only — must read the same accessor".

### M-7 · S4 gates on a method that does not exist
`EXECUTION.md:165` "gated by `get_learning_config().is_enable_dashboard()`".

`core/config.py:48-109` has no `is_enable_dashboard`. `grep -rn "is_enable_dashboard"` over `core/ server/` → no matches. Calling it raises `AttributeError` on every request. The change file the step comes from uses a different mechanism — `docs/plans/remediation/A4-gate-dashboard.md:37` raises 404 on `NEXUS_ENABLE_DASHBOARD=1` being unset, read directly from env.

Fix: either add `is_enable_dashboard()` to `LearningConfig` as part of S4 (and say so in Outputs), or gate on `NEXUS_ENABLE_DASHBOARD` as A4 does. Do not cite a method that no caller can resolve.

### M-8 · S10's "already persist this" is one column, not sessions
`EXECUTION.md:68` "`/ide/list`, `/ide/register` and MCP `project_attach` already persist this" · `:223-226` "`/ide/list` reads from `project_continuity`'s persisted rows … start two MCP processes, both appear; kill one, it disappears within the staleness window".

Actual: `projects` has five columns — `name, git_root, last_ide, last_active_at, note`. `server/project_continuity.py:78-93` `attach()` upserts **one row per project**, overwriting `last_ide` and `last_active_at`. There is no session id, no per-process row, and no `ide_sessions` table (`sqlite_master` has `ide_events, coach_sessions, tool_sessions, ig_sessions, local_harness_sessions` — none of them session-scoped for an IDE).

Consequence: two IDE processes attach to the same project → one row, last writer wins → "both appear" is impossible from those rows, and "kill one, it disappears" has nothing to disappear from. The in-process manager (`/ide/list` → `_get_ide_manager().get_dashboard()`, `server/nexus_http_daemon.py:872-874`) is the only thing that knows about two processes, and it is memory-only by definition.

Fix: S10 must say what row identifies a process (e.g. `(project, pid, started_at, heartbeat_at)` in a new small table, or `ide_events` if it can carry a heartbeat) and what the staleness window is. "No `ide_sessions` table" is a decision worth keeping — but then the union S10 describes has no second source.

### M-9 · The 4 KB cap defends against a size that does not exist
`EXECUTION.md:209` "payload never exceeds 4 KB with 10 maximal notes (current worst case ~18 KB)".

Measured on `db/nexus.db`:

```
project_notes rows: 8   (project 'nexus' 7, 'default' 1)
max single note  : 632 chars
sum of all notes : 3,913 chars
```

The largest note is 632 bytes; all eight together are 3.9 KB. An 18 KB worst case requires ten ~1.8 KB notes, none of which exist. So the cap is being sized against a hypothetical, and the real finding is the inverse: current data already sits at 97% of the proposed cap, so the cap will start truncating on the next note.

Fix: "current corpus is 3.9 KB across 8 notes (max 632 B); a 4 KB cap would already bind — either raise it to 8 KB or state the per-note truncation rule."

### M-10 · "4-field `/health`" — it has nine
`EXECUTION.md:38` "the daemon's 4-field `/health` clients break" (echoed at `E2-boot-sanity.md:144` "four old keys").

`server/nexus_http_daemon.py:357-367` returns nine keys: `status, uptime_seconds, embedding_model, embedding_load_time_s, db_available, total_requests, cache_hit_rate, cache_stats, timestamp`.

The argument (clients depend on fields that would move) stands; the count does not, and it is the only quantified support for rejecting Option A.

Fix: "nine keys"; list the two that are load-bearing (`cache_stats`, `embedding_model`) rather than counting.

---

## MINOR

### N-1 · Terminal-set line is the error string, not the set
`EXECUTION.md:130` cites `broker_runtime.py:1106` for `{correct, wrong, partial}`. `:1105` is `if verdict not in {"correct","wrong","partial"}:`; `:1106` is the `return {"status": "error", "message": "verdict must be correct…"}`. Fix: `:1105`.

### N-2 · `mcp.run` line
`EXECUTION.md:232` "before `mcp.run` (line 1759)". `:1759` is `if __name__ == "__main__":`; `mcp.run(transport="stdio")` is `:1760`. Fix: `:1760`.

### N-3 · "4 new modules"
`EXECUTION.md:55`. The 14 change files name three new modules: `server/verify_registry.py` (S2), `server/health.py` (S11), and the "~40-line alias module" (§1 Option B). Fix: "3".

### N-4 · MCP explicit-pass line
`EXECUTION.md:173` "`nexus-mcp-enhanced.py:809`". `:809` is `result = create_task_run(`; `auto_execute=auto_execute` is `:816`. The claim is true, the line is not. Fix: `:816` (or `:809-816`).

### N-5 · PID evidence has already decayed inside the same table
`EXECUTION.md:294` "4 live: 289293, 289840, 311553, 318953 (plan cites … stale)". At re-probe: `nexus-mcp-enhanced.py` PIDs are `289293, 289840, 318953, 342420` — `311553` is gone, `342420` is new. The row criticising the change files for stale PIDs is itself stale. Fix: cite the count (4) and the command, not the PIDs; PIDs belong in a timestamped log, not a plan.

### N-6 · S0's input list omits the third patcher
`EXECUTION.md:118` lists `make_db, mock_rw_conn, mock_ro_conn`. `tests/conftest.py:185` also defines `mock_nexus_conn` (patches `context_harness.nexus_bridge.get_conn`). If S0 is patching every independent connection factory, this one is in the same category.

### N-7 · "both servers" — there are three `/health`
`EXECUTION.md:235` "Existing `/health` on both servers unchanged". Sites: `server/unified.py:1008`, `server/nexus_http_daemon.py:352`, `core/server.py:254` — the third is the `/v1` OpenAI-compatible app, which the plan never mentions in §1 but is a site E2's "unchanged" promise covers. Fix: name all three or scope the promise to the two.

### N-8 · D3's conclusion is right; its reasoning is half-right
`EXECUTION.md:71` "`router.py:786-792` already sorts preferred names ahead of score. A score boost cannot change order."

The sort is `server/router.py:785-792` with key `(0 if _explore else 1, 0 if name in preferred_name_set else 1, -score, name)`. Preferred names dominate *score* — true — so a score boost cannot lift a non-preferred candidate over a preferred one. But the **explore** flag at `:787` dominates the preferred flag at `:788`, so a boost cannot touch exploration candidates at all. Two further facts weaken the argument: the sort lives inside `find_matching_skills` (`:415`), which §2 line 64 declares "not on the production path" (`ROUTER_MODES["2"].production = True`, `router_cutover.py:52`; v5 imports neither). Cite a production sort or say plainly "D3 targets a dead function, so it is a no-op" — that is a stronger cut than "the sort already handles it".

### N-9 · D2's "0 of 28" is right; its wording is not
`EXECUTION.md:70` "Toolset names match 0 of 28 resource names". `TOOLSETS` has **4** keys (`core, research, browser, memory`) and **28** tool entries. Three of the four keys *are* exact resource names in the live DB. The true statement is "0 of the 28 tool names inside `TOOLSETS` match a resource name" — which I confirmed (`exact hits: []`). Fix the wording before someone re-checks "3 of 4 match" and calls the row false.

### N-10 · Albias identity only holds off-flag-0
`EXECUTION.md:33` "`/nexus/*` and `/route` must be proven to return identical bodies". `server/nexus_http_daemon.py:422-436` branches: when `get_router_flag() != "0"` it awaits `route_for_task`; when the flag is `"0"` it calls `server.router.route_task` directly. The two paths diverge by design, so identity holds only for flag 1/2. Say so, or the alias will be "identical" in the test and different in the field.

---

## Verified TRUE (no action)

Each of these was re-checked; the plan is correct and I am not padding it with commentary.

| Claim | Proof |
|---|---|
| `:8000` = `nexus_http_daemon.py --port 8000`, PID 288888, systemd `--user` unit `nexus-daemon` | `ss -ltnp`; `/proc/288888/cgroup` → `...app.slice/nexus-daemon.service`; `systemctl --user is-active nexus-daemon` → `active`; system scope → `inactive` |
| `:8080` dead; `/nexus/route` 404; `/nexus/search` 404; `/health` 200 | curl matrix above |
| `unified.py` defines `/nexus/route` at 347, `/nexus/search` at 357, not running | `unified.py:347,357`; only 288888 listens on 8000 |
| Daemon exposes `/route`, `/find_skills`, `/health`, `/ide/list`, `/ide/register` | `nexus_http_daemon.py:404,619,352,872,844` |
| `ROUTER_MODES` exists; A1.2 already done at `broker_runtime.py:475` | `router_cutover.py:49-53`; `broker_runtime.py:475` `routed = route_for_task(` |
| Non-terminal guard already at `learning.py:365-392` | `:365 if outcome not in TERMINAL_OUTCOMES:` … `:392` |
| `bandit` absent | `python -c "import bandit"` → `ModuleNotFoundError` |
| Supervisor does not own the daemon | owner is systemd user unit; `supervisord.conf:47-48` defines a program that is not the running process |
| `get_router_status()` already returns the keys | `router_cutover.py:62-75` (note: in `router_cutover`, not `router.py`) |
| `find_matching_skills` off the v5 production path | `router_cutover.py:52` `production: True` for flag 2; `v5.py:42-48` imports `router_v2`, `fusion`, `dispatch` — not `router` |
| D2 "0 of 28" | `TOOLSETS` = 28 entries; exact name matches against 3,254 resource names = 0 |
| `mock_rw_conn` patches only `context_harness.cache._get_rw_conn`; learning/broker untouched | `tests/conftest.py:178-182` (single `patch`); no `learning`/`broker_runtime` patch anywhere in the file |
| 59% order-dependence of Python flaky tests | `RESEARCH-primary-sources.md:112-115` (59% of 7,571 flaky tests / 876,186 cases / 22,352 projects, Gruber et al., ICSE-adjacent ICST 2021, arXiv:2101.09077) |
| `unified.py:457` is the `create_task_run` caller | `unified.py:457-465` |
| `{correct, wrong, partial}` is the terminal set | `broker_runtime.py:1105` (line off by one, see N-1) |
| `router.py:391` is `_evaluate_outcome`; `broker_runtime.py:1080` writes `outcome="correct"` | `router.py:391`; `broker_runtime.py:1078-1082` inside `_close_routing_outcomes` — the self-grade the plan wants removed, correctly located |
| `ROUTER_MODES` at `router_cutover.py:49-53` | exact |
| `route_for_task` returns `"skills"` | `router_cutover.py:478` `candidates = routed.get("skills", [])` |
| `broker_runtime.py:555` `auto_execute` default True; `unified.py:457` passes it | `:555 auto_execute: bool = True`; `unified.py:464` |
| `tool_traces` live distribution 147 / 6 / 2 | `mode=ro` group-by — true of `tool_traces` (see B-1 for why it is the wrong table) |
| `v5.py:308` `_v5_rerank`, `:408` `unified_route_v5` (no `project` param) | `grep -n "def _v5_rerank\|def unified_route_v5"` → 308, 408; signature has no `project` |
| MCP `find_skills` at `nexus-mcp-enhanced.py:79` bypasses the endpoint | `:79 def find_skills(task, project="default", ...)` — local implementation, no HTTP |
| `repo_context` normalised at `broker_runtime.py:560` | `:560 repo_context = repo_context or {}` |
| Harness response keys `harness_plan, toolsets, harness_tools, harness_channels, harness_intent`; no `harness` key | `harness_engine/attach.py:24-28`; `grep '"harness"'` over harness/router_v2 → none |
| No `ide_sessions` table | `sqlite_master` — absent |
| `tests/test_unified.py:244-248` asserts `/health` in routes | `:248 self.assertIn("/health", routes)` |
| G7 quote and A4 quote in §5 | `B1-project-scope.md:85` exact; `A4-gate-dashboard.md:89` exact |
| 105 golden cases | `evals/golden/routing_cases.yaml` → 105 entries |
| Wilson interval [0.504, 0.689], half-width 9.2 pp; 73/105 lower bound 0.6016 > 0.600 | recomputed: 63/105 → [0.5044, 0.6886]; 73/105 → [0.6016, 0.7751] |
| `RESEARCH-primary-sources.md` 171 lines | `wc -l` → 171 |
| `db/migrations` (021 last), no `server/migrations` | `ls` → `...020_local_harness.sql, 021_experiments.sql, BASELINE.sql`; `ls -d server/migrations` → absent |
| `/health` sites `unified.py:1008`, `nexus_http_daemon.py:352`, `core/server.py`; none in `execution_plane.py` | grep + `core/server.py:254` |
| `CLAUDE.md:128` autostart rule present | exact text: "do not flip `autostart=true` while the IDE or systemd owns those processes" |
| `CLAUDE.md:38,66,123` say `:8080`, `supervisord.conf:48` says `--port 8000` | both confirmed; `:123` additionally names `systemd nexus-daemon.service` — which the cgroup confirms is right |
| Rollback point 624 MB, 19:18 | `624033792` bytes, `Nov 10 19:18` |
| `router.py` 1,424 · `v5.py` 596 · `fusion.py` 591 | `wc -l` exact |
| `plan_toolsets` → `-> list[str]` | `toolsets.py` annotation |

## Verified FALSE (summary)

| Claim | Actual |
|---|---|
| `:26` every verification curl 404s | `/health` → 200; `:8080` → 000 |
| `:307` 11 of 12 steps 404 | 7 (8 with S11's half); 3 steps are unit-only; S3's route works |
| `:296` all 5 plan test files missing | 15 missing, 6 of the named 21 exist |
| `:55` 5 new test files | 15 |
| `:55` 4 new modules | 3 |
| `:38` 4-field `/health` | 9 fields |
| `:244` G1 reads `result["results"]` | no test does; tests read `top1_match`/`v4_names`/`v5_names`; `"results"` exists at `cutover:394` |
| `:195` `project_resources` unpopulated | table does not exist |
| `:179` policy vocabulary includes `write` | `resource_policies` = read/local_write/operational; `write` appears only in `tool_traces` |
| `:181` boot assertion can find NULLs | 0 of 2,624 policy rows are NULL |
| `:178` owner `dispatch.py` / `_invoke_tool` | no `_invoke_tool` anywhere; `dispatch.py` is the waterfall bus |
| `:206` `route_for_task` has `repo_context` | signature `(task, project, max_results, *, flag, open_session, ide)` |
| `:214` `core/config.py` harness flag | no harness code in `core/config.py` |
| `:183` `execution_plane.py:183` is a gate | diagnostics dict row; real gate is `router_cutover.py:277` |
| `:165` `is_enable_dashboard()` | does not exist on `LearningConfig` or anywhere |
| `:68` IDE "already persist this" | `projects` = 5 columns, one row per project, no session id |
| `:209` ~18 KB worst case | 3,913 chars total, max 632 |
| `:118` `isolated_home` fixture | no such symbol; cited lines are `sys.path` + `MagicMock` |
| `:232` `mcp.run` at 1759 | 1760 |
| `:130` terminal set at 1106 | 1105 |
| `:173` explicit pass at 809 | 816 |
| `:294` 4 live MCP PIDs | 311553 gone, 342420 new |

## Not verified (stated, not checked)

- `pytest tests -q` → 490 passed / 24 skipped / 89.46 s (`:122`, `:255`, `:290`). I was instructed not to run the suite. Note that `pytest-timeout` and `pytest-randomly` are **not installed** in `venv` (`pip list` → pytest 9.0.3, pytest-asyncio 1.4.0 only), so S0's "same suite passes with the tests run in reverse order" has no declared mechanism: `-p no:randomly` is the only way to *fixed* order, and there is no plugin to reverse it. Name the mechanism — `pytest --collect-only | tac | xargs` or installing `pytest-randomly` — or the verification is unrunnable.
- Golden `pass 0.600 · skill 0.500 · cluster 0.750` at `c8782ad`/`6a3abaa` (`:256`). The case count is right; I did not re-run the eval.
- `ops cluster_divergence FAIL (1326 vs 0)`, `process_topology FAIL (0.0)` (`:257`).

## One structural note

Eight of the eleven steps in §4 name a `file:line`, and six of those lines are inside the *right* function — that is better than most plans. The failures cluster in one place: wherever the plan reaches for a **symbol it did not open**, it invents one (`_invoke_tool`, `is_enable_dashboard`, `repo_context` in `route_for_task`, `project_resources`, `isolated_home`). The fix is mechanical: every symbol in §4 must appear in a `grep -n` output pasted into §7, the same way the port evidence already does.
