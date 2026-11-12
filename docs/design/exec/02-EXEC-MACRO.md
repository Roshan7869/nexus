# NEXUS — Execution: Macro phases

Elaborates `02-PLAN-MACRO.md`. A macro phase advances only when its **exit gate** runs and passes. This
doc turns each exit gate into a script you actually run, and names the phase's kill condition.

Grammar: `exec/README.md` §1. Per-ticket steps: `03-EXEC-TICKETS.md`. **Corrections apply: P2 (I3),
P3 (A1-3), C2 (DB size), C9 (migration dirs).**

---

## PHASE 0 — Correct the record
*Entry: none · Duration: hours · Code change: none*

### P0-G0.1 — Evidence the three retractions
```
DO      the three claims and their verdicts are recorded in 02-PLAN-MACRO.md PHASE 0
ASSERT  each of the three has a verdict: wrong-mechanism / wrong-shape / subset
```

### P0-G0.2 — **Three MORE corrections from this pass** (add them)
```
DO      append to 02-PLAN-MACRO.md PHASE 0 and 06-CORRECTIONS.md:
          P1  `Fuser` -> `RRFFuser` (fusion.py:34)
          P2  I3's "no removal path" is FALSE — delete_tool (:155) exists and works
          P3  `/v1/ops` does NOT hang — 5/5 probes = 200 in 1.35-2.07 s
ASSERT  the parent docs no longer assert the false version
```

### P0-G0.3 — Exit gate
```
ASSERT  docs/design/ reflects all six corrections. No code touched. `git status` shows only docs.
```

**Kill condition:** if a "correction" cannot be reproduced by a command in this pass, it is not a
correction — it is a second unverified claim. Do not add it.

---

## PHASE 1 — Fundament (Track A)
*Entry: P0 exit · sections S1,S2,S3,S5,S7,S8 · ~60 LOC*

**Order is A4 → A1 → A2 → A3 and is not negotiable.**

### P1-G1.1 — `A4` deterministic suite (must be first)
```
DO      ./venv/bin/python -m pytest -q 2>&1 | tail -1     # default order
        ./venv/bin/python -m pytest -q -p no:randomly 2>&1 | tail -1
ASSERT  BEFORE: `2 failed, 374 passed`.  AFTER A4-1: `374 passed` in BOTH orders.
```
Runs first because it is the baseline that makes A2/A3 measurable.

### P1-G1.2 — `A1` one served surface
```
DO      for p in $(README-named routes); do curl -s -o /dev/null -m 5 -w "$p %{http_code}\n" \
          http://127.0.0.1:8000$p; done
        grep -n NEXUS_DAEMON_URL .mcp.json          # expect :33 -> 8000
        for i in 1 2 3 4 5; do curl -s -o /dev/null -m 5 -w '%{http_code} %{time_total}\n' \
          http://127.0.0.1:8000/v1/ops; done
ASSERT  every README-named route non-404 · POST /v1/chat/completions != 404 ·
        NEXUS_DAEMON_URL -> :8000 · /v1/ops 5/5 = 200 (it does not hang — P3)
```
**`/v1/ops` is no longer part of this gate's pass condition.** It already returns 200.

### P1-G1.3 — `A2` cold quota on the fast path
```
PRECONDITION  A2-0 returned "pool_size fails"
DO      seed one times_triggered=0 resource in a strong cluster (on a DB COPY)
        issue a query that trips the short-circuit
ASSERT  the seeded resource APPEARS
        router_ab_suite top-1 agreement NON-REGRESSING
        invoke_cold_target_pct >= 35.0 measured ON THE SHORT-CIRCUIT PATH
```
**If A2-0 returned "confidence fails":** the exit gate for A2 changes entirely — the deliverable is the
**deletion of `_short_circuit`** with proof it is unreachable, and this gate is satisfied by
`grep -c _v5_rerank`-style evidence that the path is gone. **Record which branch applied.**

### P1-G1.4 — `A3` one lifecycle column
```
DO      SELECT COUNT(*) FROM resources WHERE status != lifecycle_state AND lifecycle_state != 'quarantined'
        INSERT ... lifecycle_state='bogus'      -> must RAISE
        re-run the migration                   -> 0 rows changed
        SELECT COUNT(*) FROM resources          -> 3255
ASSERT  0 · raises · idempotent · 3,255 · version row present in schema_migrations
BLOCKED until the canonical migrations dir is named (C9)
```

### P1-G1.5 — PHASE EXIT GATE (all four, or the phase does not advance)
```
1  suite green (matching P1-G1.1's post-A4-1 baseline)
2  README routes non-404
3  router_mode = unified_v5
4  lifecycle_divergence_count = 0
```

---

## PHASE 2 — Deletion
*Entry: P1 exit · deletes 632 LOC + 95 files / 15,219 LOC*

### P2-G2.1 — `B1` the dead chain
```
CHECK   wc -l server/autonomous_pipeline.py server/router_v2/fts5.py server/test_v3.py
ASSERT  before: 396 / 215 / 380        (C3 corrected 397 / 216)
DO      git rm the three; run the suite
ASSERT  suite green; `grep -rn autonomous_pipeline --include=*.py` returns 0 (prose refs in
        scripts/auto_close_routes.py:25 and router_v2/scoring.py:26 are docs and may remain,
        but must not be IMPORTS)
```

### P2-G2.2 — `B2` the 95-file quarantine
```
DO      run _research/verify_delete_recovered.py in BATCHES OF 20
ASSERT  after all 95: suite green; 0 of 95 in place; restore path proven on SIGINT/TERM/HUP
```
**Never single-shot.** `04-FEASIBILITY.md` §5.3 — the prior attempt died at the 300 s cap.

### P2-G2.3 — `B3` the literal `$HOME` directory
```
DO      tar czf /tmp/stray_home.tgz './$HOME' && rm -rf './$HOME'
        fix the unexpanded-quoting bug in the creating script
ASSERT  path gone AND not recreated after a FULL script run
```

### P2-G2.4 — Do-NOT-delete list (verified live — do not re-kill)
```
KEEP    GLOBAL_GATES / precondition.py — LIVE, backs check_preconditions + update_state
        (precondition.py:65 StateGates, :114 GLOBAL_GATES = StateGates())
KEEP    HallucinationGate — constructed at router.py:321
ASSERT  neither appears in the deletion set
```

### P2-G2.5 — Exit gate
```
ASSERT  net -15,851 LOC · suite green · no live import of any deleted module
        (grep each deleted module name across *.py with imports only)
```

---

## PHASE 3 — THE DECISION
*Entry: P2 exit*

### P3-G3.1 — Answer it and write it down
```
QUESTION  Is NEXUS becoming a plugin host?
DO        record YES or NO + date in 00-HLD.md §6 and 02-PLAN-MACRO.md PHASE 3
ASSERT    a written answer exists
```
**An unanswered decision blocks P4.** "Probably eventually" resolves to **YES**.

### P3-G3.2 — Mark the losing branch
```
if NO:   ASSERT C1..C4 marked CANCELLED in 03-EXEC-TICKETS.md (not "pending")
         ASSERT the 15,851-LOC deletion (B) is scheduled
         ASSERT C0-1 alone is scoped (~30 LOC)
if YES:  ASSERT C1-1 prereq C0-1; C4-1 additionally needs the in-flight decision (P4-G4.3)
```

---

## PHASE 4 — CORDIS composition (YES branch only)
*Entry: P3 = YES · sections S3 (primary), S4, S5, S6, S7*

### P4-G4.1 — `C0` Disposer
```
ASSERT  _demo() passes: double close() no-op · post-close effect() raises Disposed ·
        reverse order · atomic() keeps the previous tree on failure
ASSERT  a study killed mid-harvest leaves NO orphan rows / temp files
ASSERT  wc -l server/disposer.py < its test file
```

### P4-G4.2 — `C1` Registry
```
BEFORE  grep -c "fuser.register(BackendDef" server/context_harness/server.py   -> 13
AFTER   same command                                                            -> 0
ASSERT  fusion.py `RRFFuser` reaches ZERO production callers   (P1 — not "Fuser")
ASSERT  all 13 backends still return results
ASSERT  a backend WITH close() is disposed; one WITHOUT still works
ASSERT  backend_protocol.py:142's declared policy is now SATISFIED, not merely stated
```
**Exact 13 lines:** 918, 943, 968, 991, 1013, 1032, 1051, 1070, 1089, 1113, 1136, 1160, 1188.

### P4-G4.3 — `C4` in-flight semantics — **DECIDE BEFORE IMPLEMENTING**
```
BLOCKER dsh.md §4 marks this PARTIALLY SPECIFIED; 04-FEASIBILITY.md §5.4 = undefined
DO      write into 00-HLD.md §5: does disposal WAIT for, or ABORT, an in-flight operation?
ASSERT  a documented answer + rationale. It must NOT be inherited by accident.
```
**C4-1 cannot start until this line exists.** This is the one place the design is genuinely incomplete.

### P4-G4.4 — `C2` Waterfall
```
ASSERT  a listener that skips next() vetoes an assembly step
ASSERT  mode is observable in telemetry
ASSERT  serial chains are NOT the default (04-FEASIBILITY.md §3: additive on a 1.2-2.1 s path)
```

### P4-G4.5 — `C3` Fiber
```
ASSERT  StateGates and resource lifecycle are Fibers
ASSERT  GLOBAL_GATES still works as a facade (WRAP, do not delete)
ASSERT  lifecycle_divergence_count = 0
```

### P4-G4.6 — `C4` Hot swap
```
ASSERT  swap a backend with zero restart
ASSERT  failed recomposition keeps the previous tree (atomic())
ASSERT  no in-flight operation observes a half-mounted tree
ASSERT  no dropped request under load
```

### P4-G4.7 — Not in Phase 4 (tripwires)
```
ASSERT  no CORDIS proof obligations (Thms 68-80, C_CONFLUENCE, C_PROGRESS)
ASSERT  no G_MODULE / DI container (04-FEASIBILITY.md §5.2 — infeasible as specified)
ASSERT  no in-flight semantics inherited by accident
```

---

## PHASE 5 — HY routing quality
*Entry: none beyond P1 · sections S2, S8 · closes ZERO gaps — quality, not structure*

### P5-G5.1 — `D1` Independent scoring
```
ASSERT  router_ab_suite top-1 agreement NON-REGRESSING
ASSERT  score distribution no longer sums to 1.0
GATE    04-FEASIBILITY.md §4 marks D1/D2/D3 "NEEDS MEASUREMENT" — independent scoring CHANGES
        top-k ordering. A/B first; revert on regression.
```

### P5-G5.2 — `D2` Guaranteed fallback
```
ASSERT  zero empty results over the A/B suite
ASSERT  `_autocut`'s `if not merged: return []` (v5.py def :135) is unreachable from routing
```

### P5-G5.3 — `D3` Telemetry
```
ASSERT  the metric exists AND is read by the D1/D2 gates (not a dashboard ornament)
```

### P5-G5.4 — Analogy guard
```
ASSERT  C_ANNOTATE (TAG-MoE) is treated as ANALOGY-ONLY (hy.md §4.1, §6.0).
        Routing tokens to experts inside a model is NOT routing tasks to resources.
```

---

## PHASE 6 — Per-integration remediation
*Runs alongside P1-P5*

### P6-G6.1 — `I1` Skills (529/1,296; 767 inactive)
```
DO      un-starve the cold tier (A2) -> wait ONE measurement window -> RE-COUNT
ASSERT  the re-count is taken AFTER A2 has had a window; only then is "genuinely dead" a valid label
        each remaining inactive skill gets an explicit decision
NOTE    I1 is gated on A2 BY CONSTRUCTION — a skill inactive because it was never reachable is not dead
```

### P6-G6.2 — `I2` Agents (162/186; 24 inactive)
```
ASSERT  24 -> 0 unexplained (each reactivated or archived WITH a reason)
```

### P6-G6.3 — `I3` MCPs — **RE-SCOPED** (P2)
```
RETRACT the premise: the removal path EXISTS (delete_tool :155, called :218-222 from
        sync_from_mcp_server :178). 301 archived rows are the mechanism SUCCEEDING.
DO NOT  describe 8.5% as "CRITICAL rot". Say what it measures.
REAL GAP  servers ABSENT from the sync loop are never reconciled -> their tools sit active forever
ASSERT  deliberate desync of a REGISTERED server -> tools disappear; re-sync -> they return;
        mcp_sync_log records both
ASSERT  a test exists for an ABSENT server's tools being reconciled (the actual defect)
ASSERT  count of archived-MCP rows with archived_at IS NULL  ->  was 61, target 0
```

### P6-G6.4 — `I4` Harness agents
```
ASSERT  a new toolset mounts and unmounts with NO code edit
ASSERT  a failed mount leaves no half-bound plan
CHECK   toolsets.py tuples at :8 (CORE) :15 (RESEARCH) :29 (BROWSER) :41 (MEMORY)
```

### P6-G6.5 — `I5` Harness sites
```
ASSERT  adding a site = a registry entry + ONE channel module, NO edit to server.py:918-1188
        (verified: exactly 13 registrations live in that range)
ASSERT  backend_protocol.py:142's migration is complete (shared with C1)
DETAIL  05-EXEC-SITES.md
```

---

## Macro critical path with kill conditions

```
P0 ──> P1 (A4 -> A1 -> A2 -> A3) ──> P2 (B1 -> B2 batched -> B3) ──> P3 DECISION
            │                                                            │
            │  KILL: A4 not green => A2/A3 unmeasurable => STOP           │
            │  KILL: A2-0 = "confidence fails" => A2-1 cancelled,        │
            │        branch to B-track deletion instead                  │
            │  KILL: migrations dir undecided => A3 blocked              │
            │                                                            │
            │                              YES ──> P4 (C0 - C1 - C2/C3 - C4)
            │                                         KILL: in-flight decision unwritten => C4 blocked
            └─ NO ──> C0 only, stop

P5 (D1 -> D2 -> D3)  parallel from P1 · KILL: A/B top-1 regresses => revert
P6 (I1..I5)          parallel          · I3 re-scoped per P2
```

**Three decisions gate three phases.** P3 gates all of P4. A2-0 gates A2-1. The migrations-dir choice
gates A3-1. All three are cheap; all three are expensive to assume.
