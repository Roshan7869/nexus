# Second Brain KG — per-file execution plan (L0 + L1)

Status: DONE (L0 + L1 complete · L2–L4 deferred)
Owner: —
Last updated: 2026-09-25
Companion authority: [`docs/research/ADR-002-research-authority-second-brain-v2.md`](../../research/ADR-002-research-authority-second-brain-v2.md) · [`docs/research/gap-paper-map.yaml`](../../research/gap-paper-map.yaml)
Sibling track: [`docs/plans/okf-native-cordis/00-overview.md`](../okf-native-cordis/00-overview.md)

## Why this doc exists

The `okf-native-cordis` plan is organized by **phase** (an execution timeline). This
companion is organized by **file** — one doc per *new file we actually create* — because
each new file is a distinct research-grounded decision that must record: what role it plays,
which first-principles atom / gap it serves, which paper(s) authorize its design, and the
guided implementation. It does not re-open settled decisions; it points at ADR-002 for those.

## Layered re-evaluation (what we build now vs later)

| Layer | Theme | Status | New files |
|-------|-------|--------|-----------|
| **L0** | Finish the Cordis local harness (spatial coeffects + live HMR apply) | **DONE** | `providers.py`, `apply_db.py` |
| **L1** | Make the knowledge graph *usable at retrieval time* (typed edges + graph-expansion lane) | **DONE** (7h typed edges + graph lane shipped) | `server/kg/__init__.py`, `server/kg/graph_lane.py` (+ **edit** `scripts/densify_kg.py`) |
| L2 | Learning-loop quality (feedback coverage, fail-loud, Reflexion wiring) | deferred | see `deferred-roadmap.md` |
| L3 | LLM-derived / inductive edges (GraphSAGE edge prediction) | deferred | see `deferred-roadmap.md` |
| L4 | Architecture hygiene (router deprecation, import-cycle break, HTTP facade) | deferred | see `deferred-roadmap.md` |

### Scope decision (locked for this track)
- **Build L0 + L1 only.** L2–L4 are enumerated in `deferred-roadmap.md` with their paper
  grounding so nothing is lost, but they are not implemented here.
- **L1 was reduced after reading the codebase.** `scripts/densify_kg.py` already derives
  `similar_to` / `member_of` / `co_triggered` edges (vectorized cosine, **not** O(n²),
  per-node density cap, reversible via `created_at` run timestamp + `resource_edges_backup`).
  `resource_edges` (migration `007_graph_topology.sql`) already carries `edge_type TEXT`
  and even names `dependency`/`alternative`/`documentation_for` in its comment. So we do
  **not** add `edge_derivation.py`, `scripts/derive_edges.py`, or a new migration — that
  would duplicate existing infra (violates reuse-first). Instead we **extend** `densify_kg.py`
  with one typed-edge pass and add the one genuinely new module: `graph_lane.py`.

## Index of per-file docs

| Doc | Target file | New/Edit | Layer | Atom(s) | Gap(s) |
|-----|-------------|----------|-------|---------|--------|
| [per-file/server.local_harness.providers.py.md](per-file/server.local_harness.providers.py.md) | `server/local_harness/providers.py` | new | L0 | A6_serve | G4, G6 |
| [per-file/server.local_harness.apply_db.py.md](per-file/server.local_harness.apply_db.py.md) | `server/local_harness/apply_db.py` | new | L0 | A1_registry, A6_serve | G1, G2 |
| [per-file/server.kg.__init__.py.md](per-file/server.kg.__init__.py.md) | `server/kg/__init__.py` | new | L1 | A1_registry | — |
| [per-file/server.kg.graph_lane.py.md](per-file/server.kg.graph_lane.py.md) | `server/kg/graph_lane.py` | new | L1 | A2_retrieve | G7, G10, G25 |
| [change-densify_kg-typed-edges.md](change-densify_kg-typed-edges.md) | `scripts/densify_kg.py` | **edit** | L1 | A1_registry, A3_rank | G25 |
| [deferred-roadmap.md](deferred-roadmap.md) | (L2–L4 future files) | — | L2–L4 | — | G8,G12,G14,G16,G17,G22,G25(L3) |

## First-principles atoms (from ADR-002 §3)

1. **A1_registry** — one authoritative resource set (embeddings, clusters, **edges**).
2. **A2_retrieve** — task → candidate list (hybrid sparse + dense).
3. **A3_rank** — multi-signal score (semantic + quality + explore).
4. **A4_gate** — only active, precondition-ok, in-set tools execute.
5. **A5_feedback** — outcomes update beliefs with quality weights.
6. **A6_serve** — one warm process fleet; tests never share prod locks.

L0 serves **A6** (provider reactivity + live surface mutation). L1 serves **A1/A2/A3**
(typed edges enrich the registry; the graph lane turns them into retrieval signal).

## Research map (only papers already fixed in ADR-002 / gap-paper-map)

| Concern | Authority paper(s) | Used by |
|---------|--------------------|---------|
| Serve / reliability, health-probed restart | Agentic architecture `arXiv:2602.10479`; Self-adaptive cloud reliability `arXiv:2503.06705`; NetMCP health-aware `arXiv:2510.13467` | `providers.py` |
| Data integrity, migration/diff gates, op-versioning | Data-centric ML `arXiv:2506.16051`; SmartDiff `arXiv:2509.00293`; Operation-based versioning `arXiv:2512.09762` | `apply_db.py` |
| List fusion | RRF, Cormack SIGIR 2009 (k=60) | `graph_lane.py` |
| Multi-source retrieval fusion | RAG-Fusion `arXiv:2402.03367`; Retrieve-and-rerank skills `arXiv:2603.22455` | `graph_lane.py` |
| Top-k discipline / context tax | Tool Attention `arXiv:2604.21816`; Lost in the Middle `arXiv:2307.03172` | `graph_lane.py` (hop/expansion caps) |
| KG densify (semantic edges, no O(n²)) | GraphSAGE `arXiv:1706.02216` (inductive → **deferred L3**); KG-TS `SSRN:4807690` | `densify_kg.py` edit |

> Do not introduce arXiv IDs that are not in ADR-002 / `gap-paper-map.yaml`. If a new claim
> needs a citation not on that list, add it to the gap-paper-map first (that file is the
> research authority of record).

## Locked decisions inherited from ADR-002 (do not re-open here)
- `edge_type` column is the typed-edge contract (not a `relation` column).
- KG densify uses **vectorized cosine / FAISS-NN**, never O(n²) pairwise (anti-pattern list).
- Edge derivation is **reversible by run timestamp** (`created_at >= run_ts`) + backup table.
- Retrieval stays a **single** pipeline: the graph lane folds into the existing RRF fusion —
  it is **not** a fourth parallel router (respects the "three parallel routers" anti-pattern, G8).
- Live surface mutation is **archive-not-delete**, single transaction, LIFO-reversible (Cordis).

## Global acceptance for this track
- [x] L0: killing DB connectivity mid-session cleanly deactivates the Cordis session (spatial
      half live — Phase 4 tests), and `nexus_local_harness_reconcile` returns `applied:true`
      with a reversible, audited DB mutation (Phase 5: live verify + rollback test).
- [x] L1: `densify_kg.py` populates ≥1 `documented_by` and ≥1 `alternative_to` edge on the
      restored brain (✅ 158 / 462 written + converged); `graph_lane.py` measurably changes
      candidate ordering on the golden routing suite **without** regressing top-1 agreement
      vs the pre-lane baseline.
      Evidence: retrieval arm (`evals/results/lane_defaults_20260925.json`) — ordering changed
      31.4%, recall 0.1905→0.3214, mrr 0.1373→0.2421, expected-head 10 wins / 2 losses;
      router arm (`evals/results/20260925_115234_router-nolane-final.jsonl`) — 33 passes /
      skill 0.2381 / cluster_ok 0.4405, inside the pre-lane off-band (33–34) because the
      router's chunk stream opts out of the lane (`graph_lane=False`) while RAG consumers
      keep it (see `per-file/server.kg.graph_lane.py.md` § As built for the trade-off).
- [x] No new parallel router; no duplicate edge-derivation script; no O(n²) pass.
