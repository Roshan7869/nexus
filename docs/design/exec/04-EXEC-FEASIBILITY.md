# NEXUS — Execution: Feasibility

Elaborates `04-FEASIBILITY.md`. Every measured number becomes a **re-measurement command**, and every
verdict gets the evidence that would overturn it. A feasibility doc without re-runnable measurements
decays into assertion.

Grammar: `exec/README.md` §1.

---

## §1.1 — Route latency (the binding constraint)

```
M-LAT  ./venv/bin/python _research/_bench_route.py 2>&1 | grep -v "Loading weights"
       USE_UNIFIED_ROUTER=2, real queries, ./venv/bin/python
ASSERT 8 queries: min 1,367 ms · median 1,807 ms · mean 3,146 ms · max 12,568 ms
ASSERT 3 queries warm: min 1,213 · median 1,633 · mean 1,651 · max 2,107
```
**Steady-state ≈ 1.2–2.1 s.** Re-measure after any change to A2/C2/D1 — they all run *inside* this path.

**Overturns the doc if:** median drops well below 1.2 s (then the constraint is relaxed and the
fall-through alternative becomes viable) or exceeds ~3 s (then even the backfill is questionable).

---

## §1.2 — The model does NOT reload per call

```
M-EMB  ./venv/bin/python _research/_bench_embedder.py 2>&1 | grep -v "Loading weights"
ASSERT ids: <same> <same> <same>   same: True
       _instances keys: ['/home/roshan/nexus/db/nexus.db']   (exactly ONE key)
       [IsoEmbedder] Loading ...   printed ONCE across 3 route calls
```
**Do NOT "fix" the embedder singleton — it is correct.** The apparent per-query reload was
stdout/stderr interleaving. Recorded because it is the class of unverified claim that PART 0 had to
retract three of.

**Residual (real, lower priority):** `iso_embedder.py:17` warns that constructing `IsoEmbedder()` per
call reloads the model — true. Offline scripts do exactly that:
```
M-EMB2 grep -n "IsoEmbedder(" scripts/phase_d_verify.py scripts/compute_centroids.py \
         scripts/compute_weighted_centroids.py core/learner.py
ASSERT offline-only. If any of these enters the hot path, this becomes urgent.
```

---

## §1.3 — Storage

```
M-DB   stat -c%s db/nexus.db
ASSERT 538640384            # 538.6 MB   <- HLD's "526 MB" is WRONG (C2)

M-DB2  ./venv/bin/python - <<'PY'
import sqlite3
c=sqlite3.connect('file:db/nexus.db?mode=ro',uri=True)
print('resources', c.execute("SELECT SUM(pgsize) FROM dbstat WHERE name='resources'").fetchone()[0])
PY
ASSERT chunk_embeddings 136.5 MB · chunk_embed_cache 118.6 MB · ingestion_chunks 83.1 MB ·
       chunks_fts_content 69.4 MB · chunks_fts_data 23.0 MB · **resources 22.2 MB**
```
**This is what makes A3 cheap:** the table being migrated is 22 MB of 538.6 MB. ≈430 MB (80%) is
ingestion + chunk embeddings + a 118 MB embedding *cache* — a separate, larger remediation nobody sized.

---

## §1.4 — Indexes

```
M-IDX  sqlite3 -readonly db/nexus.db "SELECT name,sql FROM sqlite_master WHERE type='index' AND tbl_name='resources'"
ASSERT present: resource_type · status · tier · cluster · (status,name) · (status,cluster) ·
                (status,times_correct) · (status,token_count)
ASSERT **NO index on times_triggered**
ASSERT `idx_resources_status_triggered` is actually on `(status, times_correct)` — misleading name
```
**Consequence for A2-1:** `_cold_backfill` must ride `idx_resources_cluster`. No new index needed.

---

## §1.5 — Vector search

```
M-VEC  grep -n "1174\|384\|0.2 ms\|15 ms" server/numpy_router.py | head
ASSERT 1,174 routable × 384 dims ≈ 1.8 MB · load ~15 ms · search ~0.2 ms
```
**Costs are negligible — the 1.2–2.1 s is NOT vector search.** This is what forces the §3 conclusions.

---

## §2 — FINDING: the fast path is dead code

```
M-A20  ./venv/bin/python _research/_bench_route.py 2>&1 | grep path
ASSERT 8/8 `full_pipeline`; 0/8 `short_circuit`

M-A20b grep -n "SHORT_CIRCUIT_CONFIDENCE\|SHORT_CIRCUIT_MIN_POOL" server/router_v2/v5.py
ASSERT 123:SHORT_CIRCUIT_CONFIDENCE = 0.85 · 124:SHORT_CIRCUIT_MIN_POOL = 50

M-A20c sed -n '455,470p' server/router_v2/v5.py
ASSERT 463:    if confidence > short_circuit_confidence and pool_size >= short_circuit_min_pool:
       464:        merged = _short_circuit(intent, top_k, db_path)
```
**Trap:** `v5.py:478` has `pool_size >= 20` — the **L2 `constrain`** gate, a different condition.
Reading it by mistake is the easiest way to mis-answer A2-0.

### The two branches and their evidence (A2-0 decides — 15 min, `03-EXEC-TICKETS.md`)

```
BRANCH 1  confidence never clears 0.85
          => `_short_circuit` is DEAD CODE
          => CANCEL A2-1; DELETE `_short_circuit` (B-track, 20m)
          EVIDENCE REQUIRED: a20_gate.csv with confidence_ok=False on 8/8

BRANCH 2  pool_size never clears 50
          => `_cluster_pool_size` UNDERCOUNTS
          => fix the counter, THEN A2-1 is live and matters
          EVIDENCE REQUIRED: a20_gate.csv with pool_ok=False on 8/8

SPLIT     STOP and report. The premise is different again.
```

**What §2 overturns in the parent docs:** G_COLDSTART is **latent, not active**. Observed `cold_k` =
2–3 on every query, exactly as designed for `cold_fraction = 0.3`. Cold resources ARE being selected.
The research's "743 permanently unselectable" is **not happening**.

---

## §3 — Latency, not structure, is binding

```
M-L3   the three rules of §3, each checkable before merging a change:
1  A2 must not add a round-trip        -> backfill = 1 indexed read (~ms); fall-through = +1.2-2.1 s
   ASSERT  A2-1's implementation contains NO call to full_pipeline / unified_route
2  D1 must be in-memory                -> runs after fusion on <=10 candidates
   ASSERT  no new DB read or HTTP call in the scoring layer
3  C2 must be listener-count aware      -> serial chains are additive on a 2 s path
   ASSERT  interception defaults to PARALLEL
```

---

## §4 — Feasibility by item (verdict + overturning evidence)

| item | verdict | re-check | overturn if |
|---|---|---|---|
| **A1** one surface | FEASIBLE, hours | route counts 32/39/10 | a route needs a rewrite, not a mount |
| **A1-3** `/v1/ops` | **RE-SCOPED — not a bug** (P3) | 5× `curl -m 5 -w '%{http_code} %{time_total}'` | any 000 appears on the 5 probes |
| **A2-1** cold quota | FEASIBLE **after A2-0** | `a20_gate.csv` | **A2-0 says confidence** ⇒ cancelled |
| **A3** lifecycle | FEASIBLE, cheap | `dbstat` → resources = 22.2 MB | the table is not ~22 MB |
| **B1** 632 LOC | FEASIBLE, mechanical | `grep --include=*.py` → prose only | a live importer appears |
| **B2** 95 files | FEASIBLE **with batching** | batch of 20 completes < 300 s | a batch times out ⇒ smaller batches |
| **C0** disposer | FEASIBLE | `ExitStack.callback` O(1); `close()` O(10) µs | `_demo()` fails |
| **C1** registry | FEASIBLE | 13 registrations; `close()` optional via getattr | the Protocol must change |
| **C2** waterfall | FEASIBLE | in-process dispatch | serial chain is required by ordering |
| **C3** Fiber | FEASIBLE, medium risk | `StateGates` is a dict with no parent | GLOBAL_GATES breaks as a facade |
| **C4** hot swap | FEASIBLE | `pop_all()` is the atomicity primitive | in-flight semantics stay undefined |
| **D1/D2/D3** | **NEEDS MEASUREMENT** | `router_ab_suite` top-1 | agreement regresses ⇒ revert |
| **I3** MCP | **RE-SCOPED** (P2) | `delete_tool` :155 exists | — premise already overturned |
| **I4** toolsets | FEASIBLE | static tuples → registry | cascade needs cross-process state |

---

## §5 — Verified INFEASIBLE: keep these shut

### §5.1 — CORDIS proof obligations
```
M-INF1 grep -rn "C_CONFLUENCE\|C_PROGRESS\|Thm 68\|Theorem 68" docs/design/ | grep -v EXEC | grep -v CORRECTIONS
ASSERT  no work item claims to prove them
WHY     proved over a formal model with transitions and a DISTRIBUTED state space.
        NEXUS is ONE process with ONE SQLite writer. The model does not exist.
        DSH ships 0.1.0-rc.5 WITHOUT them (_research/dsh.md:259).
```

### §5.2 — `G_MODULE` via a DI container
```
M-INF2 graph: Module = 0 · Interface = 0 · ACCESSES = 2
       268 / 300 traced flows CROSS a community boundary
ASSERT  no DI container is introduced to solve this
WHY     a container registers PROVIDERS; it does not create BOUNDARIES. Mounting one over
        already-violated boundaries makes the violations INJECTABLE and therefore harder to see.
        `ACCESSES = 2` means the graph cannot even observe the problem.
FIX     import-linter contracts or a package restructure — a separate effort.
```

### §5.3 — Unbounded graph queries (**demonstrated, not theoretical**)
```
M-INF3 grep -rn "600\|300" _research/README.md | head
ASSERT  two of six subagent deliverables were KILLED by the 600 s wall clock on unbounded Kuzu
        scans over all 8,944 nodes, and large stdout truncation broke JSON parsing
RULE    EVERY graph query must be BOUNDED: LIMIT, a starting node, or a depth cap
RULE    `.gitnexus/lbug` is NOT queryable from this shell — no kuzu/ladybug client in system
        python or venv. A client must be installed FIRST.
```
This is the rule that makes B2 batch at 20 and every `ASSERT` fit inside 300 s.

### §5.4 — In-flight hot-swap semantics
```
M-INF4 grep -n "PARTIALLY SPECIFIED" _research/dsh.md
ASSERT  dsh.md §4 marks it PARTIALLY SPECIFIED
STATUS  UNDEFINED, not infeasible
RULE    it CANNOT be inherited by accident. Decide and document before C4-1.
```

---

## §6 — Complexity (re-verify against the implementation)

| operation | complexity | check |
|---|---|---|
| effect registration | O(1) | `ExitStack.callback` is an append — no growth in the disposer |
| close / unload | O(N), N≈10 | `wc -l server/disposer.py` stays ~30-60 |
| atomic recompose | O(N) | `pop_all()` is a pointer transfer |
| route (current) | I/O-dominated | **1.2–2.1 s measured** |
| cold backfill (new) | +1 indexed read | rides `idx_resources_cluster`; ~ms |
| vector search | ~0.2 ms | 1,174 × 384 matmul, 1.8 MB |
| A3 migration | O(2,521 rows) | 22 MB table copy |
| B2 quarantine | O(95 files) | **batch to survive the 300 s cap** |

**Nothing in Tracks A, B, C is computationally infeasible.** The two binding constraints are:
(a) 1.2–2.1 s per route ⇒ prefer the backfill over the fall-through;
(b) the 300 s tool cap ⇒ batch B2, bound all graph queries, keep every `ASSERT` under 300 s.

---

## §7 — Open measurements (each becomes a command; feed results back)

```
M1  A2-0 — which gate blocks the short-circuit?          [03-EXEC-TICKETS A2-0, 15 min]
M2  Where does the 1.2-2.1 s actually go?                [profile the stages]
    ASSERT  a per-stage breakdown naming where >1 s sits. Vector search is 0.2 ms, so ~1.2 s is
            elsewhere: L1 intent, topic leg, RRF, rerank, or DB reads on 538.6 MB.
    NOTE    /v1/ops also costs 1.35-2.07 s (P3) — if an ops endpoint costs a full route, it likely
            CALLS the router. That is a cheap lead for M2.
M3  Is chunk_embed_cache (118.6 MB) still earning its place?
    ASSERT  a hit rate. If low, it is pure weight — the single largest removable object in the DB.
M4  Why 12.6 s on the tail?
    ASSERT  contention / cold page cache / a pathological query — name which. Repeat the 8-query
            bench and check whether the tail reproduces.
```

**M2, M3, M4 are unblocked right now** and need no decision. M2 is the highest value: it is the only one
that could change the *architecture* rather than the scheduling.
