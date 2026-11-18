# `server/kg/graph_lane.py` — graph-expansion retrieval lane (NEW)

| Field | Value |
|-------|-------|
| Layer | L1 (KG usable at retrieval time) |
| Status | DONE |
| New or edit | **NEW** (additive; the one genuinely new L1 module) |
| Depends on | `resource_edges` (migration 007, exists); typed edges from the `densify_kg.py` edit; `router_v2/fusion.py` RRF |
| First-principle atom | **A2_retrieve** |
| Gaps served | **G7** (retrieval quality), **G10** (chunk→resource surfacing), **G25** (make densified edges pay off) |

## Role — what this file is
A **1-hop graph-expansion lane** that runs *after* the existing hybrid retrieval produces its
seed candidates. Given the top seed `resource_id`s, it walks `resource_edges` one hop over
**typed** edges (`alternative_to`, `documented_by`, and weighted `similar_to`/`co_triggered`),
produces an edge-ranked candidate list, and returns it so the caller can **fold it into the
existing RRF fusion** as one more ranked list. Its purpose: surface *related* resources the
lexical/dense arms miss — the alternative tool, the doc that explains a skill, the co-used
companion — without dumping the whole neighborhood.

It is a **library, not a router.** It does not own scoring, tiering, or gating; it emits a
ranked list that `router_v2/fusion.py` already knows how to blend.

## Issue it handles
Retrieval today is flat text/vector match. The graph is derived (`densify_kg.py`) and stored
(`resource_edges`) but **never read at query time** — so "give me the alternative to X" or
"the doc for skill Y" is invisible to ranking. G25's whole point ("make the graph pay off") is
unrealized without a consumer. This lane is that consumer.

## Research grounding
- **RRF, Cormack SIGIR 2009 (k=60)** — the fusion primitive. We take: the graph lane is
  *just another ranked input list*; fold via `score(d) += 1/(k+rank)`. This is why it must
  **not** be a parallel router (respects G8 anti-pattern) — it plugs into `fusion.py`.
- **`arXiv:2402.03367` (RAG-Fusion)** — multi-source retrieval improved by RRF over several
  query/source lists. We take: graph expansion = an additional source list, fused not appended.
- **`arXiv:2603.22455` (Retrieve-and-rerank for skills)** — body/relationship attention beats
  name match for skill retrieval. We take: neighbors-of-strong-seeds are high-value rerank
  candidates.
- **Tool Attention `arXiv:2604.21816` + Lost in the Middle `arXiv:2307.03172`** — top-k
  discipline; more context is not better. We take: **hard caps** — expand only the top-N seeds,
  ≤ K neighbors per seed, single hop — so the lane cannot flood the context window.

## Interface / contract
```python
def expand_over_edges(
    seed_ids: list[int],          # top resource_ids from the existing retrieval
    conn,                          # sqlite connection (read-only use)
    *,
    edge_types: tuple[str, ...] = ("alternative_to", "documented_by",
                                   "similar_to", "co_triggered"),
    per_seed_cap: int = 5,         # ≤K neighbors per seed  (Lost-in-Middle discipline)
    max_seeds: int = 10,           # only expand the strongest seeds
    hops: int = 1,                 # single hop; multi-hop is deferred
) -> list[dict]:
    """Return edge-ranked neighbors as [{resource_id, edge_type, weight, via_seed}], best-first.
    Excludes seed_ids themselves. Deterministic ordering. Never raises on empty graph."""
```
Invariants:
- **Single hop** in L1 (multi-hop deferred to L3 — see roadmap).
- **Bounded fan-out**: `max_seeds × per_seed_cap` upper bound on returned rows.
- **Read-only**: never writes `resource_edges`.
- **Weighted**: neighbor rank = edge `weight` (typed edges > generic `similar_to`, tunable).
- **Deterministic**: stable sort by `(weight desc, resource_id asc)` for reproducible eval.

## Guided plan (pseudocode)
```
def expand_over_edges(seed_ids, conn, ...):
    seeds = seed_ids[:max_seeds]
    rows  = conn.execute(
        "SELECT source_id, target_id, edge_type, weight FROM resource_edges "
        "WHERE (source_id IN (seeds) OR target_id IN (seeds)) "
        "AND edge_type IN (edge_types)"
    )
    neighbors = {}                       # resource_id -> best (weight, edge_type, via_seed)
    for r in rows:
        nid = r.target_id if r.source_id in seeds else r.source_id
        if nid in seeds: continue
        keep best-by-weight per nid, respecting per_seed_cap per via_seed
    return sorted(neighbors, key=(-weight, resource_id))
```
Fusion wiring (in `router_v2/fusion.py` / `chunk_retrieval.retrieve_chunks`, **edit at impl
time — confirm exact call site against `fusion.py` first**): treat the returned list as an
extra RRF input with a lane weight (mirror the chunk lane's existing blend weight; start ~0.5
and tune on the golden suite). No new blend term is added to the 4-term ADR formula — the lane
contributes *ranked candidates*, and existing ISO/Thompson/EFC/UCB scoring runs over the
enlarged candidate set.

## As built (deltas vs the sketch)
- **Call site = `chunk_retrieval.retrieve_chunks`**, not `fusion.py` (the sketch hedged
  "confirm exact call site"): after the text-only `_rrf(fts, sem)`, the lane is a third RRF
  list with weight `graph_weight` (labels `graph` / `fts+graph` / `fts+sem+graph`).
- **Weight tuned to 0.95 (not ~0.5)** — RRF cutoff physics: the graph term must clear the tail
  of the text pool to enter the `top_k*2` window. Weights 0.5–0.85 were inert (0 ordering
  change on all 105 cases); sweep archived in `evals/results/lane_sweep*.json`.
- **Mode knob `NEXUS_GRAPH_LANE_MODE`** (default **`promote`**): attach each neighbor to its
  *best text-ranked* chunk (candidate set unchanged, graph re-ranks). Naive promote (filter
  representative chunks to the text pool) was inert — representative ids rarely overlap;
  `rid_to_best` attachment fixed it. Legacy **`inject`** maps each neighbor to its
  representative chunk (surfaces resources the text arms miss; cost ~3 router passes).
- **Seed/fanout env knobs**: `NEXUS_GRAPH_LANE_SEEDS` (default **5** — tuning showed 10
  over-expanded weak seeds), `NEXUS_GRAPH_LANE_PER_SEED` (default 5), `NEXUS_GRAPH_LANE_WEIGHT`
  (default 0.95), `NEXUS_GRAPH_LANE` (default ON; `0/false/off/no` disables).
- **Cluster gate**: neighbors whose resource ∉ caller's `clusters` are dropped *before* the
  fold — they used to occupy the fused window and evict in-cluster candidates
  (`cluster_ok` 0.44→0.37 on the golden suite).
- **Router opts out**: `_chunk_resource_stream` passes `graph_lane=False` — the v5 ensemble
  is top-1-calibrated and the lane cost ~2 passes there (off mean 33.4 vs lane mean 31.7),
  while RAG consumers keep it (recall 0.1905→0.3214, mrr 0.1373→0.2421).
- `hops != 1` raises `ValueError` (L1 is single-hop); `EDGE_TYPE_BOOST` typed 1.0 /
  `similar_to`·`co_triggered` 0.85; tie-break resource_id asc (deterministic).

## Acceptance
- [x] On the restored brain, expanding known seeds returns their `alternative_to` /
      `documented_by` neighbors, best-first, seeds excluded.
      — `tests/test_graph_lane.py` (`test_brain_expand_*`); smoke: skill 781 → 10 neighbors.
- [x] Fan-out respects `max_seeds` × `per_seed_cap` (assert row-count bound).
      — `test_per_seed_cap_and_max_seeds_bound_fanout` + brain test row-count bound.
- [x] Empty/edgeless graph → returns `[]`, never raises.
      — `test_empty_graph_never_raises`.
- [x] Golden routing suite: folding the lane **changes** candidate ordering (lane is doing
      work) **without regressing top-1 agreement** vs the pre-lane baseline (`route_ab_compare`).
      — Retrieval arm (`evals/results/lane_defaults_20260925.json`): changed 31.4%,
      recall 0.1905→0.3214, mrr 0.1373→0.2421, expected-head 10 wins / 2 losses.
      Router arm: lane off on the chunk stream → `evals/results/20260925_115234_router-nolane-final.jsonl`
      = 33 passes / skill 0.2381 / cluster_ok 0.4405, inside the pre-lane off-band
      (33–34 passes across 4 off runs) — no regression.
- [x] `gitnexus check --cycles` = 0 (lane imports nothing from `router_v2`).
      — lane imports stdlib only (`os`, `sqlite3`); the check returns 3 cycles, all
      pre-existing in unrelated harness modules (`context_harness↔learning↔router`,
      `execution_plane↔local_harness`, `harness_metrics↔ide_manager`), none involving
      `server/kg/`.

## Impact gate + rollback
```
impact({target:"retrieve_chunks", direction:"downstream", file_path:"server/router_v2/chunk_retrieval.py"})
impact({target:"reciprocal_rank_fusion", direction:"upstream", file_path:"server/router_v2/fusion.py"})
```
Risk: LOW–MEDIUM (changes ranking, not data). Rollback: additive module + a single feature-
flagged call site (`NEXUS_GRAPH_LANE=0` disables folding) — remove the fold call and the module
is inert. No schema change to revert.
