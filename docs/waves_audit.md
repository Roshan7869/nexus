# Waves 0–2 Audit: Executions, Operations, Functionality

**Date:** 2026-09-14 · **Auditor:** session agent (self-audit with machine gates, not a claim of independence)
**Scope:** all Wave 0–2 outputs — 15 new files, 7 modified tracked files, 4 map artifacts, 34 tests, 32 graph loops.
**Method:** `verify_grounding.py` (0 FAIL) · pytest (named suites) · Archify `validate`+`check` · live probes · `ops_dashboard` + `harness_dashboard` · manual `git diff` review (GitNexus MCP unavailable; index stale 2026-08-18).

## 1. Verdicts

| Area | Grade | Basis |
|---|---|---|
| Grounding (no hallucinations merged) | **A** | verifier 27 cases 0 FAIL; every MODIFY anchor greps; every CREATE was absent pre-build; graph `planned_missing` now empty |
| Test discipline | **A-** | 34-test suite green across 3 runs incl. broker + execution_plane + fusion + cutover regressions; 1 strict-xfail resolved by building M7 (not by weakening); 1 over-strict assertion of mine corrected, not the code |
| Default-behavior preservation | **A** | fusion ranking identical with plumbing on (probe: `default==shared-off: True`); `check()` semantics untouched (test); broker flag-off paths byte-identical by construction (all pre-existing callers omit new kwargs — 2/4/2 call sites verified) |
| New-functionality proof | **B+** | round-trip + probes live (sweep moves ranking; reconcile diffs; harness fallback; read-only classifier); MCP-spawn e2e (stdio tool list) NOT run — needs live IDE spawn, flagged as Wave 1 gap |
| Map artifacts | **A** | 4/4 IRs showcase 0/0 + 4/4 HTML `check` ok; frozen triples committed as files (JSON+HTML, receipts in deliver output) |
| Ops impact of changes | **A** | changes add no processes/ports/daemons; Redis default-off; env pins local-only (`.mcp.json` gitignored) |

## 2. Static audit (diff review)

Tracked diff from my work only: **+353 / −20 across 7 files** (`.gitignore` +1, shell +9, execution_plane +62, mcp +118 append-only, precondition +75, fusion +69, broker +39). All edits additive (new kwargs defaulting to legacy, new functions, appended tools). No signature changed; no caller updated, none needed.
New files (15): 6 `server/local_harness/`, `opencode_adapter.py`, `redis_cache.py`, `nexus_exp.py`, `verify_grounding.py`, 020/021 SQL, test suite, 4 skill files + index, 8 A/B docs, 4 map JSON + 4 map HTML, 5 parent docs.
Config/syntax: `.mcp.json` valid JSON; `bash -n` clean; SQL double-applies clean (`:memory:`); `ui`-style assets untouched.

## 3. Functional audit (live probes, 2026-09-14)

- `v5_fusion_scores` default vs shared-plumbed: **identical**; `NEXUS_V5_W_SHARED=0.10` sweep: weights renormalize (Σ=1.0, shared 0.0909), top score moves — tunability proven, defaults frozen.
- `shared_prior` bounded [0,1]; `recycle_spill` cap-0 identical, overflow order-kept.
- `diff_trees` empty→1-entry: added=[x] — reconcile core correct.
- `harness.get(None)/get('zzz')` → opencode fallback, no raise (compat).
- `command_is_readonly`: `list/show` True; `write/git push/orx exp run/novel` False (conservative verified).
- `local_harness_status`/`reconcile` paths: invalid-JSON arm tested; live-DB arms follow existing repo test norms (1 probe session row + archive + learning rows written — negligible vs 8806 routes, disclosed).
- Gap: stdio MCP spawn listing the 4 new tools not exercised (no live IDE spawn in this env).

## 4. Operations audit (live system, 2026-09-14T04:04Z)

- **Process gate: FAIL (33.3%) — PRE-EXISTING, unrelated.** `nexus-dashboard` stopped (required, expected RUNNING) + `nexus-http-daemon` degraded (pid without open :8080). My changes touch no processes, ports, supervisor units, or systemd files — cannot be the cause. Supervisor STOPPED for MCP/HTTP/harness = intentional per topology.
- **KG: healthy** — 3254 resources, 20942 edges, 0 zero-edge, gates pass.
- **Router: v5 production** (flag 2, cold 0.3); top-1 agreement target 0.95 noted for M10/M11 tuning.
- **Feedback: 96.14% coverage, EFC 100%, sessions 90/90 (100%)** — learning loop healthy; my probe rows included.
- **Harness: FTSR 68.49 global / 69.71 nexus**, 8806 closed routes, 36 active sessions (includes other agents' work, not mine — my probe session closed).

## 5. Hallucination incident log (all caught pre-merge)

1. `typing` import typo (Wave 0) — caught on re-read, fixed.
2. `ctx.effect/require/dispose` assumed on session while broker holds raw context (Wave 1, tests failed) — unified onto `NexusContext`, single implementation.
3. Garbage non-English token in a log string + stray CJK char in workflow JSON — fixed on sight; JSON re-validated.
4. Over-strict float assertion (mine, not code's) — corrected to behavioral equivalence.
5. Flag-semantics drift (`!=0` vs doc's `==1`) — aligned to doc.
6. Over-broad `orx`/`git` read-only allowlist — removed (would misclassify `orx exp run`).
7. Audit-loop filename→type derivation bug (mine) — fixed; maps re-proven 0/0.
8. Workflow took 7 diagnostic rounds (rule says stop at 2 non-improving) — every round cleared its error class; recorded as process debt: prefer structural simplification by round 4 next time.

## 6. Follow-ups (ordered)

1. **M11 coordination** — v5.py has another agent's uncommitted A2-2 seam; do not touch until merged/aligned. (Blocks Wave 2 completion.)
2. **Commit decision** — explicit file list ready (Waves 0–2 receipt in chat); third_party clones (75M) must stay out (submodule/ignore decision).
3. **MCP-spawn e2e** — list tools via live stdio spawn; close Wave 1 gap.
4. **Ops debt (not mine):** dashboard stopped + http degraded → owner action; GitNexus re-index (KuzuDB v43 skew) to restore `impact()` authority.
5. **A/B debut** — `NEXUS_V5_W_SHARED` sweep via `nexus_exp.py` worktrees (its first real job) + agreement/p95 receipts.
