# ANN vector-store decision — measured, 2026-11-11

Status: research closed. Recommendation: **do not migrate**. Keep `faiss.IndexFlatIP`;
fix staleness and the rebuild path instead.

Resumes the thread left open on 2026-11-10 (LanceDB benchmark done, USearch never
measured, no migration code written). All numbers below were produced on this box
today, in `nexus/venv` (Python 3.12.3, numpy 2.4.4, faiss-cpu 1.13.2, usearch 2.26.6,
lancedb 0.41.0, pyarrow 26.0.1), CPU only, single process.

Reproduce:

```
cd /home/roshan/nexus
./venv/bin/python /home/roshan/.hermes/cache/scratch/ann_bench.py    # scale sweep
./venv/bin/python /home/roshan/.hermes/cache/scratch/ann_bench2.py   # es / nprobe sweeps
json: ~/.hermes/cache/scratch/ann_bench{,_2}_results.json
```

## 1. The finding that changes the question

The index is stale, and nothing detects it.

```
faiss index   db/faiss/resources.index      ntotal = 1,200   built 2026-07-11T22:06:41Z
db            resources status='active'      = 1,861       (status='archived' = 1,393)
              resource_embeddings total      = 3,254       (no orphans, all pre-date the build)
gap          661 active resources unindexed  = 36% of the active set
```

The 1,393 archived rows are correctly excluded (`meta.active_only = true`, and the script
filters `status IN ('active','degraded')`), so they are not part of the gap. My first pass
counted all 3,254 and overstated this as 2,054 / 63% — wrong, corrected here.

The embeddings did not change: zero rows were created after the build, and
`max(created_at)` predates it. `resources.updated_at` reaches 2026-11-10, so what moved is
**status**, not vectors. Verified: 1,200 indexed vs 1,861 active today. Inferred, not proven:
the 661 became active through post-build status transitions, since no embedding rows were
inserted after 11 July. Either way the index does not admit them.

`scripts/rebuild_faiss.py` states the invariant in its own docstring — "The FAISS index
must be a 1:1 mirror of the DB. If embeddings are added/updated without rebuilding FAISS,
the search returns stale/mismatched resource IDs" — and then relies on someone remembering
to run it. The index file's mtime is 2026-07-12; the DB's is 2026-11-10.

Search latency was never the problem. At 1,200 vectors the fastest option costs 0.33 ms.
The expensive part of the pipeline is the SQLite blob read (~3 s for the full rebuild,
per the script's own note) plus embedding inference, both of which are unchanged by
swapping the ANN library.

## 2. Measurements — production scale (1,200 × 384, L2-normalised, IP)

200 queries sampled from the corpus, recall@10 against exact inner product.

| impl | mean ms | p50 | p95 | recall@10 | build s | file MB | notes |
|---|---|---|---|---|---|---|---|
| numpy exact | 0.35–0.84 | 0.21 | 0.69 | 1.000 | – | – | run-to-run variance is real |
| faiss IndexFlatIP | 0.33–0.37 | 0.322 | 0.381 | 1.000 | 0.00 | 1.84 | current design |
| usearch HNSW c16 | 0.882 | 0.893 | 1.129 | 1.000 | 0.57 | 2.11 | load 0.9 ms mmap, save 8.5 ms |
| lancedb flat | 22.7 | 20.3 | 28.7 | 1.000 | 0.21 | 1.85 | 60× slower than faiss |

Incremental write, 50 new rows:

```
faiss     4.3 ms     (ix.add, no rebuild)
usearch  37.4 ms     (u.add, no rebuild)
lancedb 112.6 ms     (merge_insert, no rebuild)
```

At production scale HNSW is **2.7× slower than exact search**. Graph traversal overhead
is fixed cost; with 1,200 points you can afford to touch all of them. This is the whole
answer: there is no speed to buy here.

## 3. Measurements — hypothetical scale (100,000 × 384)

Synthetic: the 1,200 real vectors tiled, jittered at σ=0.01, re-normalised. Near-duplicate
structure, so recall is pessimistic versus a natural corpus — stated as a caveat, not hidden.

| impl | mean ms | p95 | recall@10 | build s | file MB |
|---|---|---|---|---|---|
| numpy exact | 25.6 | 37.1 | 1.000 | – | – |
| faiss IndexFlatIP | 33.6 | 43.3 | 1.000 | 0.44 | 153.0 |
| usearch HNSW es=64 | 0.448 | 0.663 | 0.887 | 62.0–65.5 | 167.9 |
| usearch HNSW es=128 | 0.710 | 1.098 | 0.898 | – | – |
| usearch HNSW es=256 | 1.182 | 1.761 | 0.906 | – | – |
| usearch HNSW es=512 | 2.245 | 3.302 | 0.917 | – | – |
| lancedb flat | 668–976 | 1756 | 1.000 | 1.55 | 153.2 |
| lancedb IVF_PQ p=256 sub=48, np=8 | 18.8 | 22.5 | 0.297 | 389.1 | – |
| lancedb IVF_PQ np=32 | 23.4 | 28.4 | 0.297 | – | – |
| lancedb IVF_PQ np=64 | 29.5 | 36.3 | 0.297 | – | – |
| lancedb IVF_PQ np=128 | 40.1 | 47.4 | 0.297 | – | – |

Readings:

- HNSW's 75× latency win over exact is real at 100k, but it costs 11% of the true top-10 at
  es=64, and recall plateaus at 0.917 even at es=512 (8× the latency). Raising connectivity
  from 16 to 32/64 would recover it — unmeasured, and not needed at 1.2k.
- The HNSW index is **larger than the raw vectors** (167.9 MB vs 153.6 MB) and takes 62 s to
  build. Reload is 36 ms mmap.
- lancedb flat at 100k is unusable for a synchronous route (668 ms at 99.6k rows, 976 ms at
  100k — the spread is real, both far past any interactive budget; p95 1.76 s). Its
  write path is the good part: `merge_insert` of 50 rows took 28 ms at 100k with no rebuild.
- lancedb IVF_PQ is unusable at these settings. Recall sits at 0.297 across nprobe 8→128 while
  latency scales with nprobe, so the probe depth is being applied — recall is capped by PQ
  quantisation (48 sub-vectors × 8 bits for D=384), not by search effort. 389 s to build.
- The 0.41 `create_index(metric=..., num_partitions=...)` API used here is deprecated in
  favour of `create_index(col, config=IvfPq(...))` — a live migration cost for anyone adopting it.

## 4. Decision

Keep `faiss.IndexFlatIP`. Reject usearch, lancedb, and any Rust rewrite of the vector path.

Reasons, in order of weight:

1. At 1.2k vectors exact search is 0.33 ms. Nothing to optimise.
2. Every candidate moves complexity into a second store with its own file format, load path,
   and API churn (lancedb just deprecated the index API we exercised), for zero user-visible gain.
3. The dominant costs are the DB read and the embedder, both untouched by an ANN swap.
4. The actual defect is staleness, and no ANN library fixes that.

## 5. What to do instead

1. **Incremental add, not rebuild.** `IndexFlatIP` supports `index.add()` at 4.3 ms for 50 rows.
   The writer path should append on insert and stop calling the full rebuild.
2. **Staleness guard.** Compare `index.ntotal` to
   `SELECT count(*) FROM resource_embeddings re JOIN resources r ON re.resource_id = r.id
   WHERE re.embedding IS NOT NULL AND r.status IN ('active','degraded')`
   on read or on a timer; refuse to serve (or auto-rebuild) on mismatch. The status filter is
   mandatory — `resource_embeddings` has no `active` column, and an unfiltered count (3,254)
   would mismatch permanently against 1,393 correctly-excluded archived rows.
   `resources.index.meta.json` already carries `n_total`, `built_at`, `git_commit`,
   `checksum_sha256` — the check is a subtraction, not new machinery.
3. **Rebuild now.** 1,861 active resources vs 1,200 indexed: 661 are unsearchable today.
4. **Revisit only above ~50k vectors**, and then measure connectivity 32/64 before choosing
   HNSW; expect to trade recall for latency, and budget 62 s + 168 MB at 100k.

## 6. Correction to a previous statement

An earlier note in this session claimed the daemon's interpreter could not import faiss and
that the retrieval path might be degraded. That was wrong — it came from probing
`importlib.metadata.version("faiss")`, but the distribution is named `faiss-cpu`. Both
`nexus/venv` and `nexus/.venv` import faiss 1.13.2 fine. The real gap is staleness, above,
not a broken import.
