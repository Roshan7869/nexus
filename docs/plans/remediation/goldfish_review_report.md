# Goldfish Review — Resolution Report

Target: `EXECUTION.md` revision 1 (320 lines, 18.8 KB)
Reviewers: three Stage 9 Goldfish, amnesiac, persona `implementation_readiness`, rubric
`implementation-readiness-standard`. Each got the plan path and the persona only.
Full report: `stage9-implementation-readiness.md` (37,976 bytes).
Resolution date: 2026-11-10. Plan now revision 2 (26,053 bytes).

## Verdict

Revision 1 was **not executable as written**. Eleven findings were confirmed against source
with direct probes, not by trusting the reports. Nothing in this document is taken from a
Goldfish summary alone — every number below was re-measured by the Elephant.

The pattern behind most findings: revision 1 was written from the change files, and the change
files name seams that do not exist. Four symbols were invented by the source plan and inherited
without verification:

    _invoke_tool            0 hits in the tree
    is_enable_dashboard     0 hits in core/config.py
    is_harness_eager        0 hits
    isolated_home           0 hits

That is the review's real output. The individual line numbers are symptoms.

## PROPOSE — accepted, plan edited

| # | Finding | Evidence | Change in v2 |
|---|---|---|---|
| 1 | S6 gates on `tool_traces` (audit log) instead of `resource_policies` (policy table) | classes: policies read 2134 / local_write 482 / operational 8 / write 0; traces read 147 / write 6 / local_write 2 | S6 rewritten to gate policies; `BLOCKED={write,local_write}` replaced with `ALLOWED={"read"}` |
| 2 | `write` never occurs in the policy table, so the blocked set blocks nothing; `operational` (8 rows) is admitted | `GROUP BY side_effect_level` | same |
| 3 | 630 of 3254 resources have no policy row, and `side_effect or "read"` admits them | `resources LEFT JOIN resource_policies` → 630 | NULL now denies explicitly; verify query asserts on the join, not on `tool_traces` |
| 4 | S7 depends on `project_resources`, which does not exist, and no step creates it | `sqlite_master` → absent; §7 recorded 021 as last migration | migration 022 with the DDL from `B1-project-scope.md:26-33`, `.backup` first, `except sqlite3.Error` fallback |
| 5 | S9 names two inert sites; the real gate is an inline env check | `router_cutover.py:277` = `NEXUS_HARNESS_EAGER` default `false` inside `except: pass`; `execution_plane.py:183` is an echo in `health_v1()`; `core/config.py` has no `is_harness_eager` | S9 retargeted to `:277` + a real accessor + narrowing the bare except |
| 6 | S6 names `dispatch.py` and `_invoke_tool`; neither is the policy path | `_invoke_tool` 0 hits; `_resource_row:433`, `_policy_allows:447` | S6 owner corrected, plus `tests/test_side_effect_deny.py` as a file to author |
| 7 | S5 lists two `auto_execute` defaults; there are three | `broker_runtime:555`, `mcp:806`, `unified:156`, `orchestrator:357` (already False) | all three listed; G8 rewritten to POST with no field and assert zero new traces |
| 8 | G4 checks one call site; `_evaluate_outcome` runs at two | `router.py:995`, `router.py:1265` | both named; gate asserts an `outcome_source` value instead of a string's absence |
| 9 | G9 is green before the change — `attach.py:24-28` attaches the keys unconditionally | source | G9 must assert an artifact produced by the request |
| 10 | S8 injects after a `repo_context` that the signature does not have | `route_for_task(task, project, max_results, *, flag, open_session, ide)` | injection point moved to `route_for_task`, `:560` claim removed |
| 11 | No step restarts the daemon, so every HTTP gate tests the previous commit | unit `nexus-daemon` runs `nexus_http_daemon.py`, no reload | new §3.1 restart protocol; MCP legs recorded as pending owner restart |
| 12 | Gate 2 `md5sum db/nexus.db` cannot work | 624 MB WAL with a live writer | replaced with a `routing_log WHERE ide='pytest'` count delta |
| 13 | Isolation verification "run in reverse order" has no mechanism | `pip list` → pytest 9.0.3, pytest-asyncio 1.4.0 only | `pytest $(ls tests/test_*.py \| sort -r) -q` |
| 14 | Backup lives in a directory pruned after 24 h | scratch policy | copy to `db/backups/` before 022 runs |
| 15 | S10's table is infeasible: `projects` has no pid or heartbeat, `ide_sessions` does not exist | `PRAGMA table_info(projects)` | S10 becomes check-then-descope |

## PROPOSE — accepted, but the finding was partly wrong

| # | Finding | Correction |
|---|---|---|
| 16 | "S0's three-fixture patch already leaked 8 fixture `task_runs`" | The leak is real but larger and differently shaped: **71 `routing_log` rows with `ide='pytest'`**. My `task_id LIKE` query failed — `task_runs` has no `task_id` column — so that half is unverified and not claimed. |
| 17 | "G6 must return 400 for `project='default'`" | The reviewer was right that `default` is the live default (`unified.py:145`, `daemon:300`, `mcp:79`, `:806`; tests at `:419`/`:449`). It was wrong that this is satisfiable. v1's G6 would have broken working callers. G6 now asserts 400 for empty/unknown only. |

## CONTEST — rejected, with reason

| # | Finding | Why rejected |
|---|---|---|
| 18 | "Drop S6 entirely; gating an unimplemented dispatcher is theatre" | Rejected. C2's deliverable is the deny path, not execution. A gate that denies `local_write` is worth having before a dispatcher exists — it is what makes the later dispatcher safe. Kept and retargeted. |
| 19 | "S7 should hard-filter to project resources rather than boost" | Rejected. A hard filter needs `project_resources` populated for every project; it is empty, and empty-then-filter returns zero skills for every request. Boost degrades to no-op instead. This was already the Owner's decision in `REVIEW.md`. |
| 20 | "Merge S1 and S2 into one step" | Rejected. They change the writer and the producer of the same field; combining them makes a regression bisect to two changes instead of one. Order is the safety property. |
| 21 | "Whole-file review range was wrong; should have been split" | Rejected. §1 holds the gating decision, §5 the vacuous-gate list, §7 the measurements — a split would have sent reviewers sections that cannot be judged alone. Recorded as a deliberate exception, per the rubric. |

## Not claimed

- Two of three reviewers' report bodies were not preserved through compaction. Their findings
  reached this report only where they agreed with the visible one or with a probe. No finding
  here is attributed to a report I cannot read.
- The Reviewer's claim that `/health` clients break under option A remains a **prediction**,
  now labelled as one in §1. No client was identified.
- Whether `project_continuity` exists is unknown; S10 verifies it before building.

---

## Addendum — S0 execution, 2026-11-10

S0 is the only phase executed so far, and it is the one where the review's own model of the work
was wrong. Recorded here because it calibrates how much the remaining phase estimates can be
trusted.

The plan described S0 as two constants plus a fixture. It was **36 call sites in 36 files**. Four
mechanisms were invisible to reading and only appeared under measurement:

1. **A constructor default is not a module constant.** `core/learner.py:140` falls back to a
   module-level `NEXUS_DB` when no path is passed, and `NexusLearner()` is constructed at
   `server/router.py:144`, `:343`, `:1334`. Two sweeps for `^DB_PATH =` reported zero remaining
   bypasses while the suite still wrote two rows per run. The leak was found by wrapping
   `sqlite3.connect` and capturing a stack — the inventory method, not the inventory result, was
   the defect.
2. **Import insertion has an ordering constraint.** Nine modules open with
   `from __future__ import annotations`; an import placed above it produces a `SyntaxError` that
   stops the module parsing. The first bulk patch did exactly this and broke nine modules silently.
   Insertion points must be computed with `ast` and the output re-parsed before it is written.
3. **The reverse-order gate as written could not fail.** `ls tests/test_*.py` sees 38 of 45 files
   and 464 of 490 tests, because seven modules live in subdirectories. It reported a pass over a
   subset. Only `find tests -name 'test_*.py'` collects the suite.
4. **Isolation has a disk cost nobody budgeted.** Each run snapshots 596 MB; seven runs left
   4.38 GB with no cleanup until `atexit` was added. Snapshot cost itself is 3.9 s — the suite is
   104.44 s forward and 135.84 s reverse against a 101.30 s pre-S0 baseline, so the isolation is
   free in time and expensive only in space if it leaks.

**Verified after the change:** 490 passed / 24 skipped in both orders; `routing_log` total 10359
unchanged across the run; `ide='pytest'` held at 71; traced production opens 0; zero snapshot
directories left behind.

**Not fixed, deliberately:** nine `routing_log` rows leaked before isolation existed (`10350` →
`10359`). They are audit rows in a live table with a concurrent systemd writer; removing them is an
Owner decision. 15 operator scripts still hardcode the production path on purpose.

**Calibration for S1–S9:** every phase estimate in §3 was written from source reading. S0's
estimate was wrong by a factor of 18 in sites and missed four mechanisms. The remaining estimates
should be read as lower bounds, and each phase should keep its gate measurable by an independent
probe rather than by re-reading its own diff.
