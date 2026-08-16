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
