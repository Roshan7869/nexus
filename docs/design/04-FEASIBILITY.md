# NEXUS — Computational Feasibility Analysis

Status: DRAFT v1 · 2026-09-13
Companions: `00-HLD.md`, `01-LLD.md`, `02-PLAN-MACRO.md`, `03-PLAN-MICRO.md`

Every number below was **measured on this machine today** unless explicitly marked estimated. Two
findings in this document **re-scope the plan** — see §2 and §3.

---

## 1. Measured baseline

### 1.1 Route latency (real queries, venv interpreter, `USE_UNIFIED_ROUTER=2`)

| run | n | min | median | mean | max |
|---|---|---|---|---|---|
| 8 realistic queries | 8 | 1,367 ms | 1,807 ms | 3,146 ms | **12,568 ms** |
| 3 queries, model pre-warmed | 3 | 1,213 ms | 1,633 ms | 1,651 ms | 2,107 ms |

**Steady-state per-route cost ≈ 1.2–2.1 s**, with a 12.6 s tail observed under contention.

This is the single most important feasibility constraint in the document. Everything NEXUS does per
task pays this, and A2/C2/D1 all run *inside* this path.

### 1.2 The model does **not** reload per call (verified — a claim I nearly got wrong)

`sentence-transformers/all-MiniLM-L6-v2` is loaded via `IsoEmbedder.get()`
(`server/iso_embedder.py:29`), a class-level singleton keyed by `os.path.realpath(db_path)` with
double-checked locking. Verified:

```
ids: 132886316369584 132886316369584 132886316369584   same: True
_instances keys: ['/home/roshan/nexus/db/nexus.db']     (exactly one key)
[IsoEmbedder] Loading ...     <- printed ONCE across 3 route calls
```

An earlier 8-query run appeared to show one `[IsoEmbedder] Loading` line per query. That was
**stdout/stderr interleaving**, not repeated loads. **Do not "fix" the embedder singleton — it is
correct.** Recorded here because it is exactly the class of unverified claim that
`_research/IMPLEMENTATION_PLAN.md` PART 0 had to retract three of.

Module warning remains relevant anyway: `iso_embedder.py:17` warns that constructing
`IsoEmbedder()` per call reloads the model — true, and `scripts/*` do exactly that
(`phase_d_verify.py:134`, `compute_centroids.py:120`, `compute_weighted_centroids.py:140`,
`core/learner.py:353`). Offline scripts, not the hot path.

### 1.3 Storage

DB `db/nexus.db` = **538.6 MB** (131,504 pages × 4,096 B).

| object | size | share |
|---|---|---|
| `chunk_embeddings` | 136.5 MB | 25.3% |
| `chunk_embed_cache` | 118.6 MB | 22.0% |
| `ingestion_chunks` | 83.1 MB | 15.4% |
| `chunks_fts_content` | 69.4 MB | 12.9% |
| `chunks_fts_data` | 23.0 MB | 4.3% |
| **`resources`** | **22.2 MB** | **4.1%** |
| `resource_sections` | 20.0 MB | 3.7% |
| `resource_embeddings` | 6.7 MB | 1.2% |

**≈430 MB (80%) is ingestion chunks, chunk embeddings, and a 118 MB embedding *cache*.** The
`resources` table — the one the plan actually migrates — is 22 MB. This makes A3 cheap and identifies
a separate, larger remediation.

### 1.4 Indexes on `resources`

Present: `resource_type`, `status`, `tier`, `cluster`, `(status,name)`, `(status,cluster)`,
`(status,times_correct)`, `(status,token_count)`.

**There is no index on `times_triggered`.** The index named `idx_resources_status_triggered` is
actually on `(status, times_correct)` — a misleading name, and the cold-tier queries ride
`(status, cluster)` or the rowid autoindex instead. `_cold_backfill` (`01-LLD.md` §2.3) can use
`idx_resources_cluster`; no new index is required, but the naming is a trap.

### 1.5 Vector search

`numpy_router.py` docstring: 1,174 routable × 384 dims ≈ **1.8 MB**, load ~15 ms, search ~0.2 ms.
At NEXUS scale brute-force matmul beats FAISS. Costs are negligible; **the 1.2–2.1 s is not vector
search.**

---

## 2. FINDING — the "fast path" is dead code (re-scopes A2)

**0 of 8 realistic queries took the short-circuit.** All 8 returned `path=full_pipeline`.

Gate constants (`v5.py:123-124`): `SHORT_CIRCUIT_CONFIDENCE = 0.85`, `SHORT_CIRCUIT_MIN_POOL = 50`.
Both must hold (`v5.py:463`) for `_short_circuit` to run.

### What this means, stated honestly

1. **G_COLDSTART is latent, not active.** The earlier plan said the short-circuit starves the cold
   tier. True in principle — but the path never executes, so it does not currently starve anything.
   **Observed `cold_k` = 2–3 on every query**, exactly as designed for `cold_fraction = 0.3`. Cold
   resources are being selected today. The research's "743 permanently unselectable" is **not
   happening**.
2. **The real defect is that a dead fast path is also the unsafe one.** `_short_circuit` is
   unreachable *and* it bypasses the cold tier. Fixing its cold quota fixes a path nothing takes.
3. **A2 is re-scoped, not cancelled.** Two branches, and which one is correct is a 5-minute
   measurement, not a judgement call:
   - **If `confidence` never clears 0.85** → the L1 intent classifier is too weak. Then
     `_short_circuit` is dead weight and the honest move is **delete it** (it is only ever reachable
     if confidence clears the gate) — a B-track deletion, not a Track-A fix.
   - **If `pool_size` never clears 50** → `_cluster_pool_size` undercounts and the fast path is
     suppressed by a measurement bug. Then **fix the counter** and A2 becomes live and matters.

**New ticket A2-0 (precedes A2-1, 15 min):** instrument both gate values on the 8-query set and
report which blocks. **Do not implement the cold-quota patch until A2-0 answers.** This is a
correction to `_research/IMPLEMENTATION_PLAN.md`, authored one turn earlier — the measurement
contradicted the design.

---

## 3. FINDING — latency, not structure, is the binding constraint

1.2–2.1 s per route with a warm model. The composition work in Track C adds microseconds (below), so
latency is not a reason to avoid CORDIS. But it *is* the reason the ordering matters:

- **A2 must not add a round-trip.** The cold backfill adds one indexed SQL read (~ms). The
  `fall-through to full_pipeline` alternative adds a full 1.2–2.1 s. **Choose the backfill.** At 2 s
  per route, a fall-through on the fast path is a latency regression, not a correctness win.
- **D1's independent scoring must be in-memory.** It runs after fusion on ≤10 candidates — negligible.
- **C2's waterfall must be listener-count aware.** A serial listener chain on a 2 s path is additive;
  keep interception parallel unless ordering is required.

---

## 4. Feasibility by item

| item | verdict | basis |
|---|---|---|
| **A1** one surface | **FEASIBLE** — hours | route mounting is additive; 32+39 routes |
| **A1-3** `/v1/ops` hang | **FEASIBLE** — unknown cost | must first identify the blocking call |
| **A2-1** cold quota | **FEASIBLE after A2-0** | ~10 LOC + 1 indexed read (~ms) |
| **A3** lifecycle collapse | **FEASIBLE** — cheap | `resources` is **22 MB**, not 538 MB; 2,521 rows; copy-then-rename in minutes |
| **B1** 632 LOC delete | **FEASIBLE** — mechanical | two prose references confirmed |
| **B2** 95-file quarantine | **FEASIBLE with batching** | prior attempt died at the 300 s cap → batch ~20/run |
| **C0** disposer | **FEASIBLE** | `ExitStack.callback` is O(1) append; `close()` is O(N) over ~10 effects ≈ microseconds |
| **C1** registry migration | **FEASIBLE** | 13 registrations; `close()` optional via `getattr` so the contract is unchanged |
| **C2** waterfall | **FEASIBLE** | in-process dispatch; see §3 for serial-vs-parallel |
| **C3** Fiber | **FEASIBLE, medium risk** | `StateGates` is a dict with no parent; wrapping is additive |
| **C4** hot swap | **FEASIBLE** | `pop_all()` is the atomicity primitive; no restart required |
| **D1/D2/D3** | **NEEDS MEASUREMENT** | independent scoring changes top-k ordering; gate on A/B top-1 agreement |
| **I3** MCP removal path | **FEASIBLE** | `mcp_sync.py:222` upserts; add a removal function |
| **I4** toolset registry | **FEASIBLE** | static tuples → registry; `requires=` for cascade |

---

## 5. Verified INFEASIBLE (do not attempt)

These are not "hard". They are structurally impossible in this system as it exists.

### 5.1 CORDIS proof obligations
`C_EFFECT`, `C_RECOVERY`, `C_FIBER` (Thms 68–80), `C_PROGRESS`, `C_CONFLUENCE`, `C_ORDER`,
`C_RESOLUTION` are proved over a formal model with transitions and a distributed state space. NEXUS
is **one process with one SQLite writer**. The model NEXUS would have to be embedded in does not
exist, and building it is a research program, not a ticket. `_research/dsh.md` documents DSH shipping
`0.1.0-rc.5` **without** them. **Infeasible — correctly omitted.**

### 5.2 `G_MODULE` via a DI container
`Module = 0` and `Interface = 0` in the graph. 268 of 300 traced flows cross a community boundary.
A DI container registers *providers*; it does not create *boundaries*. Mounting a container over
already-violated boundaries makes the violations injectable, and therefore harder to see.
`ACCESSES = 2` means the graph cannot even observe the problem. **Infeasible as specified — requires
`import-linter` contracts or a package restructure, which is a separate effort.**

### 5.3 Unbounded graph queries — demonstrated, not theoretical
`_research/README.md` records two of six subagent deliverables killed by the **600 s wall clock** on
unbounded Kuzu scans over all 8,944 nodes, with large stdout truncation breaking JSON parsing. This
is a measured hard failure. **Any graph query must be bounded**: LIMIT, a starting node, or a depth
cap. Also note `.gitnexus/lbug` is not queryable from this shell — no `kuzu`/`ladybug` client is
installed in system python or `venv`.

### 5.4 In-flight hot-swap semantics
`dsh.md` §4 marks this **PARTIALLY SPECIFIED**: DSH does not define whether disposal waits for or
aborts an operation using the plugin. **Undefined, not infeasible** — but it cannot be inherited by
accident. Decide and document before C4.

---

## 6. Complexity summary

| operation | complexity | note |
|---|---|---|
| effect registration | O(1) | `ExitStack.callback` append |
| close / unload | O(N), N ≈ 10 | LIFO pop; microseconds |
| atomic recompose | O(N) | `pop_all()` pointer transfer |
| route (current) | dominated by I/O | **1.2–2.1 s measured** |
| cold backfill (new) | +1 indexed read | ~ms, rides `idx_resources_cluster` |
| vector search | ~0.2 ms | 1,174 × 384 matmul, 1.8 MB |
| A3 migration | O(2,521 rows) | 22 MB table copy |
| B2 quarantine | O(95 files) | batch to survive the 300 s cap |

**Nothing in Tracks A, B, or C is computationally infeasible.** The binding constraints are (a)
1.2–2.1 s per route, which argues for the backfill over the fall-through, and (b) the 300 s tool cap,
which argues for batching B2 and bounding all graph queries.

---

## 7. Open measurements (feed back into this document)

1. **A2-0** — which gate blocks the short-circuit, `confidence` or `pool_size`?
2. Where does the 1.2–2.1 s actually go? Profile the full pipeline's stages; the vector search is
   0.2 ms, so ~1.2 s is elsewhere (L1, topic leg, RRF, rerank, DB reads on 538 MB).
3. Is `chunk_embed_cache` (118.6 MB) still earning its place? If hit rate is low it is pure weight.
4. Why 12.6 s on the tail? Contention, cold page cache, or a pathological query?
