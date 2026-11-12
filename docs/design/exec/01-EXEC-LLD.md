# NEXUS — Execution: LLD

Elaborates `01-LLD.md`. Every code block becomes: **file · insert point · the block · the test that
proves it · the undo**. The LLD gives the code; this gives the landing procedure.

Grammar: `exec/README.md` §1. Corrections applying here: **P1** (`RRFFuser`, not `Fuser`), **C4**
(`_short_circuit` call is `:464`), **C9** (two migration dirs).

---

## §1.2 — The lifecycle migration SQL

```
FILE    <canonical migrations dir>/NNNN_unify_lifecycle.sql
INSERT  new file
BLOCK   the idempotent UPDATE from 01-LLD.md §1.2
```

### Steps

```
L1.2.1  Decide the dir — this BLOCKS everything else        (C9)
        ls migrations/ | wc -l          → expect 8   (max 016_)
        ls db/migrations/ | wc -l       → expect 11  (max 017_)
        grep -rn "migrations" --include=*.py server/ scripts/ | grep -v venv
        ASSERT  one dir named canonical in the commit message.
                `schema_migrations` exists (migrations/010_fts5_outcome_log.sql) but has 1 ROW,
                so it cannot adjudicate. Decide by which dir the runner actually reads.

L1.2.2  Land the UPDATE on a COPY first
        cp db/nexus.db /tmp/lld_pre.db
        ASSERT  applying to /tmp/lld_pre.db changes exactly 2,521 rows

L1.2.3  INVARIANT — the whole point of the ticket
        SELECT COUNT(*) FROM resources
         WHERE status != lifecycle_state AND lifecycle_state != 'quarantined';
        ASSERT  expect 0

L1.2.4  Idempotency
        ASSERT  second run changes 0 rows

L1.2.5  CHECK constraint actually rejects
        INSERT ... lifecycle_state='bogus'   → must RAISE
        ASSERT  constraint lives in the table DDL, not only in application code
```

**Blocked by L1.2.1.** Do not write the migration until the canonical dir is named.

**Note on `copy-don't-ALTER`:** `resources` is 22.2 MB of a 538.6 MB DB — the copy is cheap. The down
migration is then a file restore, which is why this is the safe choice.

---

## §1.3 — `FiberState`

```
FILE    server/ (new or existing state module)
BLOCK   FiberState(str, Enum): INACTIVE / RELOADING / ACTIVE / UNLOADING
```

```
L1.3.1  ASSERT  importable; all four members present
L1.3.2  ASSERT  DB `lifecycle_state` is the PERSISTED PROJECTION of θ — the Fiber is authoritative
                in memory and the column is a cache.
L1.3.3  ASSERT  any divergence is treated as a bug; `lifecycle_divergence_count` (§8.1) surfaces it
L1.3.4  coupling  requires A3-1 complete (one column) before θ can project onto it
```
**Depends on A3-1.** Projecting θ onto *two disagreeing columns* is the defect, not the fix.

---

## §2.3 — `_cold_backfill()` on the fast path

```
FILE    server/router_v2/v5.py
INSERT  new function near `_v5_rerank` (def :301); wired into `_short_circuit` (def :237)
BLOCK   01-LLD.md §2.3 (~10 LOC)
```

```
L2.3.1  PRECONDITION — A2-0 must have returned "pool_size fails"
        sed -n '455,470p' server/router_v2/v5.py
        ASSERT  `463:    if confidence > short_circuit_confidence and pool_size >= short_circuit_min_pool:`
        WARNING v5.py:478 has a DIFFERENT gate (`pool_size >= 20` = L2 constrain). Do not confuse. (C8)

L2.3.2  Confirm the bypass
        ASSERT  `_short_circuit` (def :237) contains NO call to `_v5_rerank`
        ASSERT  `_v5_rerank` does the split at :391-394 (hot_k :391, cold_k :392)
        ASSERT  the `_short_circuit` return is at :464-466

L2.3.3  Reuse the EXISTING query, inverted
        v5.py:365 does `... AND times_triggered > 0`. `_cold_backfill` needs `times_triggered = 0`.
        ASSERT  no new index added — ride `idx_resources_cluster`
                (there is NO index on times_triggered; `idx_resources_status_triggered` is misleadingly
                named and is actually on (status, times_correct) — 04-FEASIBILITY.md §1.4)

L2.3.4  SYNTAX
        ./venv/bin/python -c "import ast;ast.parse(open('server/router_v2/v5.py').read())"
        ASSERT  exit 0

L2.3.5  REGRESSION — this is the routing hot path
        assertions BEFORE and AFTER:
          router_ab_suite top-1 agreement   non-regressing
          invoke_cold_target_pct >= 35.0    measured ON THE SHORT-CIRCUIT PATH, not overall
        ASSERT  REVERT ON ANY REGRESSION (standing rule: a regressing mod is reverted, not tuned)
```

**Choose the backfill, not the fall-through.** `04-FEASIBILITY.md` §3: fall-through adds 1.2–2.1 s;
backfill adds one indexed read (~ms).

---

## §2.4 — Waterfall post-filter (`G_INTERCEPT`)

```
FILE    server/router_v2/v5.py
INSERT  AFTER `_autocut` returns (def :135) — NOT inside it
```

```
L2.4.1  ASSERT  interception sits after `_autocut` so the veto is observable and ordered
L2.4.2  ASSERT  a registered listener that skips next() removes a result; unregistered run is
                byte-identical to baseline
L2.4.3  ASSERT  zero listeners registered => no behaviour change (rollback is "do nothing")
L2.4.4  ASSERT  C2-1's full five modes are NOT needed here — this is the hook point, C2-1 is the
                dispatcher. Do not implement the dispatcher inside v5.py.
```

---

## §3.3 — Disposal for `BackendRegistry`

```
FILE    server/context_harness/backend_protocol.py
INSERT  `class BackendRegistry` at :35 — modify in place, ADDITIVE ONLY
BLOCK   01-LLD.md §3.3 (ExitStack + _closed guard + getattr-dispatched close)
```

```
L3.3.1  CHECK   grep -n "class BackendRegistry" server/context_harness/backend_protocol.py  → 35
L3.3.2  CHECK   sed -n '35,60p' ...  → today `register()` has no cleanup handle, no reload, no unload
L3.3.3  CRITICAL the 4 BackendProtocol members (name, weight, cache_ttl, timeout_seconds + search,
                is_available) MUST remain unchanged. `close()` stays OPTIONAL via getattr.
        ASSERT  the 13 existing backends work untouched — this is why the graft is cheap
L3.3.4  ASSERT  register() after close() raises Disposed
L3.3.5  ASSERT  close() is idempotent; duplicate name still raises KeyError
L3.3.6  ALSO    fusion.py:158-160 registers `nexus`/`internet`/`memory` at weight 1.0 — part of the
                old path; include in the C1-1 migration scope
```

---

## §4.2 — `ToolsetRegistry`

```
FILE    server/harness_engine/toolsets.py
INSERT  alongside the existing tuples; CORE_TOOLS stays a module-level constant
BLOCK   01-LLD.md §4.2
```

```
L4.2.1  CHECK   grep -n "^CORE_TOOLS\|^RESEARCH_TOOLS\|^BROWSER_TOOLS\|^MEMORY_TOOLS" \
                  server/harness_engine/toolsets.py
        ASSERT  expect 8 / 15 / 29 / 41
L4.2.2  ASSERT  mount(name, tools, requires=()) -> Handle
L4.2.3  ASSERT  unmounting a dependency CASCADES (child handle registered on the parent stack)
L4.2.4  ASSERT  a failed mount leaves NO half-bound plan
L4.2.5  ASSERT  the tuple constants remain the default until the registry is proven
L4.2.6  §4.3: `attach.py` returns a Handle instead of mutating global state
```

---

## §6.2 — `server/disposer.py` (the whole kernel, ~30 LOC)

```
FILE    server/disposer.py   (NEW)
BLOCK   01-LLD.md §6.2 verbatim: Disposed · Handle · atomic
```

```
L6.2.1  CHECK   test ! -f server/disposer.py
L6.2.2  WRITE   the block AS GIVEN
L6.2.3  ASSERT  python -c "import ast;ast.parse(open('server/disposer.py').read())" → exit 0
L6.2.4  ASSERT  _demo() passes ALL of:
                  double close() is a no-op                 (C_IDEMPOTENT)
                  post-close effect() raises Disposed        (C_USE_AFTER)
                  cleanup order is REVERSE of registration   (C_REVERSE — free from ExitStack LIFO)
                  adopting a child disposes it               (C_CASCADE)
                  atomic() on a failing build keeps the previous tree (C_ATOMIC via pop_all)
L6.2.5  SIZE    wc -l server/disposer.py
        ASSERT  smaller than its own test file. If over ~60 LOC, the kernel has drifted (HLD §8 G8.2).
L6.2.6  DO NOT hand-write LIFO / idempotent / cascade / atomic. ExitStack is all four; pop_all() is
        atomic recomposition in one stdlib call. Writing them IS the defect.
```

### §6.1 — the adoption target (first adopter)

```
FLOW    harness_study_project → _harvest_all → harvest_site → _one → fetch_page
CHECK   grep -rn "def _harvest_all\|def harvest_site\|def fetch_page" server/
ASSERT  4 effects registered per acquisition: DB connection, HTTP session, temp file, partial rows
ASSERT  kill a study mid-harvest → NO orphan rows, NO temp files
UNDO    removing the `with` block restores prior behaviour (purely additive)
```

---

## §7.2 — The test-order fixture

```
FILE    tests/conftest.py   (EXISTS — 282 lines)
INSERT  append
BLOCK   01-LLD.md §7.2
```

```
L7.2.1  CHECK   grep -c "learner" tests/conftest.py     → expect 0   (premise VERIFIED, C-verified)
L7.2.2  VERIFY THE SYMBOL. The LLD assumes `learning._ENABLED`.
        grep -n "_ENABLED\|^ENABLED\|enabled" server/learning.py | head
        ASSERT  the fixture is written against the REAL symbol, not the assumed one
L7.2.3  ASSERT  full suite green in DEFAULT order AND under a different seed
L7.2.4  ASSERT  the two previously-failing tests stay fixed in both orders
```

---

## §8.1 — `lifecycle_divergence_count` (new metric)

```
FILE    server/ops_metrics.py
INSERT  new metric
```

```
L8.1.1  DEFINITION  rows where DB `lifecycle_state` disagrees with the in-memory Fiber θ
L8.1.2  ASSERT  = 0 in steady state after A3-1 + C3-1
L8.1.3  WHY  this is the observability that makes the dual-column collapse VERIFIABLE rather than
             ASSERTED. Without it, "one lifecycle column" is a claim, not a measurement.
L8.1.4  SURFACE  exposed via the ops surface so 02-EXEC-MACRO P1's exit gate can read it
```

---

## LLD execution summary

| LLD block | file | depends on | proven by |
|---|---|---|---|
| §1.2 migration | `<canonical>/NNNN_unify_lifecycle.sql` | **dir decision (C9)** | contradiction count = 0; 2,521 rows; idempotent |
| §1.3 `FiberState` | state module | A3-1 | divergence = 0 |
| §2.3 `_cold_backfill` | `router_v2/v5.py` | **A2-0 = "pool_size fails"** | seeded zero-trigger appears; A/B non-regressing |
| §2.4 waterfall hook | `router_v2/v5.py` post-`:135` | — | veto removes 1; unregistered = identical |
| §3.3 registry disposal | `backend_protocol.py:35` | C0-1 | contract unchanged; 13 backends still work |
| §4.2 `ToolsetRegistry` | `harness_engine/toolsets.py` | — | mount/unmount, cascade, no half-bound plan |
| §6.2 `disposer.py` | `server/disposer.py` | — | `_demo()` — 5 invariants |
| §7.2 conftest fixture | `tests/conftest.py` | — | green in 2 orders |
| §8.1 divergence metric | `ops_metrics.py` | A3-1, C3-1 | = 0 steady state |

**Two blocks cannot start without a decision:** §1.2 (which migrations dir) and §2.3 (A2-0's answer).
Both are cheap to resolve and expensive to assume.
