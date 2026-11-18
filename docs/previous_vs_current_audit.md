# Previous vs Current Audit: Waves 0–2 + Adaptations Working?

**Date:** 2026-09-14 · **Method:** HEAD-vs-worktree probes (`git show HEAD:` + live imports), verifier, suites, Archify receipts, ops snapshots.
**Headline:** previous behavior bit-identical where promised; adaptations add 593 lines of lib + 5 proven capabilities. Nothing regressed.

## 1. Equivalence proofs (previous == current on legacy paths)

| Claim | Proof | Result |
|---|---|---|
| Fusion ranking unchanged | HEAD `fusion.py` extracted via `git show`, same 4-array input through both `v5_fusion_scores` | **identical** (`True`); keysets differ only by additive `shared: 0.0` |
| Gate verdicts unchanged | HEAD `precondition.py` vs current, `check()` over 7 tools + unknown | **8/8 identical**; `subscribe`/`set_and_notify`/`command_is_readonly` purely additive |
| Broker legacy path | all 6 pre-existing call sites omit new kwargs; broker + execution_plane suites | **green** (no caller touched) |
| MCP surface | 4 tools appended after `register_harness_tools`; `task_start/complete` untouched | **99 tools served live**, legacy names present |

## 2. What current adds (and proof each works)

| # | Addition | Lines/files | Working proof |
|---|---|---|---|
| 1 | `server/local_harness/` (context/session/loader/harness/playbook) | 6 files | round-trip test: inverse→deps→`dumped`→accumulator empty; chaos-100 clean |
| 2 | `opencode_adapter.py` | 1 | mapping/payload/config unit tests green |
| 3 | `redis_cache.py` | 1 | `get_redis() is None` default; fallback path tested |
| 4 | `nexus_exp.py` | 1 | dry-run emits slug; no worktree without `--apply` |
| 5 | `verify_grounding.py` | 1 | 27 cases, 0 FAIL (3 consecutive runs) |
| 6 | 020/021 SQL | 2 | double-apply clean + **applied live** (gap found here: no bootstrap runner — closed by direct idempotent DDL) |
| 7 | `tests/test_local_harness.py` | 32 tests | green (incl. broker/fusion/cutover regressions: 55–62 pass) |
| 8 | `skills/nexus-{router,eval,harness}` | 4 | exist, format-checked; **outside-in acceptance runs pending** (A09 criterion open) |
| 9 | `docs/nexus-maps/` ×5 | 5 JSON + 5 HTML | **all showcase 0/0 + `check` ok**, delivered with sha receipts |
| 10 | 13 design/build docs | ~3000 lines | graph: 125 docs / 170 code / 575 edges / 32 loops, `planned_missing` empty |

## 3. Are the OpenResearch adaptations working?

| Adaptation | Status | Evidence |
|---|---|---|
| A1 Harness registry | **working** | `get(None)/get('zzz')` → opencode fallback, no raise; one-file-add rule testable |
| A3 Playbook split | **working** | renders, contains no procedures (test); broker adoption behind `NEXUS_PLAYBOOK_V2` (still 0 — **planned, not yet flipped**) |
| A4 Read-only gate | **working** | 5 probe cases incl. conservative unknowns |
| A5 Watchdog/retry | **not started** | constants not yet added (M15 follow-up) |
| A6 Freeze policy | **half** | trigger enforced in SQL + `nexus_exp` dry-run; no frozen production rows yet (no sweeps run) |
| A7 Manifest | **working** | keys live in `report_session`; backfill-safe proven |
| A8 Literature policy | **doc only** | no code needed; compliance unmeasured |
| A9 Skill packs | **shipped, unproven** | outside-in runs pending |
| A10 Telemetry | **correctly absent** | no analytics added (opt-out contract documented for later) |

## 4. Is Archify working?

Yes — 5/5 diagrams showcase-clean and delivered (runtime, core, router workflow, task sequence, session lifecycle). Workflow took 7 diagnostic rounds (process note in audit); the rest ≤3. `dsh-plugin` for Archify itself was **not** installed (user declined the only install path available from sandbox).

## 5. Context: parallel development (not mine, not touched)

Untracked/in-flight by others: `server/dispatch.py` + `tests/test_dispatch.py` (A2-2 seam, also edits `v5.py`), `docs/design/`, `docs/research/`, `config/harness_sites.yaml`, pre-existing deletions. **M11 stays deferred until the A2-2 work lands**; my fusion hooks (`shared`, `recycle_spill`) are compatible by design (convex-only, default-off).

## 6. Residual gaps (owned, ordered)

1. MCP-spawn e2e listing new tools (stdio proven by pipe, not by IDE spawn).
2. A09 outside-in acceptance transcripts.
3. `NEXUS_PLAYBOOK_V2` flip after golden parity.
4. A5 constants + first `nexus_exp` A/B sweep.
5. Commit Waves 0–2 (explicit list ready) + third_party strategy.
6. Ops owners: dashboard stopped, http degraded (unchanged, unrelated).
