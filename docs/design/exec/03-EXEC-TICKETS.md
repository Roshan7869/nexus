# NEXUS — Execution: the 24 tickets

Elaborates `03-PLAN-MICRO.md`. Grammar and protocol: `exec/README.md`. **Corrections first:
`exec/06-CORRECTIONS.md`** — A1-3 and I3-1 are re-scoped there, and A2-0's gate is pinned.

Convention: `RUN` is the restart command discovered in `exec/README.md` **S0**; it is written as
`$RESTART` below. Do not substitute a guess.

---

# PHASE 1 — FUNDAMENT

## A4-1 — Isolate learner state per test
*ENG · low · 30m · blocks A2-1, A3-1*

### A4-1.1 — Reproduce the failure
```
WHERE   none (read-only)
CHECK   test -f tests/conftest.py
DO      ./venv/bin/python -m pytest -q > /tmp/a41_before.txt 2>&1; echo "exit=$?"
ASSERT  exit=1 and tail -1 matches `2 failed, 374 passed`
UNDO    n/a
```

### A4-1.2 — Name the two failures and the leaked symbol
```
WHERE   none
CHECK   grep -c "def test" tests/test_unified.py
DO      grep -E "^(FAILED|ERROR)" /tmp/a41_before.txt
ASSERT  exactly 2 FAILED lines, BOTH on the learner-disabled path
UNDO    n/a
```
If the two failures are **not** both learner-path, **stop** — the premise is different from the doc and
A4-1 needs re-diagnosis.

### A4-1.3 — Find the symbol that leaks
```
WHERE   server/learning.py
CHECK   grep -n "_ENABLED\|^ENABLED\|enabled" server/learning.py | head
DO      grep -rn "learning\.\(_\)\?ENABLED\|set_learner\|enable_learner\|disable_learner" tests/ server/ | head -20
ASSERT  you can name the exact module-level symbol mutated by a test
UNDO    n/a
```
`01-LLD.md` §7.2 assumes the symbol is `learning._ENABLED`. **Verify it; do not assume.** If the symbol
differs, write the fixture against the real name.

### A4-1.4 — Add the fixture
```
WHERE   tests/conftest.py  (append; 282 lines exist today, no learner fixture present)
CHECK   grep -c "learner" tests/conftest.py     → expect 0
DO      append the autouse fixture from 01-LLD.md §7.2, renamed to the symbol found in A4-1.3
ASSERT  ./venv/bin/python -m pytest -q 2>&1 | tail -1     → expect `374 passed`
UNDO    git checkout -- tests/conftest.py
```

### A4-1.5 — Prove it is order-independent
```
WHERE   none
CHECK   cat tests/pytest.ini 2>/dev/null | head
DO      ./venv/bin/python -m pytest -q -p no:randomly 2>&1 | tail -1
        ./venv/bin/python -m pytest -q --randomly-seed=12345 2>&1 | tail -1   # if plugin present
ASSERT  both invocations: `374 passed` (identical)
UNDO    n/a
```
**Exit gate: green in default order AND green under a different seed.** A fixture that fixes only the
default order has not fixed the leak.

---

## A1-1 — One served surface
*OPS · low · 1h · blocks all of P4*

### A1-1.1 — Freeze the current surface
```
WHERE   none
CHECK   curl -s -o /dev/null -m 3 -w '%{http_code}' http://127.0.0.1:8000/health
DO      for p in $(grep -oE '@app\.(get|post)\("(/v1|/)[^"]*"' server/nexus_http_daemon.py | grep -oE '"/[^"]*"' | tr -d '"' | sort -u); do
          printf "%-34s %s\n" "$p" "$(curl -s -o /dev/null -m 3 -w '%{http_code}' http://127.0.0.1:8000$p)"
        done | tee /tmp/routes_before.txt
ASSERT  file written; `/v1/models` shows 404, `/health` 200
UNDO    n/a
```
Re-verified counts: daemon **32** routes, `unified.py` **39**, `core/server.py` **10**.

### A1-1.2 — Confirm the target routes exist in `unified.py` and are unserved
```
WHERE   server/unified.py
CHECK   grep -n '@app.post("/v1/chat/completions")' server/unified.py
DO      grep -n '@app.get("/v1/models")' server/unified.py
ASSERT  expect `173:@app.post("/v1/chat/completions")` and `306:@app.get("/v1/models")`
UNDO    n/a
```

### A1-1.3 — Mount, do not merge
```
WHERE   server/nexus_http_daemon.py, immediately after `app = FastAPI(` (line 113)
CHECK   grep -c "include_router" server/nexus_http_daemon.py     → expect 0
DO      add an import of unified.py's `app` (or its sole router) and:
            app.mount(...)  is WRONG for routes — use app.include_router(unified_router)
        If unified.py exposes only `app`, extract its routes via
            for r in unified_app.routes: app.router.routes.append(r)
        and record in the commit WHY the loop was needed instead of include_router.
ASSERT  $RESTART && sleep 3 && grep -E "/v1/(models|chat)" /tmp/routes_after.txt
UNDO    git checkout -- server/nexus_http_daemon.py
```
**Do not hand-merge the two FastAPI files.** The 39-vs-32 divergence came from exactly that.

### A1-1.4 — Verify every README-named route
```
WHERE   none
CHECK   grep -oE '/v1/[a-z/]+' README.md | sort -u
DO      re-run the A1-1.1 loop into /tmp/routes_after.txt
ASSERT  every README-named route is non-404; `POST /v1/chat/completions` is 200 or 4xx, **never 404**
UNDO    remove the mount; daemon file otherwise untouched
```

### A1-1.5 — Retire the unserved fork
```
WHERE   core/server.py (10 routes)
CHECK   grep -rn "core.server\|core/server" --include=*.py server/ scripts/ | grep -v "test" | head
DO      git mv core/server.py attic/   (only if A1-1.5's CHECK shows zero live importers)
ASSERT  grep -rn "core.server" --include=*.py . | grep -v attic/ | grep -v test → expect 0
UNDO    git mv attic/server.py core/server.py
```

---

## A1-2 — Fix the dead daemon URL
*OPS · low · 15m*

### A1-2.1 — Confirm the dead port and the live port
```
WHERE   .mcp.json  (line **33**, not 34 — see C1)
CHECK   grep -n "NEXUS_DAEMON_URL" .mcp.json
DO      ss -ltn | grep -E "8000|8080"
ASSERT  expect `"NEXUS_DAEMON_URL": "http://127.0.0.1:8080",` and ONLY `127.0.0.1:8000` listening
UNDO    n/a
```

### A1-2.2 — Repoint
```
WHERE   .mcp.json:33
CHECK   curl -s -o /dev/null -m 3 -w '%{http_code}' http://127.0.0.1:8080/health   → expect 000
DO      change 8080 → 8000
ASSERT  curl -s -o /dev/null -m 3 -w '%{http_code}' http://127.0.0.1:8000/health   → expect 200
UNDO    revert the one line
```

### A1-2.3 — Prove an MCP tool now reaches the daemon
```
WHERE   none
CHECK   grep -rn "NEXUS_DAEMON_URL" server/nexus-mcp-enhanced.py | head -3
DO      invoke one MCP tool that calls the daemon (e.g. nexus_health) through the MCP surface
ASSERT  a real payload, not a connection error
UNDO    n/a
```

---

## A1-3 — **RE-SCOPED: there is no hang** *(was: OPS · low · 30m)*
*See `06-CORRECTIONS.md` P3. 5/5 probes returned 200 in 1.35–2.07 s.*

### A1-3.1 — Confirm the symptom is absent
```
WHERE   none
CHECK   none
DO      for i in 1 2 3 4 5; do curl -s -o /dev/null -m 5 -w '%{http_code} %{time_total}\n' \
          http://127.0.0.1:8000/v1/ops; done
ASSERT  5/5 = 200. If any 000 appears, you have reproduced the original finding — STOP and diagnose.
UNDO    n/a
```

### A1-3.2 — If 200 (expected): measure why it costs a full route
```
WHERE   server/nexus_http_daemon.py:163  (@app.get("/v1/ops"))
CHECK   sed -n '163,180p' server/nexus_http_daemon.py
DO      trace which internal call dominates the 1.4 s (see 04-EXEC-FEASIBILITY M2)
ASSERT  a named stage accounting for >1 s
UNDO    n/a
```
**Disposition:** with 5/5 → 200, A1-3 is **demoted out of P1**. It is either closed or folded into the
latency profiling item (`04-EXEC-FEASIBILITY.md` M2). Do not spend P1 time on a non-reproducing hang.

---

## A2-0 — Which gate blocks the short-circuit? **(do this first)**
*ENG · low · 15m · prereq A4-1 · blocks A2-1*

### A2-0.1 — Pin the gate (it is confirmed verbatim)
```
WHERE   server/router_v2/v5.py:463
CHECK   grep -n "if confidence > short_circuit_confidence and pool_size >= short_circuit_min_pool" server/router_v2/v5.py
DO      sed -n '455,470p' server/router_v2/v5.py
ASSERT  expect `463:    if confidence > short_circuit_confidence and pool_size >= short_circuit_min_pool:`
UNDO    n/a
```
**Trap (C8):** `v5.py:478` contains `pool_size >= 20` — that is the **L2 `constrain`** gate, *not* this
one. Reading the wrong line is the easiest way to mis-answer A2-0.

### A2-0.2 — Instrument both operands on the 8-query set
```
WHERE   _research/_bench_route.py  (8 queries already defined)
CHECK   grep -c "unified_route_v5" _research/_bench_route.py
DO      wrap the call to capture, per query:
          intent["confidence"]           from _l1_intent
          _cluster_pool_size(conn, cluster)
          confidence > 0.85              (bool)
          pool_size >= 50                (bool)
        Append each row to _research/a20_gate.csv
ASSERT  csv has 8 rows + header, with both booleans per row
UNDO    read-only instrumentation; delete the wrapper
```

### A2-0.3 — Report which gate fails
```
WHERE   none
CHECK   wc -l _research/a20_gate.csv     → expect 9
DO      count how many rows have confidence_ok=False vs pool_ok=False
ASSERT  the report names ONE dominant blocker over 8/8 (or explains a split)
UNDO    n/a
```

### A2-0.4 — Branch (this decides A2-1's existence)
```
branches:
  confidence fails 8/8  -> _short_circuit is DEAD CODE.
                           Action: CANCEL A2-1. Delete _short_circuit (B-track, 20m).
                           Rationale: patching a cold quota into a path nothing takes is waste.
  pool_size fails 8/8   -> _cluster_pool_size UNDERCOUNTS.
                           Action: fix the counter (its own ticket), THEN A2-1 is live and matters.
  split / neither       -> STOP and report. The premise is different again; re-derive.
```

---

## A2-1 — Cold quota on the L1 fast path
*ENG · medium · 1h · **prereq: A2-0 returned "pool_size fails"** · DO NOT START OTHERWISE*

### A2-1.1 — Confirm the bypass
```
WHERE   server/router_v2/v5.py
CHECK   grep -n "def _short_circuit" server/router_v2/v5.py
DO      sed -n '237,270p' server/router_v2/v5.py
ASSERT  `_short_circuit` (def :237) contains NO call to `_v5_rerank`
UNDO    n/a
```
`_v5_rerank` performs the two-tier split at `:391-394` (`hot_k` :391, `cold_k` :392). `_short_circuit`
returning at `:464-466` never reaches it. That is the whole defect.

### A2-1.2 — Add the cold backfill
```
WHERE   server/router_v2/v5.py — new `_cold_backfill()` near `_v5_rerank`; wire into `_short_circuit`
CHECK   grep -c "_cold_backfill" server/router_v2/v5.py   → expect 0
DO      apply 01-LLD.md §2.3 (~10 LOC): reuse the `times_triggered > 0` query at :365 INVERTED
        (`times_triggered = 0`); ride `idx_resources_cluster`; no new index needed.
ASSERT  python -c "import ast;ast.parse(open('server/router_v2/v5.py').read())"   → expect exit 0
UNDO    git checkout -- server/router_v2/v5.py
```
**Use the backfill, not the fall-through.** `04-FEASIBILITY.md` §3: the fall-through adds a full
1.2–2.1 s; the backfill adds one indexed read (~ms).

### A2-1.3 — Seeded test: a never-triggered resource must appear
```
WHERE   tests/ (new test)
CHECK   ./venv/bin/python -m pytest tests/ -q -k cold 2>&1 | tail -1
DO      on a DB COPY: set one resource in a strong cluster to times_triggered = 0,
        force the short-circuit, assert it is present in the result
ASSERT  the seeded zero-trigger resource appears; unseeded run is unchanged
UNDO    drop the copy
```

### A2-1.4 — Regression gate (mandatory — this is the routing hot path)
```
WHERE   none
CHECK   ./venv/bin/python -m pytest -q 2>&1 | tail -1     → must match /tmp/baseline_before.txt
DO      run router_ab_suite before/after; read invoke_cold_target_pct ON THE SHORT-CIRCUIT PATH
ASSERT  top-1 agreement non-regressing AND invoke_cold_target_pct >= 35.0 on that path
UNDO    revert; REVERT ON ANY REGRESSION — per standing rule, a regressing mod is reverted, not tuned
```

---

## A2-2 — Waterfall hook point
*ENG · low · 30m*

### A2-2.1 — Place the interception after `_autocut`
```
WHERE   server/router_v2/v5.py, post-`_autocut` (def :135)
CHECK   grep -n "def _autocut" server/router_v2/v5.py     → expect 135
DO      add a dispatcher call AFTER `_autocut` returns, not inside it — so the veto is
        observable and ordered. No listener registered => pass-through.
ASSERT  with zero listeners, output byte-identical to baseline on the A/B suite
UNDO    remove the call site; no listener = no behaviour change
```

### A2-2.2 — Prove a veto works
```
WHERE   tests/
CHECK   none
DO      register a listener that does NOT call next(); assert one result is removed
ASSERT  count drops by exactly 1; unregistered run identical to baseline
UNDO    n/a
```

---

## A3-1 — Collapse to one lifecycle column
*OPS · medium · 2h · prereq A4-1 · blocks C3-1*

### A3-1.0 — **Decide the canonical migrations dir** (new — see C9)
```
WHERE   migrations/ (8 files, max 016_)  vs  db/migrations/ (11 files, max 017_)
CHECK   ls migrations/ | wc -l; ls db/migrations/ | wc -l
DO      grep -rn "migrations" --include=*.py --include=*.sh server/ scripts/ | grep -v venv | head
ASSERT  ONE dir is named as canonical; the other is documented as legacy in the commit
UNDO    n/a
```
`schema_migrations` exists (`migrations/010_fts5_outcome_log.sql`) but holds **1 row**, so it cannot
adjudicate. **A3-1 cannot land until this is decided.**

### A3-1.1 — Snapshot before touching a single-writer DB
```
WHERE   db/nexus.db  (538,640,384 B)
CHECK   ss -ltn | grep 8000     → identify the writer
DO      stop the writer ($RESTART inverse); then
        ./venv/bin/python -c "import sqlite3;c=sqlite3.connect('file:db/nexus.db?mode=ro',uri=True);\
        print(c.execute('PRAGMA integrity_check').fetchone())"
        cp db/nexus.db /tmp/a31_pre.db
ASSERT  integrity_check = 'ok'; copy exists with identical size
UNDO    n/a
```

### A3-1.2 — Baseline the contradiction count (on the COPY)
```
WHERE   /tmp/a31_pre.db
CHECK   none
DO      SELECT COUNT(*) FROM resources WHERE status != lifecycle_state AND lifecycle_state IS NOT NULL;
        SELECT status, lifecycle_state, COUNT(*) FROM resources GROUP BY 1,2;
ASSERT  cross-tab reproduces `active/archived = 132`, `active/NULL = 1730`, `archived/NULL = 659`
UNDO    n/a
```
If the cross-tab differs, the numbers in `00-HLD.md` §4 S1 and `01-LLD.md` §1.1 are stale — record the
new ones before writing the migration.

### A3-1.3 — Write and run the migration (copy-don't-ALTER)
```
WHERE   <canonical dir>/NNNN_unify_lifecycle.sql
CHECK   test -d <canonical dir>
DO      the idempotent UPDATE from 01-LLD.md §1.2 (2,521 rows), then table-copy + CHECK constraint
        (lifecycle_state IN ('active','archived','quarantined','unknown')) + rename
RUN     apply to /tmp/a31_pre.db FIRST; only then to db/nexus.db
ASSERT  SELECT COUNT(*) FROM resources WHERE status != lifecycle_state AND lifecycle_state != 'quarantined'  → 0
        SELECT COUNT(*) FROM resources  → 3,255 (unchanged)
UNDO    restore db/nexus.db from /tmp/a31_pre.db; down-migration restores both columns
```
`resources` is **22.2 MB** of the 538.6 MB DB — this migration is a table copy, minutes, not hours.

### A3-1.4 — Prove rejection and idempotency
```
CHECK   none
DO      attempt INSERT with lifecycle_state='bogus'    → must be REJECTED by CHECK
        re-run the migration a second time          → must be a no-op
ASSERT  invalid write raises; second run changes 0 rows; version row in schema_migrations
UNDO    revert
```

### A3-1.5 — Restart and re-verify
```
RUN     $RESTART
ASSERT  ./venv/bin/python -m pytest -q → matches baseline; daemon serves /health 200
UNDO    full restore
```

---

# PHASE 2 — DELETION

## B1-1 — Remove the dead chain
*ENG · low · 20m*

### B1-1.1 — Enumerate references (before deletion)
```
WHERE   none
CHECK   wc -l server/autonomous_pipeline.py server/router_v2/fts5.py server/test_v3.py
ASSERT  expect **396**, **215**, **380** (C3 corrected 397→396 and 216→215)
DO      grep -rn "autonomous_pipeline\|router_v2.fts5\|from.*fts5\|import fts5" \
          --include=*.py --include=*.md . | grep -v venv
ASSERT  only prose refs remain: `scripts/auto_close_routes.py:25`, `router_v2/scoring.py:26`,
        plus the two `NEXUS_SECOND_BRAIN_PLAN*` docs and `docs/**`
UNDO    n/a
```
**Note:** `server/autonomous_pipeline.py:38` imports `router_v2.fts5` — the two die together.

### B1-1.2 — Delete and prove
```
WHERE   the three paths
CHECK   ./venv/bin/python -m pytest -q 2>&1 | tail -1   → baseline
DO      git rm server/autonomous_pipeline.py server/router_v2/fts5.py server/test_v3.py
ASSERT  pytest tail matches baseline; grep for the three symbols in *.py returns 0
UNDO    git checkout HEAD -- <the three paths>
```

---

## B2-1 — Quarantine the 95-file list
*OPS · medium · 1h*

### B2-1.1 — Confirm the script and its failure mode
```
WHERE   _research/verify_delete_recovered.py · _research/delete_list_recovered.json
CHECK   ./venv/bin/python -c "import json;d=json.load(open('_research/delete_list_recovered.json'));print(len(d))"
ASSERT  a list of 95 entries
DO      grep -n "signal\|SIGINT\|SIGTERM\|atexit\|finally\|restore" _research/verify_delete_recovered.py
ASSERT  restore-on-exit handlers exist for SIGINT/TERM/HUP
UNDO    n/a
```

### B2-1.2 — Run in batches of 20 (the prior attempt died at the 300 s cap)
```
WHERE   none
CHECK   ls $(the 95 paths) | wc -l     → expect 95 present
DO      for each batch of 20: move to quarantine/, run pytest, record result
        ABORT the batch immediately if pytest leaves baseline
ASSERT  after all 95: pytest matches baseline; 0 of 95 remain in place
UNDO    the script's restore path; verify count returns to 491 tracked / 95 present
```
**Never run all 95 in one call.** `04-FEASIBILITY.md` §5.3: the cap is a measured hard failure.

---

## B3-1 — Remove the literal `$HOME` directory
*OPS · low · 10m*

### B3-1.1 — Archive, then remove
```
WHERE   ./$HOME/nexus/db/nexus.db  (184 KB) — confirmed present via find
CHECK   find . -maxdepth 3 -name '$HOME'
DO      tar czf /tmp/stray_home.tgz './$HOME' && rm -rf './$HOME'
ASSERT  find . -maxdepth 3 -name '$HOME' → empty
UNDO    tar xzf /tmp/stray_home.tgz
```

### B3-1.2 — Fix the unexpanded `$HOME` at source
```
WHERE   the script that creates it
CHECK   grep -rn "'\$HOME\|\"\$HOME\|\\\$HOME" scripts/ server/ | grep -v venv | head
DO      quote/expand correctly in the creating script
ASSERT  run that script fully; `find . -name '$HOME'` stays empty
UNDO    revert
```
A directory literally named `$HOME` means a path was built with `'$HOME'` under quotes that suppressed
expansion. **Find the quoting bug, do not just delete the artifact** — otherwise it returns.

---

# PHASE 4 — CORDIS (YES branch)

## C0-1 — The disposer
*ENG · medium · 1.5h*

### C0-1.1 — Write `server/disposer.py` verbatim from the LLD
```
WHERE   server/disposer.py (new)
CHECK   test ! -f server/disposer.py
DO      create with the ~30 LOC from 01-LLD.md §6.2 (Disposed / Handle / atomic)
ASSERT  python -c "import ast;ast.parse(open('server/disposer.py').read())" → exit 0
        `_demo()` runs: double close() is a no-op; post-close effect() raises Disposed;
        order is reverse; atomic() on a failing build leaves the prior tree
UNDO    rm server/disposer.py
```
**Do not hand-write LIFO / idempotent / cascade / atomic.** `ExitStack` is all four; `pop_all()` is
atomic recomposition in one stdlib call. Hand-writing them *is* the defect.

### C0-1.2 — Convert the live study flow
```
WHERE   harness_study_project → _harvest_all → harvest_site → _one → fetch_page  (S6)
CHECK   grep -rn "def _harvest_all\|def harvest_site\|def fetch_page" server/ | head
DO      convert the acquisition to `with Handle(...)`; register DB conn, HTTP session, temp file,
        partial rows — each with its cleanup
ASSERT  kill a study mid-harvest → no orphan rows, no temp files left
UNDO    removing the `with` restores prior behaviour (purely additive)
```

---

## C1-1 — Finish the declared registry migration
*ENG · medium · 2h · prereq C0-1*

### C1-1.1 — Count the old registrations (verified: exactly 13)
```
WHERE   server/context_harness/server.py:918-1188
CHECK   grep -c "fuser.register(BackendDef" server/context_harness/server.py
ASSERT  expect **13** — at 918, 943, 968, 991, 1013, 1032, 1051, 1070, 1089, 1113, 1136, 1160, 1188
UNDO    n/a
```

### C1-1.2 — Add disposal to `BackendRegistry` (additive only)
```
WHERE   server/context_harness/backend_protocol.py:35 (`class BackendRegistry`)
CHECK   sed -n '35,60p' server/context_harness/backend_protocol.py
DO      apply 01-LLD.md §3.3: ExitStack + `_closed` guard; register via
        `getattr(backend,"aclose",None) or getattr(backend,"close",None)`
ASSERT  the 4 `BackendProtocol` members are UNCHANGED — `close()` is optional
        (this is why 13 backends keep working untouched)
UNDO    git checkout -- server/context_harness/backend_protocol.py
```

### C1-1.3 — Port all 13, one commit
```
WHERE   server/context_harness/server.py:918-1188
CHECK   keep the old block behind a module flag until verified
DO      register each of the 13 onto `BackendRegistry`; verified names: scrapling_fetch,
        sherlock_probe, scrapecraft_pattern, scrapling_spider, arxiv, semantic_scholar, github,
        paperswithcode, google_scholar, openalex, crossref, pubmed, target_site
ASSERT  fusion.py `RRFFuser` reaches ZERO production callers (corrected from "Fuser" — P1)
        all 13 still return results; one with close() is disposed, one without still works
UNDO    flip the flag back
```
Also at stake: `fusion.py:158-160` registers `nexus`/`internet`/`memory` at weight 1.0 — those three are
the in-process fusion backends and are part of "the old way".

---

## C2-1 — Waterfall dispatch
*ENG · medium · 2h · prereq C1-1*

### C2-1.1 — Implement the five modes
```
WHERE   S2/S3 assembly points
CHECK   grep -n "waterfall" dsh.md | head
DO      implement emit / parallel / serial / bail / waterfall; a listener that skips next() vetoes
ASSERT  a veto removes a step; parallel vs serial ordering is observable in telemetry;
        no listener registered => output identical to today
UNDO    unregistered dispatcher is a pass-through
```

### C2-1.2 — Latency guard
```
ASSERT  serial chains measurable and NOT default. 04-FEASIBILITY.md §3: at 1.2–2.1 s per route,
        a serial listener chain is additive — keep interception parallel unless ordering is required.
```

---

## C3-1 — Fiber
*ENG · high · 2h · prereq C1-1, A3-1*

### C3-1.1 — Wrap, do not delete
```
WHERE   server/precondition.py:65 (`class StateGates`), `GLOBAL_GATES = StateGates()` at :114
CHECK   grep -n "GLOBAL_GATES" server/nexus-mcp-enhanced.py
DO      wrap `StateGates` as a `Fiber(parent, state, effects)`; keep `GLOBAL_GATES` as a facade
ASSERT  `check_preconditions` and `update_state` still work through the facade
UNDO    facade retained until the Fiber is proven
```
**`GLOBAL_GATES` is live — it backs two MCP tools.** The research already retracted the "delete it"
claim; record that here so nobody re-kills it.

### C3-1.2 — DB column becomes a projection of θ
```
WHERE   db/nexus.db `lifecycle_state` (post A3-1)
CHECK   A3-1 exit gate = 0 contradictions
DO      θ ∈ Inactive/Reloading/Active/Unloading becomes authoritative in memory
ASSERT  `lifecycle_divergence_count` = 0 in steady state (new metric, 01-LLD.md §8.1)
UNDO    StateGates retained as facade
```

---

## C4-1 — Hot swap
*ENG · high · 2h · prereq C2-1, C3-1*

### C4-1.0 — **DECIDE the in-flight semantics first** (blocks the ticket)
```
WHERE   dsh.md §4  — marked PARTIALLY SPECIFIED
DO      write the decision explicitly into 00-HLD.md §5: does disposal WAIT for, or ABORT,
        an in-flight operation?
ASSERT  a documented answer with a rationale. 04-FEASIBILITY.md §5.4: undefined, and it must not
        be inherited by accident.
```
**Do not implement C4-1 before this line is written.**

### C4-1.1 — Swap under load, atomic on failure
```
WHERE   `atomic()` from server/disposer.py
CHECK   C0-1 `_demo()` passes
DO      swap a backend with zero restart; deliberately fail a recomposition
ASSERT  no dropped request; failing recomposition leaves the OLD tree serving; no in-flight
        operation observes a half-mounted tree
UNDO    atomic() guarantees it by construction — this is the point of pop_all()
```

---

# PHASE 5 — HY (parallel)

## D1-1 — Independent scoring
*ENG · medium · 1.5h*

```
WHERE   server/router_v2/v5.py `_rank_normalize` + RRF
CHECK   grep -n "_rank_normalize\|RRF_K" server/router_v2/v5.py | head
DO      hy.md §2.1: sigmoid + learned bias per resource, instead of competing for a softmax 1.0
ASSERT  scores no longer normalise to 1.0; router_ab_suite top-1 agreement NON-REGRESSING
UNDO    fusion path retained; the new score is additive and ignorable by consumers
```
**Gate before merging:** independent scoring changes top-k ordering (`04-FEASIBILITY.md` §4 — "NEEDS
MEASUREMENT"). A/B first; revert on regression.

## D2-1 — Guaranteed fallback
*ENG · low · 30m*

```
WHERE   `_autocut`'s `if not merged: return []` (v5.py, def :135)
CHECK   grep -n "if not merged" server/router_v2/v5.py
DO      hy.md §2.3: never return empty — always include the cluster's default skill (shared expert)
ASSERT  ZERO empty results across the A/B suite; the `return []` branch is unreachable from routing
UNDO    revert the insertion
```

## D3-1 — Per-cluster telemetry
*ENG · low · 1h · prereq D1-1, D2-1*

```
WHERE   existing cluster_affinity_history, coach_burndown + new counters
CHECK   sqlite3 -readonly db/nexus.db ".schema cluster_affinity_history"
DO      per-cluster trigger counters + a load-balance metric
ASSERT  the metric is what D1/D2 gates READ — build it to serve those gates, not as a dashboard
UNDO    drop the metric
```

---

# PHASE 6 — PER-INTEGRATION

## I2-1 — Triage 24 dead agents
*ENG · low · 1h*

```
CHECK   SELECT COUNT(*) FROM resources WHERE resource_type='agent' AND status!='active'   → 24
DO      each of the 24: reactivate OR archive with a reason written to `archive_reason`
ASSERT  zero agents in a non-active state without an explanation
UNDO    restore status from a saved list
```

## I3-1 — **RE-SCOPED: the removal path already exists** *(was: OPS · medium · 1.5h)*
*See `06-CORRECTIONS.md` P2. This is the correction with the biggest consequence.*

### I3-1.1 — Retract the premise
```
WHERE   none
CHECK   grep -n "def delete_tool" server/mcp_sync.py            → expect 155
DO      sed -n '155,172p' server/mcp_sync.py
ASSERT  `delete_tool` soft-deletes: UPDATE resources SET status='archived' ...
        AND `sync_from_mcp_server` (:178) calls it at :218-222 for names not in server_names
        => THE REMOVAL PATH EXISTS AND HAS BEEN RUNNING
UNDO    n/a
```
**Therefore the 8.5% MCP "health" is not rot.** 301 archived rows are the removal path **succeeding**.
That headline must not be repeated as a health metric without saying what it measures.

### I3-1.2 — Find the real defect: reconcile absent servers
```
CHECK   grep -n "sync_from_mcp_server" server/nexus-mcp-enhanced.py    → 964
DO      determine which servers are currently passed in vs the servers historically present in
        `resources` where resource_type='mcp'
ASSERT  you can name tools whose server is NO LONGER in the sync loop — those are the true stale set
UNDO    n/a
```
The blade only falls on tools of servers still being synced. A server gone from `.mcp.json` is never
called again, so its tools are **never evaluated for removal** and sit `active` forever.

### I3-1.3 — Fix `archived_at`
```
CHECK   SELECT COUNT(*) FROM resources WHERE resource_type='mcp' AND status='archived' AND archived_at IS NULL
ASSERT  expect **61**   (301 archived total; 240 carry archived_at from a separate sweep)
DO      set archived_at in delete_tool alongside status
UNDO    revert the UPDATE
```

### I3-1.4 — Gate
```
ASSERT  deliberately desync a still-registered server → its tools disappear
        re-sync → they return; `mcp_sync_log` records both
        AND a test for an ABSENT server's tools being reconciled (the real gap)
```

## I4-1 — Toolset registry
*ENG · medium · 2h*

```
WHERE   server/harness_engine/toolsets.py — CORE_TOOLS :8 · RESEARCH_TOOLS :15 · BROWSER_TOOLS :29 · MEMORY_TOOLS :41
CHECK   grep -n "^CORE_TOOLS\|^RESEARCH_TOOLS\|^BROWSER_TOOLS\|^MEMORY_TOOLS" server/harness_engine/toolsets.py
DO      `ToolsetRegistry.mount(name, tools, requires=()) -> Handle` (01-LLD.md §4.2);
        `requires=` cascades; CORE_TOOLS stays a module constant (always mounted)
ASSERT  a new toolset mounts/unmounts with no code edit; unmount cascades to dependents;
        a failed mount leaves no half-bound plan
UNDO    tuple constants remain the default until the registry is proven
```

## I5-1 / I5-2 — Sites
*ENG · medium · 2h each · I5-1 prereq C1-1*

See `05-EXEC-SITES.md` for the full molecular walk-through. Summary:

```
I5-1  adding a site = registry entry + one channel module, NO edit to server.py:918-1188
      (verified: exactly 13 registrations live in that range)
I5-2  load config/harness_sites.yaml at startup; malformed entry fails LOUDLY at startup,
      not silently at query time
```

---

# Cross-cutting checks (run for EVERY ticket)

```
1. ./venv/bin/python -m pytest -q            → matches /tmp/baseline_before.txt
2. git diff --stat                            → touches only the files the ticket names
3. grep -n "type: ignore" <changed files>     → no NEW occurrences
4. grep -n "except Exception: pass"           → no NEW occurrences
5. commit message contains the UNDO line verbatim
6. routing-path change → router_ab_suite top-1 recorded before AND after
7. OPS tickets → tested on a COPY first
8. estimate over 2h → the ticket is under-decomposed; split it before starting
```
