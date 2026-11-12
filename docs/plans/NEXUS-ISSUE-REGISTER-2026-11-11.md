# NEXUS Issue Register and Execution Plan (2026-11-11)

Single list of every known NEXUS issue, sorted by severity, then dependency. Supersedes the
per-plan status tables in `remediation/README.md`, `EXECUTION.md` and `FIX-DESIGN.md` for
tracking; those documents keep their design text.

Status key: **DONE-V** verified this session (test or read); **DONE-S** commit subject only,
not checked against the diff; **OPEN**; **DECIDE** needs an owner decision (default in brackets).

Baseline at start: 606 passed / 24 skipped. Current: **616 passed / 24 skipped, 0 failed**.

## P0 — live harm (wrong data reaches routing today)

| ID | Issue | Evidence | Status | Gate / action |
|---|---|---|---|---|
| I-01 | Timeout (`skip`) counted as a wrong outcome | `server/learning.py` final `else` branch; `event_bridge.py:149` maps timeout to skip | DONE-V | Early return for `skip`; test in `tests/test_site_outcome_learning.py` (parametrized) |
| I-02 | Partial outcome moved counters by 0.5/0.5 | `learning.py` partial branch | DONE-V | Telemetry-only; dead partial branch removed; root test updated |
| I-03 | Root test deletes rows from and writes to the live DB; order-dependent | `test_edges_and_reflection.py` | DONE-V | Runs on a backup-API copy; `server.router` and `server.learning` DB paths forced; `random` seeded; live DB sha256 unchanged across runs |
| I-04 | Literal `outcome="correct"` writer in the context engine | `server/context_harness/engine.py:299` | OPEN | Label `synthetic` or drop the write (T04) |
| I-05 | Literal `outcome="correct"` writer in the RL feedback loop | `server/rl_feedback_loop.py:296` | OPEN | Same as I-04 (T04) |
| I-06 | `outcome_source` defaults to `"explicit"` for any caller that omits it | `learning.py:339` | OPEN | Remove default, require a source (T04) |
| I-07 | 270 timeout rows labelled `correct` in `routing_log` | Read-only query: `outcome_source='timeout'`, `outcome='correct'` | DECIDE | Default: exclude from learning queries, keep rows; deletion needs a count review |
| I-08 | ~9,770 `routing_log` rows with no provenance | Read-only query: `outcome_source IS NULL` | DECIDE | Default: exclude from the shadow re-ranker; do not backfill |
| I-09 | Sweeper wrote `correct` on unobserved results | `broker_runtime.py:1100-1107` | DONE-V | Now writes `unverified` with `outcome_source="timeout"` |
| I-10 | `auto_execute` defaulted to True | `orchestrator.py:358` | DONE-V | Default False; no True default found in broker, MCP or unified |
| I-11 | Dashboard binds `0.0.0.0` with no auth | `server/dashboard/server.py`; writes are loopback-only | DECIDE | Default: bind to `127.0.0.1` (not changed yet) |
| I-12 | Dashboard and other `/tmp` DB fallback | `server/db.py` | DONE-S | Subject `ebf1ea9`; no `/tmp/nexus.db` in `db.py` (grep) |

## P1 — correctness and routing

| ID | Issue | Evidence | Status | Gate / action |
|---|---|---|---|---|
| I-13 | Harness sites are not wired into backend registration | `build_backends` has no caller outside tests; `context_harness/server.py` still has 13 hardcoded registrations | OPEN | One `build_backends` call; parity test (T13) |
| I-14 | Sites cannot be routed as resources | No `site` resource type | OPEN | After I-13 (T14) |
| I-15 | Site outcomes have no path of their own | Only skill names resolve in `learning.py` | OPEN | Generic path through `record_outcome` is in place; site-specific wiring after I-14 |
| I-16 | FAISS index stale: 1,200 vectors vs 1,861 active resources | Read-only count; index built 2026-07-11 | OPEN | Find the reader, rebuild, record the gap (T10) |
| I-17 | `cluster_divergence` gate fails (254 vs 0) | `ops_metrics.py:144`; `catalog_lifecycle.py:212` | OPEN | Find the cause before changing the gate (T12) |
| I-18 | v5 latency and golden pass rate unmeasured | No measurement since 2026-06-27 (v2 path) | OPEN | Benchmark v5 with per-layer timing; score golden cases (T07, T08) |
| I-19 | Legacy `route_task` runs on every v5 route (layer L1) | `router_v2/v5.py:204-215` | OPEN | Ablation decides keep or remove (T11) |
| I-20 | Verification returns a constant `True` | FIX-DESIGN S2-b | OPEN | Verify registry (S2) |
| I-21 | Self-graded router table | FIX-DESIGN S2-a | DONE-S | Subject `be49e23`; not diff-checked |
| I-22 | Broker writes `executed` with NULL outcome | FIX-DESIGN S1-a | DONE-S | Subject `b7f4712` / `04a03c7`; not diff-checked |
| I-23 | `ended_at` corrupted on session open | FIX-DESIGN S1-d | OPEN | No subject in the last 30 commits |
| I-24 | Duplicate outcome writers (`event_bridge` and `learning`) | FIX-DESIGN S1-e | OPEN | One writer (S1) |
| I-25 | Quality `0.0` turned into `1.0` | FIX-DESIGN S1-c | DONE-S | Subject `04a03c7` |
| I-26 | Orchestrator attests an approval it never got | FIX-DESIGN T1-1 | DONE-S | Subject `be49e23` |
| I-27 | Reflector decides retries with a seeded coin | FIX-DESIGN T1-2 | DONE-S | Subject `be49e23`; the router's own `random.random()` (lines 726, 1020) is still present and is exploration, not a verdict |

## P1 — security on live surfaces

| ID | Issue | Status | Note |
|---|---|---|---|
| I-28 | SSRF in fetch paths (T2-5) | DONE-S | `48f8de9`; `net_guard` re-checks every redirect hop; the new user-site channel uses it |
| I-29 | `ruflo_invoke` forwards any tool name (T2-1) | OPEN | Allowlist needed |
| I-30 | `mode` parameter widens its own permissions (T2-2) | DONE-S | `ebf1ea9` |
| I-31 | Unified server binds `0.0.0.0` without auth (T2-3) | DONE-S | `ebf1ea9`; check the current bind before relying on it |
| I-32 | Supervisor and tmux name injection (T2-4) | OPEN | Unverified in the audit; not opened |
| I-33 | scrapecraft reads a key from `~/.bashrc` (T2-6) | DONE-S | `48f8de9` |
| I-34 | `forge` kills projects by prefix (T2-7) | DONE-S | `48f8de9` |
| I-35 | Untrusted harvested text stored without markers (T6-1) | OPEN | Precondition for reviving ingest |

## P2 — data hygiene and structure

| ID | Issue | Status | Action |
|---|---|---|---|
| I-36 | Seeders write benchmark and routing rows into live data (T3-1) | OPEN | Stop seeders writing to the live DB |
| I-37 | Four overlapping plans with colliding ids (remediation A1 ≠ macro A1, etc.) | OPEN | One id space under FIX-DESIGN ids (T19) |
| I-38 | Remediation statuses disagree between README and EXECUTION | OPEN | Reconcile (T19) |
| I-39 | Migration numbering conflict (local harness 020/021 vs planned 022) | OPEN | Decide numbering before the next migration |
| I-40 | "Phase 1" means two different things | OPEN | Rename one |
| I-41 | 91 uncommitted working-tree changes; much of the new work is untracked | DECIDE | Default: commit on a branch after review |
| I-42 | Dead code and the legacy router (T4, T20) | OPEN | Delete one module per change, after the import closure (T09) |
| I-43 | GitNexus index stale (KuzuDB version mismatch) | OPEN | Blocked on the tool; grep-based impact analysis is the fallback |
| I-44 | Stale docs: `docs/design/exec/05-EXEC-SITES.md` says the loader does not exist | OPEN | Update (R7 follow-up) |
| I-45 | W5 adapter design doc is missing repo sources and has wrong line citations | OPEN | Mark untrusted; rewrite when adapter work starts |

## Execution log (this session)

1. Verified the outcome provenance numbers from the revised plan (315 explicit, 276 correct or wrong, 270 timeout, about 9,770 unsourced). They match.
2. **T02**: isolated `test_edges_and_reflection.py` (I-03); partial assertion changed to "unchanged".
3. **T03**: `skip` is telemetry-only (I-01); dead partial branch deleted (I-02); `skip` added to the parametrized test.
4. Full suite: 616 passed, 24 skipped, 0 failed.

## Next, in order

1. **T04** (I-04, I-05, I-06): remove the `"explicit"` default; label the two literal writers `synthetic`. Gate: a call with no source fails; synthetic writes change no counter.
2. **T05** (I-07, I-08): read-only outcome audit script, `scripts/outcome_audit.py`. Gate: reproduces the counts above.
3. **T06**: source gate, so only `explicit` moves counters (depends on T04).
4. **T07, T08, T09** (I-18, I-19): benchmark v5, score the golden cases, compute the import closure. All read-only.
5. **T12** (I-17): `cluster_divergence` root cause.
6. **T10** (I-16): FAISS staleness.
7. **T13, T14** (I-13, I-14): wire the site loader, then route sites.
8. **T15** (shadow class-level re-ranker, per the classifier design), then **T11** (L1 ablation) on those numbers.
9. **T19, T20** (I-37, I-38, I-42): plan consolidation and dead-code removal.

## Decisions needed before steps 1, 7 and 9

- **I-41** Commit the work? Default: a branch, after a review of the diff.
- **I-07 / I-08** Purge or exclude the unsourced and timeout-as-correct rows? Default: exclude; no deletion until counts are reviewed.
- **I-11** Bind the dashboard to `127.0.0.1`? Default: yes.
- **I-19** Keep or remove legacy L1 routing? Default: decide from the T11 numbers.
- **I-40** Which "Phase 1" keeps the name?
