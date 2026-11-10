# Decision 0002 — OKF/sections-first retrieval: ALREADY the v5 architecture

Date: 2026-08-17. Status: ACCEPTED (no new build; one experiment queued).

## Verified facts (read from the tree, not docs)
- The v5 hot path (find_skills → route_for_task → unified_route_v5) retrieves via
  `router_v2/chunk_retrieval.py:155 retrieve_chunks(query, top_k, clusters)` —
  an FTS5 bm25 arm (`chunks_fts` over `ingestion_chunks`, sanitized MATCH,
  fts5.py) fused with a semantic arm (chunk embeddings) via **RRF**, hydrated
  to resources, with **cluster gating** already a parameter.
- FAISS (`server/cluster_faiss.py`) serves the OTHER paths: `cluster_route`
  HTTP/MCP legacy surface. It is NOT in the v5 hot path.
- Two section-ish stores exist: `ingestion_chunks` (retrieved) and OKF
  `resource_sections` (migration 006; surfaced via topic_read + the ops gate
  `cold_without_sections ≤ 20`, not retrieved).

## Decision
1. The pivot's "OKF-like instead of vector DB" is **already implemented** as
   the FTS5+RRF hybrid on the hot path. No parallel `NEXUS_RETRIEVAL` flag or
   duplicate retrieval module will be built (that would re-create the
   9-router-variant mistake).
2. FAISS demotion = legacy `cluster_route` surface only; retire it when the
   v5 path's eval gate (golden baseline, pending curation) covers those calls.
3. QUEUED EXPERIMENT (cheap, high value on 2-core CPU): an FTS5-only arm —
   disable the semantic arm in retrieve_chunks behind a param, A/B on the
   golden set. If parity, the embedder leaves the hot path entirely
   (the ~5s/case eval latency collapses). Do this AFTER the curated baseline
   exists, via evals/run_eval A/B tags.
4. OKF `resource_sections` stays the enrichment/gate layer; folding it into
   the retrieved corpus is a data question (backfill sections as chunks),
   not an architecture question — one backfill script when needed.

## Consequences
- Task 4's planned build (retrieval_okf.py + v5 adapter) is CANCELLED as
  duplicate-surface risk. The eval harness remains the adoption gate.

## RESULT (2026-09-25) — queued experiment run; verdict: **A (keep chunks hybrid)**

Run: `evals/run_eval.py --arm chunks+hybrid --arm sections+hybrid --arm sections+lexical`
→ `evals/results/ab_okf_20260925.json` (golden set: 105 curated cases, scored=84,
G10 zero-result confound applies equally to all arms).

| arm | corpus | vec | recall@k | mrr | p50 ms |
|-----|--------|-----|----------|-----|--------|
| A (baseline) | chunks | ✓ | **0.1905** | **0.1373** | 56.3 |
| B | sections | ✓ | 0.1905 | 0.0873 | 66.4 |
| C (pure lexical target) | sections | ✗ | 0.1071 | 0.0476 | 29.5 |

Adoption rule (EPS=0.02, both metrics):
- **C rejected**: recall 0.1071 < 0.1867 and mrr 0.0476 < 0.1346 — pure-FTS
  sections is far from parity; the embedder stays on the hot path.
- **B rejected**: recall ties base (0.1905) but mrr 0.0873 < 0.1373 (−36%) —
  sections granularity reaches the right resources at k but ranks the exact
  match lower (heading-level rows dilute RRF position vs chunk rows).
- **Verdict A**: `RETRIEVAL_CORPUS` stays `"chunks"`, `vec=True` stays;
  no production default changed. Sections corpus retained as an eval arm and
  enrichment layer (coverage now complete: 0 chunk-linked resources without
  sections).

Notes / follow-ups:
- The queued "embedder leaves the hot path" experiment (3) is answered: NO —
  lexical-only C is not safe at this baseline. Latency of C (29.5ms p50) does
  confirm the cost the embedder carries (~27ms/case), but not at parity quality.
- Biggest shared ceiling is G10 (17 cluster-gated zero-result cases); fixing
  chunk→resource linking would lift all three arms and should be re-run before
  any future corpus flip.
