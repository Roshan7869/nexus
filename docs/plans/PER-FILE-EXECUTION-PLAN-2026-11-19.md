# Per-File Execution Plan — merged consensus execution

**Date:** 2026-11-19 · Merges: RESEARCH-EXECUTION-PLAN-2026-11-19 (citations verified 17/17) +
PERF-PLAN-2026-11-18 (measured baselines) + scoreboard corrections (real p50 = 650 ms, p95 = 1,110 ms;
arm coverage 0.08%; route-close 7.9%).
**Checkpoint:** `ff94eb6` (both session lineages snapshotted before this work).

## Corrected baselines (measured, reproducible)

| Metric | Value | Source |
|---|---|---|
| Warm route p50 / p95 (rotating) | 643–651 / ~1,110 ms | this session, 3 independent benches |
| Golden-set accuracy | **never measured** | 105 cases exist, 0 runs |
| `chunk_embeddings` | 0 rows | verified twice |
| Arm outcome coverage | 4 / 5,068 = 0.08% | verified |
| Stubs (<300 B) | 363 | verified |

## Execution order (dependency-driven)

```
E1 checkpoint ──► E2 eval harness + FIRST ACCURACY NUMBER (gates all)
                        │
                        ├──► E3 DB layer (shared conn, cached aggregates) ──► re-bench + re-eval
                        ├──► E4 vector lane backfill (index-side, 0 ms cost) ──► re-eval
                        └──► E5 bandit priors (revive rl/thompson + doc-score priors) ──► re-eval
DEFERRED (external deps): hosted-LLM profiles, cross-encoder rerank, JEV wiring, memory ops, fan-out.
```

---

## E2 — Eval harness · NEW `scripts/eval_router_seeds.py` · reuse `evals/scorer.py`

| File | Change |
|---|---|
| `scripts/eval_router_seeds.py` (new) | Load `evals/golden/routing_cases.yaml` (105 cases); run `route_for_task` per case × N seeds; score via `evals.scorer.score_case`; report top-1/top-3 hit@k, MRR, nDCG@5, per-case latency p50/p95; bootstrap 95% CIs over cases; JSON receipt to `evals/results/perf/`. |

**Accept:** first accuracy number with CIs exists; per-seed variance reported (state-feedback drift is the real variance source — routing writes change counters between seeds).

## E3 — DB layer (PERF P1) · `server/db.py`, `server/router.py`

| File | Change |
|---|---|
| `server/db.py` | + `get_read_connection()`: thread-local cached read connection (WAL + busy_timeout + row_factory), reused across a process; writes keep short-lived connections. |
| `server/router.py` | Route-path reads via `get_read_connection()` (replaces ~19 raw `sqlite3.connect` per request). + TTL cache (60 s) for request-invariant aggregates: `COUNT(*) routing_log`, cluster-sizes GROUP BY. Single commit at route end. |

**Accept (E2-gated):** warm p50 ≤ 400 ms; accuracy delta within CI (no regression).

## E4 — Vector lane backfill · NEW `scripts/backfill_chunk_embeddings.py`

| File | Change |
|---|---|
| `scripts/backfill_chunk_embeddings.py` (new) | Chunk bodies >500 tokens into 64–128-token units prefixed `name — description` (Dense X 2312.06648 granularity); embed via IsoEmbedder; write `chunk_embeddings`; verify `retrieve_chunks(vec=True)` returns vector hits. |
| `server/router_v2/bitvec.py` | Ensure sqlite-vec loads per connection (vec0 module registration). |

**Accept (E2-gated):** chunk_embeddings populated; hybrid retrieval returns both lanes; nDCG re-measured.

## E5 — Bandit surgery (RESEARCH P2, amended) · `server/learning.py`, `server/rl/thompson.py`

| File | Change |
|---|---|
| `server/learning.py` | Doc-score-informed Beta priors: untriggered resources initialize α,β from ISO/embedding score (not Beta(1,1) 50% claim) — Dynamic Prior TS 2602.00943 pattern, local CPU version. |
| `server/rl/thompson.py` | REVIVE (orphaned, 0 importers, already implements discounted TS exp(−age/30 d)): wire as the belief source for cold/untriggered resources in the live path. |

**Accept (E2-gated):** exploration hits (cold resources in top-20) up at equal token cost.

## E6 — Propensity logging (RESEARCH P0 item) · `server/router_v2/scoring.py`

| File | Change |
|---|---|
| `server/router_v2/scoring.py` | Log the Thompson draw probability with each route (enables IPS offline eval, 2504.08773). |

**Accept:** routing_log propensity column populated on new routes.

---

## Non-goals (carried from both plans)
No LLM/classifier over the 5,068-way label space · no default swarms (5/6 lose; gated fan-out is P6, later) ·
no graph-everything · no CoT rerankers · no growth-without-pruning memory · GPU/local-LLM anything.
