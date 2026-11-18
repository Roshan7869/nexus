# OKF-native retrieval + full Cordis local harness — execution plan

Status: NOT STARTED
Owner: —
Last updated: 2026-09-25

## Goal (one sentence)
Move the router's retrieved corpus from `ingestion_chunks` to OKF `resource_sections`,
conditionally drop the semantic/embedder arm (pure FTS5 lexical) **only if a curated
golden A/B shows parity**, and complete the Cordis local harness (OKF context wiring +
M9 spatial coeffects + HMR loader live-apply).

> **Companion (per-file docs):** [`docs/plans/second-brain-kg/`](../second-brain-kg/00-overview.md)
> holds one research-grounded doc per *new file* (Phase 4 `providers.py`, Phase 5 `apply_db.py`)
> plus an L1 KG-retrieval track (`server/kg/graph_lane.py` + a typed-edge pass in `densify_kg.py`).
> That track does not change these phases; it documents the files they create and adds the graph lane.

## Locked decisions
| # | Decision | Source |
|---|----------|--------|
| D1 | Retrieved corpus becomes OKF `resource_sections` / `sections_fts` | user, 2026-09-25 |
| D2 | Drop embedder → pure FTS5 lexical, **adopt only on eval parity** | user, 2026-09-25 |
| D3 | Cordis full build: OKF retrieval + M9 coeffects + HMR loader | user, 2026-09-25 |
| D4 | No new `NEXUS_RETRIEVAL` flag / parallel retrieval module | decision 0002:18-21 |

## The tension we are gating on (read this before Phase 2)
`evals/golden/routing_cases.yaml` is ~2KB and half the cases still live in
`evals/golden/drafts/`. Decision 0002:27 explicitly marks the golden baseline as
**pending curation**. A lexical-vs-hybrid parity verdict on a thin set is theater.
Therefore **Phase 0 (curate the golden set) is a hard prerequisite for Phase 2's
adoption rule.** If Phase 0 is skipped, Phase 2 must ship `sections+hybrid` (no
embedder removal) by default.

## Sequencing (DAG)
```
Phase 0  golden eval baseline
   │
Phase 1  OKF sections corpus arm (hybrid intact)  ── depends → 0
   │
Phase 2  A/B + conditional embedder drop          ── depends → 0,1
   │
Phase 3  harness OKF context block                ── depends → 1
   │
Phase 4  M9 spatial coeffects                      ── depends → 3 (+2 if FAISS removed)
   │
Phase 5  HMR loader live-apply                     ── depends → (independent of 1-2)
```
Shippable unit A = Phases 0-3 (retrieval end-to-end).
Shippable unit B = Phases 4-5 (Cordis spatial + HMR).

## Global execution gates (apply to EVERY phase)
- Per AGENTS.md/GitNexus: run `impact({target, direction:"upstream"})` on every
  symbol before editing it; report blast radius; STOP on HIGH/CRITICAL.
- Run `detect_changes()` before any commit; confirm only expected symbols moved.
- No find-and-replace renames — use `rename` (call-graph aware).
- Reuse existing params (`vec`, new `corpus`) — never add a parallel retrieval flag (D4).

## Key files (map)
| Area | File | Role today |
|------|------|-----------|
| Retrieval | `server/router_v2/chunk_retrieval.py:155` | `retrieve_chunks()` FTS+sem→RRF over `ingestion_chunks` |
| Router L2 | `server/router_v2/__init__.py:277` | calls `_retrieve_chunks(..., vec=True)` |
| OKF sections | `db/migrations/006_okf_resource_sections.sql` | `resource_sections` + `sections_fts` (FTS5 external-content) |
| OKF synth | `server/okf_sections.py` | `write_sections`, `synthesize_from_catalog`, `backfill_cold_sections` |
| Context slot | `server/context_assembly.py:5,72` | reserved OKF-sections adapter slot (empty) |
| Harness session | `server/local_harness/session.py` | Cordis temporal done; `:70,:74` M9 stubs |
| Harness ctx | `server/local_harness/context.py:63` | `classify_transition` (built, unused on live path) |
| Harness loader | `server/local_harness/loader.py:49` | `reconcile()` done; `apply` not wired to DB |
| Eval | `evals/run_eval.py`, `evals/scorer.py`, `evals/golden/` | baseline harness (thin set) |

## Per-phase docs
- [phase-0-golden-eval.md](phase-0-golden-eval.md)
- [phase-1-okf-corpus.md](phase-1-okf-corpus.md)
- [phase-2-ab-embedder.md](phase-2-ab-embedder.md)
- [phase-3-harness-context.md](phase-3-harness-context.md)
- [phase-4-m9-coeffects.md](phase-4-m9-coeffects.md)
- [phase-5-hmr-loader.md](phase-5-hmr-loader.md)
- [STATUS.md](STATUS.md) — live checklist
