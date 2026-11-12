# Stage 9 — Style & Clarity Review: `EXECUTION.md`

Reviewer: egm-style Goldfish · Target: `/home/roshan/nexus/docs/plans/remediation/EXECUTION.md` (320 lines)
Date: 2026-11-10 · Evidence: every factual claim below was checked against the repo at `HEAD` and the live DB (read-only) during review.

---

## Verdict

**FAIL — not executable as written.** 12 blockers, 24 majors, 8 minor.

The document is strong on *measurement* — the probe table in §7 is real, the Wilson interval in §6 recomputes correctly, and several of its corrections to the source plan are accurate. It is weak on *artifact naming*. Eight of the twelve steps do not name a file, function, signature, or path precisely enough to be executed without invention, and four name artifacts that do not exist in this repository. Three of the ten gates cannot fail.

The prose is clean of banned adjectives. No `great`/`brilliant`/`seamless`/`robust`/`powerful` appears anywhere; the tone is dry and factual. That is the one axis on which the document passes without qualification.

---

## 1. The phase count question

The brief asks whether the prose disagrees with a section list. It does not, and the premise of an "eight phase" claim does not hold:

- `grep -i eight EXECUTION.md` → no match.
- `### S` headings: S0, S1 … S11 → **twelve** steps.
- Prose counts: §8 "11 of 12 steps"; §3 "fixing 11 tests later". Both are consistent with twelve.

So the step list is twelve and the document says twelve. Two counting defects remain, neither of them this one:

1. §2's verdict tally (`7 build · 4 shrink · 3 drop/defer`) does not match its own table (see F13).
2. §8's `11 of 12` is asserted without enumeration and is not supported by the per-step text (see F16).

---

## 2. Blockers — steps that cannot be executed as written

### F1 · S5 names the wrong owners; G8 cannot pass
> "**Owner** `server/broker_runtime.py:555`, `server/nexus-mcp-enhanced.py:806`"
> "Both callers already pass it explicitly at `unified.py:457` and `nexus-mcp-enhanced.py:809`."

The HTTP path does not take its default from either cited file. `server/unified.py:156` declares `auto_execute: bool = True` on `AutopilotRunRequest`, and `unified.py:464` passes `req.auto_execute` through. Flipping only the two function defaults leaves the HTTP default at `True`, so **G8 (`status: previewed` over HTTP) cannot pass** after this step. The step's own change file (`C1-preview-default.md`) states the same thing: "`server/unified.py:149` — `class AutopilotRunRequest(BaseModel)`; `auto_execute: bool = True` at **`unified.py:156`** … was True". S5 dropped that line. The cited `:457` is `result = create_task_run(`, not the argument pass (which is `:464`).

**Fix:** owner list becomes `server/broker_runtime.py:555`, `server/unified.py:156`, `server/nexus-mcp-enhanced.py:806`; verification line cites `:464` for the pass-through; add `core/config.py` `auto_execute_default` if the rollback escape hatch in C1 is in scope.

### F2 · S6 names a function that does not exist
> "**Owner** `server/dispatch.py` (or wherever `_invoke_tool` lives)"

`grep -rn "_invoke_tool" server/*.py` returns nothing. `server/dispatch.py` exists; it has no `_invoke_tool`. The parenthetical is the plan admitting it does not know its own target, and the target it guesses is absent.

**Fix:** name the actual gate function and line: the policy check that reads `resource_policies.side_effect_level` and decides execution. Replace the parenthetical with `(verified absent: `_invoke_tool` does not exist; gate lives at <file>:<line>)`.

### F3 · S6's boot assertion cannot fail
> "a **boot assertion** that every tool row has a non-NULL `side_effect_level`"

`db/schema.sql:185` declares `side_effect_level TEXT NOT NULL DEFAULT 'read'`. The column is `NOT NULL` with a default — the assertion is guaranteed true by the constraint, and unknown rows silently become `'read'`, which is an **allow**, not a deny. The assertion checks nothing and contradicts the stated default-deny posture.

**Fix:** assert on the policy table's *content*, not its nullability: `SELECT COUNT(*) FROM resource_policies WHERE side_effect_level NOT IN ('read','write','local_write','operational')` must be 0, and `SELECT COUNT(*) FROM resource_policies WHERE side_effect_level IN ('write','local_write')` must be > 0 (proves the block set is non-empty).

### F4 · S6 measures the wrong table, and mislabels what the numbers are
> "**Inputs** live `tool_traces.side_effect_level`: `read` 147, `write` 6, `local_write` 2"

The counts are accurate (verified against `db/nexus.db` in read-only mode). `tool_traces` is a per-trace history of tools selected during runs — 155 rows across many task runs — not a registry of tools. The table that actually governs execution is `resource_policies`: **2134 `read`, 482 `local_write`, 8 `operational`, 0 `write`**. The plan derives `BLOCKED = {"write", "local_write"}` from traces and then calls the rows "every tool row". An executor following this will gate on historical traces and never touch the policy table, and will not discover `operational` — a level with 8 live rows that is neither allowed by name nor covered by the block set.

**Fix:** state the table (`resource_policies`), state the real distribution, and decide `operational` explicitly (blocked or allowed — name it).

### F5 · S6's verification is an undefined gate
> "**Verification** G-gate of your own: a row with `side_effect_level=\"write\"` is blocked at `allow_writes=False` **and** an unrecognised value blocks by default."

"Your own" delegates gate design to the implementer. The Zero-Decision rule names exactly this: a plan that leaves the acceptance criterion to the implementer fails. The two conditions are also not a gate — no file, no test name, no assertion text, no fixture.

**Fix:** write the two assertions verbatim, name the test file and function, and give the fixture row values.

### F6 · S7 depends on a table that does not exist
> "**Inputs** `project_resources` — currently unpopulated, so seeding is part of this change"

`grep -rn "project_resources" --include=*.py --include=*.sql .` returns **no matches**. The table is not "unpopulated"; it does not exist. Seeding therefore requires a schema migration — `db/migrations/022_*.sql` per README correction 4 — which no step mentions and §2 does not count. §2 claims the plan adds "1 new table" but never says which change adds it, and S10 explicitly says "**No `ide_sessions` table.**"

**Fix:** either state "new table `project_resources(id, project, resource_name, weight, created_at)` added in `db/migrations/022_project_resources.sql`" with the DDL, or re-point B1 at an existing table and name it.

### F7 · S7's file path is wrong
> "**Owner** `router_v2/v5.py:308` `_v5_rerank`, `:408` `unified_route_v5`"

The file is `server/router_v2/v5.py`. The line numbers (308, 408) are correct; the path prefix is not. A fresh session running `cd /home/roshan/nexus && sed -n '308p' router_v2/v5.py` gets `No such file or directory`. Same class of defect the document criticises in the source plan.

**Fix:** `server/router_v2/v5.py:308` and `:408`.

### F8 · S8's injection point does not exist
> "Injected at the point in `route_for_task` that already has `project` bound, after `repo_context` normalisation at `broker_runtime.py:560`."

`route_for_task` is defined at `server/router_cutover.py:245` and takes `(task, project, max_results, *, flag, open_session, ide)`. It has **no `repo_context` parameter and no `repo_context` reference anywhere in that file**. `broker_runtime.py:560` is `repo_context = repo_context or {}` inside `create_task_run`, a different function in a different module. The instruction describes a coordinate that exists in neither location.

**Fix:** name one function and one line, with the surrounding statement quoted, e.g. "in `create_task_run`, immediately after `repo_context = repo_context or {}` (`broker_runtime.py:560`), insert `repo_ctx["project_memory"] = get_project_context(project)`".

### F9 · S2's verifier signature contradicts its own gate
> "**Outputs** `verify(task_type)` returning `correct|wrong|skip`"
> "G5 — `verify('general','.')` outcome equals a hand-run `compileall` exit code"

One argument declared, two passed. The implementer must invent the second parameter's name and meaning (a root path? a target?), and must also invent which "subset" `coding` runs.

**Fix:** `verify(task_type: str, root: str = ".") -> Literal["correct","wrong","skip"]` and name the coding subset explicitly (e.g. `pytest tests/test_router_unified.py -q`).

### F10 · S2's `skip` violates the database constraint
> "returning `correct|wrong|skip`"

`server/db.py:197` and `:217`: `outcome TEXT CHECK(outcome IN ('correct','wrong','partial','pending','routed'))`. `skip` is not a legal value, and `partial` — present in S1's terminal set — can never be produced by this verifier. The plan never states how `skip` persists (no write? a `pending` row? a new column?).

**Fix:** map `skip → 'pending'` (or "no write") in the step text, and state how `partial` survives when the verifier cannot emit it.

### F11 · S3 names no artifact at all
> "**Outputs** one function behind HTTP, MCP and broker. Under option B the daemon gets `/nexus/route` and `/nexus/search` aliases"

No function name, no file, no signature, no handler names, no statement of what "duplicate entry points removed" deletes from. This is the largest change in the list (it touches `router_cutover.py`, `unified.py`, `nexus_http_daemon.py`) and has the least specification. "One function" is a description of intent, not an action.

**Fix:** `def route_query(task: str, project: str = "default", max_results: int = 5) -> dict` in `server/router_cutover.py`, with the two daemon handlers `async def nexus_route(req: RouteRequest)` and `async def nexus_search(req: SearchRequest)` and their exact bodies.

### F12 · No step assigns its gate to a test file
README correction 3 is explicit: "Gate test files named in the plan don't exist (`test_router_unified.py`, `test_learning_loop.py`, `test_no_fake_verify.py`, `test_project_boost.py`). **Create them under `tests/`.**" `EXECUTION.md` never assigns a single gate to a file. `tests/` appears exactly twice, both incidental (`conftest.py` as an owner, `test_unified.py:244-248` as a regression check). Eleven gates have no home.

**Fix:** add a `**Test**` field to every step: file path, test function name, and the exact assertion.

### F13 (gate) · The database-fingerprint gate can pass while writes occur
> "`md5sum db/nexus.db` unchanged across the run (proves S0 works)"

`server/broker_runtime.py:68` sets `PRAGMA journal_mode=WAL`. WAL writes land in `db/nexus.db-wal`; `db/nexus.db` is untouched until auto-checkpoint. A test run that writes the live DB can therefore leave the main file's md5 identical — the gate passes while the thing it tests has failed. `db/nexus.db-wal` does not currently exist (checkpointed clean), so the first dirty run creates it, which is exactly the signal the gate should read.

**Fix:** fingerprint the write set, not one file: `md5sum db/nexus.db db/nexus.db-wal 2>/dev/null` before and after, plus `ls db/nexus.db-wal` must report "No such file" after a clean run.

### F14 (gate) · S0's reverse-order gate has no mechanism
> "the same suite passes with the tests run in reverse order"

`pytest-reverse` is not installed in either virtualenv (`import pytest_reverse` → `ModuleNotFoundError`), and no alternative command is given. The repo also contains two environments (`.venv` and `venv`, both pytest 9.0.3) and the plan never says which interpreter runs `pytest tests -q` — the load-bearing command for every gate in §6. An executor must guess both the interpreter and the reverse mechanism.

**Fix:** state `./.venv/bin/python -m pytest tests -q` (pick one env and say why) and either add `pytest-reverse` to `requirements.txt` in S0 or replace the requirement with a named, runnable alternative.

---

## 3. Majors — ambiguity, miscounts, unverifiable claims

### F15 · §2's tally does not match its own table
> "Net: **7 build · 4 shrink · 3 drop/defer.**"

The table uses 13 distinct verdict strings and, counted as written, yields **7 keep / 2 shrink / 2 cut / 3 drop-defer** (7+2+2+3 = 14). The two rows labelled "cut to deletion" (A1) and "cut to gating only" (C2) are silently relabelled "shrink" to make the arithmetic reach 14. The total is right; the buckets are wrong, and a reader who takes "4 shrink" at face value believes A1 and C2 preserve surface area when the whole point is that they delete it.

**Fix:** "Net: **7 keep · 2 shrink · 2 cut · 3 drop/defer**", or define the four verdict tokens at the top of §2 and use only those throughout the table (currently `keep, halve` / `keep, shrink` / `shrink to X` / `cut to X` / `drop` / `drop for now` / `defer` overlap without a scale).

### F16 · §5 says "three", lists two gates and one change ID
> "The README's G1–G10 are not all safe as written. Three pass without checking anything:"
> "G1 … G7 … A4  `assert status != 404`  → passes on a 500"

Two of the three entries are gates (G1, G7). The third is `A4` — a **change** identifier, not a gate; the README assigns A4 no `G` number. A reader looking for gate `A4` finds nothing, and the count of three cannot be reconciled against a list of ten gates.

**Fix:** introduce the third entry by its artifact: "A4's acceptance check (`assert status != 404`)", and say which gate number it will be when the A4 step lands (or state that A4 has no gate and that the absence is the defect).

### F17 · Four gate IDs are silently redefined
README maps `G4 = "Production routing never returns correct"`, `G5 = "Real verifier matches pytest exit code"`, `G9 = "`/nexus/route` response has harness key"`, `G10 = "`/health` reports all_pass: true"`. `EXECUTION.md` reuses the same IDs with different meanings:

- S2: "G4 — `route_task` source contains no `_evaluate_outcome(`" (a grep, not a routing property)
- S2: "G5 — `verify('general','.')` outcome equals a hand-run `compileall` exit code" (compileall, not pytest)
- S9: "G9 must assert those names; there is no `harness` key and there never was" (correctly flagged as a correction — the one that is)
- S11: "G10 — `all_pass: true`" against `/health/deep`, while README's G10 is `/health` and S11 also requires "Existing `/health` on both servers unchanged"

Three redefinitions are made silently while §5 announces that gates "must be rewritten". Gate IDs are the only cross-document index; changing their meaning without a note breaks the README↔EXECUTION mapping.

**Fix:** annotate each redefinition: `G4 (was: "never returns correct"; now: no call to _evaluate_outcome in route_task — the old check cannot fail because the caller passes a literal)`.

### F18 · "11 of 12 steps" is not supported by the steps
> "**G0 unresolved.** 11 of 12 steps name a route that 404s."

Enumerating the per-step text, only S3 names a `/nexus/*` route; S4, S5, S7, S11 refer to HTTP paths without naming them (`one GET`, "over HTTP", "the HTTP path", `/health/deep` — the last being a route that does not exist yet, so it cannot 404). S0, S1, S2, S6, S8, S9 and S10 name no route. The aggregate claim is checkable in principle and wrong as written.

**Fix:** "5 of 12 steps depend on a `/nexus/*` route that returns 404 today (S3, S4, S5, S7, S11); the rest are file-level and unaffected by G0", or list the 11.

### F19 · §7's "all 5 missing" contradicts the four files named in README
> "| plan test files | `ls tests/` | all 5 missing |"

README correction 3 names four files. §2 says the original 14 changes add "5 new test files". The fifth is never identified, so the row cannot be verified even by a reader who checks `ls tests/`.

**Fix:** name all five paths in the cell, or write "4 of the 4 files named in README correction 3 are absent; no fifth file is specified anywhere".

### F20 · S9 presents a new function as existing
> "**Owner** `core/config.py` (`is_harness_eager()`), `server/execution_plane.py:183`"

`grep -rn "is_harness_eager\|harness_eager"` returns nothing. The function does not exist; only `os.environ.get("NEXUS_HARNESS_EAGER", "false")` at `execution_plane.py:183` does. Writing a to-be-created symbol in an "Owner" field reads as "modify this existing thing". Every other new artifact in the document is marked "new" (S2, S11); this one is not.

**Fix:** "**Owner** `core/config.py` (**new** `is_harness_eager() -> bool`, default `True`), `server/execution_plane.py:183`".

### F21 · S9's verification has no pass condition
> "**Verification** G9 on the real keys; benchmark with `open_session=False` against a copied DB"

"Benchmark" against no threshold, no command, no comparison point. G9 asserts key presence, which any response with the keys passes regardless of speed. A gate that checks the name of a field and calls itself a benchmark is a gate that can pass while checking nothing.

**Fix:** a number: "p95 `open_session=False` routing latency on the copied DB ≤ baseline p95 + 5 ms (measure `<command>`), and `NEXUS_HARNESS_EAGER=false` reproduces baseline within noise".

### F22 · S1 leaves the shape of its central artifact open
> "**Outputs** one function that writes outcome + counters; `event_bridge.record_outcome` reduced to a delegator or removed after checking its two callers"

No name, no file, no signature — and "delegator or removed" is an explicit design choice handed to the implementer, which the Zero-Decision rule forbids. "after checking its two callers" also defers a discovery step that the plan should have already performed.

**Fix:** `def record_outcome(conn, task_run_id: str, outcome: str, *, source: str) -> None` in `server/learning.py`; `event_bridge.record_outcome` becomes a two-line delegator (state this, do not offer the alternative); name both callers with line numbers.

### F23 · S7's boost has no magnitude
> "a bounded additive boost, applied to project-matched candidates only"

"Bounded" by what, and additive on which scale? `_v5_rerank` scores are not normalised in the step text, so "bounded" is unquantified and the rollback ("set the boost constant to 0") implies a constant that is never named or valued.

**Fix:** `PROJECT_BOOST = 0.15` applied to `candidate["score"]` for `candidate["resource_name"] in project_resources[project]`, with the observed score range stated so the reviewer can judge 0.15.

### F24 · S10's staleness window is never given a value
> "start two MCP processes, both appear; kill one, it disappears within the staleness window"

A gate whose threshold is a name. Also: "reads from `project_continuity`'s persisted rows" names no table, no query, and no dedupe rule when a session appears in both the in-process manager and the persisted rows.

**Fix:** "appears within `STALE_AFTER = 30 s` (constant in `ide_manager.py`); dedupe on `(ide_name, pid)`, persisted row wins".

### F25 · S11 names a module that does not exist and checks it never enumerates
> "**Owner** new `run_sanity()` in `server/health.py`"
> "`GET /health/deep` → `{all_pass, checks[]}`"

`server/health.py` does not exist (`ls` → "No such file or directory"). "new `run_sanity()` in X" reads as "add a function to an existing module"; the module is also new. And `checks[]` is never enumerated, so the 503-on-failure gate ("503 when a check is forced false") cannot be executed — there is nothing to force.

**Fix:** "**Owner** new file `server/health.py` (≈40 lines) exposing `run_sanity() -> dict`; checks: `db_openable` (`mode=ro` connect), `schema_version >= 21`, `router_flag_in {0,1,2}`; each returns `{name, ok, detail}`".

### F26 · S6 leaves the allow-list source unnamed and the interactions undefined
> "that every tool row has a non-NULL `side_effect_level` and that the allow-list is non-empty"
> "a row with `side_effect_level=\"write\"` is blocked at `allow_writes=False`"

Three undecided things: which table populates the allow-list; whether `allow_writes=True` overrides `BLOCKED`; and why the gate tests only `allow_writes=False`, which is the case where blocking is expected anyway. A gate that only exercises the permissive-by-construction branch cannot detect the regression it exists to catch.

**Fix:** "allow-list = `resource_policies` rows where `side_effect_level='read'`; `BLOCKED` applies unconditionally and `allow_writes` only widens the allowed set to `{read, operational}`; gate asserts both `allow_writes=False` and `True` block `write`".

### F27 · S8's performance gate has no method
> "route latency increase under 5 ms p95 with a warm cache"

No baseline value, no command, no sample size, "warm cache" undefined. Also "two queries" is the entire SQL specification for a function with a 4 KB cap.

**Fix:** give the two statements verbatim (or their tables and filters), and the measurement: "`<n>=200` iterations of `route_for_task('x', project='nexus')` against `mode=ro`, p95 ≤ baseline p95 + 5 ms, baseline recorded in the commit body".

### F28 · §2's inventory is unattributed and contradicts S10
> "Building all 14 adds roughly 4 new modules, 3 new endpoints, 1 new table, 1 new supervisor topology and 5 new test files"

None of the 4 modules or the 1 table is identified. S10 states "**No `ide_sessions` table.**" and S2 is the only step adding a module (`verify_registry.py`); S11 adds a file the plan calls existing. The quantities describe the *rejected* plan, but the paragraph reads as a description of this one.

**Fix:** open with "The 14-change plan as written would have added…" and, where the quantity matters downstream (test files), name them.

### F29 · The rollback point is stored where it will be deleted
> "Rollback point for the whole plan: `~/.hermes/cache/scratch/nexus.before-pytest.db` (624 MB, 2026-11-10 19:18)."

That directory is pruned of idle entries on a 24-hour cycle. The single restore artifact for an 11-change, 624 MB database migration sits in a cache directory, with no restore command and no checksum. A future session following this plan will find nothing.

**Fix:** copy to `db/backups/nexus.before-remediation.2026-11-10.db` (the directory already exists), record `sha256sum`, and give the restore line: `cp db/backups/… db/nexus.db && rm -f db/nexus.db-wal db/nexus.db-shm`.

### F30 · §1's "written for B" is contradicted three times in §4
> "Everything below is written for B; the A-only deltas are marked. Do not start S1 until this is answered."

§4 does not deliver that. S3 gives both branches ("Under option B … under option A"), S4 says "`server/unified.py` or the daemon (per G0)", S11 says "route on `unified.py` (or daemon under B)". Three of twelve steps are unresolved on the axis the document calls "the one decision that gates everything" — and only one of them is marked as an A-delta.

**Fix:** pick B and delete the A branches from S3/S4/S11 into a short "If A" appendix, or change §1 to "Steps S3, S4 and S11 remain blocked on G0; the rest are written for B."

### F31 · §5's promise is not carried into §4
> "## 5. Gates that must be rewritten before use"

Only G1 (S3), G7 (S7) and the A4 check get rewrite guidance. G2, G3, G5, G6, G8, G9 and G10 are cited in §4 in their README form with no statement of assertion form, even though the section says all unsafe gates must be rewritten first. A reader cannot tell which gates survive as written and which do not.

**Fix:** one row per gate: ID, old check, new check (verbatim assertion), file, "safe as written: yes/no".

### F32 · S4 gates on a predicate that does not exist
> "gated by `get_learning_config().is_enable_dashboard()`"

`grep -rn "enable_dashboard"` returns nothing. `LearningConfig` exists (`core/config.py:48`) with no such member. The step also names no route path and no handler, so the implementer invents both the flag and the endpoint.

**Fix:** "add `enable_dashboard: bool = False` (env `NEXUS_ENABLE_DASHBOARD`) to `LearningConfig` in `core/config.py:48`; route `GET /nexus/status` on the daemon (`nexus_http_daemon.py`, beside `@app.get("/health")` at :352)".

### F33 · Line 38 states an unverified prediction as measured fact
> "the daemon's 4-field `/health` clients break"

The document opens with "Every claim about the running system was probed on 2026-11-10". No client is identified, no probe of client behaviour appears in §7, and "4-field" is asserted without a field list. This is the only drift into future tense presented as present fact; it is also load-bearing for the A/B comparison, since it is one of the three costs attributed to option A.

**Fix:** "A: `/health` currently returns 4 fields (`/health`, `nexus_http_daemon.py:352`) — any client reading a field that `unified.py` does not emit breaks; clients not enumerated."

---

## 4. Minors

### F34 · "11 tests later" reads as the step count
> "Fixing this once is cheaper than fixing 11 tests later"

Eleven is also the number of steps after S0. The sentence is about tests; the reader cannot tell whether the two elevens are coincidence.

**Fix:** "than fixing the isolation in 11 separate test files later".

### F35 · Colloquialisms that survive in otherwise dry prose
> "`create_task_run` caller at `unified.py:457` **suddenly** becomes live code"
> "the old \"120 s timeout always fires\" premise **is dead**"
> "they become two routers **by accident**"
> "Cheapest **real safety win** in the set"

None is banned-adjective marketing; all four are register breaks in a document that elsewhere writes "Terminal outcome increments `times_correct`". "Suddenly" also carries meaning it cannot support — the code goes live when the process changes, not at a moment.

**Fix:** "becomes reachable code"; "the premise is false: 120 s > 89 s"; "they become two routers"; "lowest-cost safety change in the set".

### F36 · Two meanings of "Owner"
> "**Owner** `tests/conftest.py`" (§4) vs "**G0 — who owns the HTTP surface?**" / "one process, one port, one owner" (§1)

In §1 "owner" is the process/runtime that holds the port; in §4 it is the file list. Three pages apart, no disambiguation.

**Fix:** rename §4's field to "**Files**" (it lists files, not owners) and leave "owner" to mean the process.

### F37 · "the owner picks A or B" names no role and no record
> "**Action:** the owner picks A or B. … Do not start S1 until this is answered."

The only human decision in the plan has no named decision-maker, no deadline, and no place where the answer is written down. A fresh session cannot tell whether the decision has been made, only that it must not start S1.

**Fix:** "Decision owner: `<role>`; record the answer in this file under §1 as `**G0 decision:** B (2026-11-DD)`; S1 is unblocked only when that line exists."

### F38 · Presentation nits
- §7: "| `/health` sites | grep | `unified.py:1008`, `nexus_http_daemon.py:352`, `core/server.py`; …" — the third path has no line number where the other two do.
- §3: "S0  test isolation fixture        ← blocks everything, no decision needed" — the arrow is used once and never again.
- Line 6: "primary-source research (§6, filled by the research task)" — passive past/present ambiguity; §6 and `RESEARCH-primary-sources.md` are both present, so say "filed".
- Line 272: "`RESEARCH-primary-sources.md` in this directory (171 lines; …)" — file length is not evidence of anything; verified accurate at 171, but it argues nothing.
- `G0` shares a prefix with `G1`–`G10` but is a decision, not a gate. "G0" appears 3 times, always as a blocker. Rename to `D0` or "Decision 1".
- S1 cites `broker_runtime.py:1106` for the terminal set; the literal is at `:1105`. One-line drift, in a document that asks its readers to re-locate every line with `grep` — apply the same rule here.

---

## 5. Banned adjectives and filler — PASS

Searched the full text for `great, brilliant, seamless, robust, powerful, leverage, essentially, simply, obviously, clearly, world-class, best-in-class, cutting-edge, holistic, synergistic, it is important to note` — **zero matches**. No finding to quote. The nearest thing to evaluative language is "real" used as an intensifier twice ("the cheapest real safety win", "a real exit code"), which is defensible in both places.

The document is also free of hedging in its measurements: §6's interval arithmetic is correct (Wilson 95% on 0.600/105 → [0.504, 0.689], half-width 9.2; 73/105 = 0.695 is indeed the first count whose lower bound clears 0.600 at 0.6016; 72/105 falls to 0.5917), and the §7 probe rows for the daemon, the 404s, the suite cost, the missing `bandit`, the missing migrations directory and the stale PIDs are consistent with the repository.

Where style fails, it fails in the other direction: the *substance* of steps is thin and the *measurements* are thick. The document is confident about what it observed and vague about what it will build.

---

## 6. What would move this to PASS

1. Split every step into **Files** (path:line), **Change** (verbatim before/after or function signature), **Test** (file::function + assertion), **Rollback** (command). Four fields, no prose.
2. Replace the eleven gate IDs with assertions written out in full, and drop the ones that cannot fail (`!= 404`, "non-NULL", "of your own", "benchmark").
3. Delete the four artifacts that do not exist (`_invoke_tool`, `project_resources`, `is_enable_dashboard`, `server/health.py`) or declare them new with DDL/signature.
4. Resolve G0 to a single branch and remove the other from §4.
5. Fix the taxonomy in §2 to match its own table.
6. Move the rollback database out of the pruned cache directory and give the restore command.

---

## Findings index

| # | Severity | Section |
|---|---|---|
| F1 | BLOCKER | S5 — wrong owners, G8 unsatisfiable |
| F2 | BLOCKER | S6 — `_invoke_tool` absent |
| F3 | BLOCKER | S6 — boot assertion vacuous |
| F4 | BLOCKER | S6 — wrong table, `operational` unhandled |
| F5 | BLOCKER | S6 — "G-gate of your own" |
| F6 | BLOCKER | S7 — `project_resources` absent |
| F7 | MAJOR | S7 — `router_v2/v5.py` path |
| F8 | BLOCKER | S8 — injection point does not exist |
| F9 | BLOCKER | S2 — `verify()` arity contradiction |
| F10 | BLOCKER | S2 — `skip` outside CHECK constraint |
| F11 | BLOCKER | S3 — no artifact named |
| F12 | BLOCKER | §4 — no test file assigned |
| F13 | BLOCKER | §6 gate 2 — md5 on WAL database |
| F14 | MAJOR | S0 — reverse-order gate has no mechanism |
| F15 | MAJOR | §2 — tally vs table |
| F16 | MAJOR | §5 — "Three" lists A4 |
| F17 | MAJOR | §4 — G4/G5/G9/G10 redefined |
| F18 | MAJOR | §8 — "11 of 12" unsupported |
| F19 | MAJOR | §7 — "all 5 missing" |
| F20 | MAJOR | S9 — `is_harness_eager` not new-marked |
| F21 | MAJOR | S9 — benchmark without threshold |
| F22 | MAJOR | S1 — unnamed function, open choice |
| F23 | MAJOR | S7 — boost magnitude unstated |
| F24 | MAJOR | S10 — staleness window undefined |
| F25 | MAJOR | S11 — `server/health.py` absent, checks unenumerated |
| F26 | MAJOR | S6 — allow-list source, `allow_writes` interaction |
| F27 | MAJOR | S8 — latency gate method |
| F28 | MAJOR | §2 — unattributed inventory |
| F29 | MAJOR | §7 — rollback in pruned cache dir |
| F30 | MAJOR | §1 — "written for B" contradicted |
| F31 | MAJOR | §5 — rewrite promise not carried |
| F32 | MAJOR | S4 — `is_enable_dashboard` absent |
| F33 | MAJOR | §1 — `clients break` stated as probed |
| F34 | MINOR | §3 — "11 tests later" |
| F35 | MINOR | §2/S2 — colloquialisms |
| F36 | MINOR | §1/§4 — two meanings of "Owner" |
| F37 | MINOR | §1 — decision-maker unnamed |
| F38 | NIT | §3/§6/§7/S1 — presentation |
