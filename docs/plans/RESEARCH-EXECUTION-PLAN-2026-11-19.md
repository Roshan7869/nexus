# NEXUS Research-Grounded Execution Plan

**Date:** 2026-11-19 · **Base:** 6 parallel research lanes, ~120 arXiv-verified papers (2024–2026)
**Archives:** `D:/nexus-data/research/01…06-*.md` (every claim below traces to a paper ID there)
**Constraint:** unchanged — 8 vCPU, 16 GB, iGPU. CPU-only algorithms + hosted APIs. No local LLM/GPU ever.

---

## 0. Cross-lane consensus (what six independent surveys agreed on)

1. **Measure before you scale.** Lanes 2, 5 and 6 converged independently: single-run
   agentic evals swing **2.2–6.0 pts** and invert **83% of pairwise rankings** (2602.07150,
   2509.24086); unmeasured improvements evaporate under realistic conditions (Wild-Skills
   2604.04323). NEXUS has never run its own benchmark. Every phase below is gated on one.
2. **Retrieval stays the substrate at 5k scale.** No paper runs an LLM/classifier over a
   5,000-class label space (lane 1). Classifier/bandit routing is validated for small
   label spaces (escalation, model choice) only.
3. **The cheapest wins are index-side and prior-side, not inference-side.** Tool-profile
   expansion (Tool-DE 2510.22670, Re-Invoke 2408.01875), metadata-anchored chunking
   (2409.04701, 2312.06648), and informed Beta priors (2602.00943, 2608.03382) all
   change what is computed *before* or *about* routing, not the 200 ms path itself.
4. **Per-arm independence is hopeless at 5k arms × 16% labels.** Strength must be shared
   across contexts/clusters (lane 4; every scale-successful bandit paper parameter-shares).
5. **Swarms lose by default; gating wins.** Only 1 of 6 MAS workflows beats a single
   agent in the cleanest controlled study (BenchAgent 2606.05670); but confidence-gated
   fan-out hits −92% tokens at +13.5% accuracy (iMAD 2511.11306). NEXUS's orchestrator
   should default single-session and fan out only on measured signals.
6. **Consolidation is not optional.** Ungated accumulation makes agents *worse than
   memoryless* in 7/8 domains (AdMem 2611.20605); the fix is measured: ops + counters +
   novelty gates + supersession (Mem0, ACE, A-MEM/SAGE, Zep).

---

## 1. Phase plan

### P0 — The eval harness (gates everything) · effort **S–M** · ~1–2 days

| Item | Source | Detail |
|---|---|---|
| Golden set v2 | lane 6 | Extend the 20-task set to ~200 queries: frozen JSONL of `query → gold resource(s)` built from routing_log outcomes + 15k edges (the ToolRet recipe, 2503.01763 §2) |
| ≥3 seeds + significance | lane 6 | Paired bootstrap / McNemar; measured noise floor auto-rejects sub-noise "wins" (2602.07150, 2509.24086) |
| Thompson propensity logging | lane 4 | Log the draw probability per route → enables IPS offline eval later (2504.08773) |
| Latency on the same curve | lane 6 | Every eval reports hit-rate AND tokens AND p50/p95 — accuracy-only rankings mislead (2407.01502) |

**Accept:** harness runs cold vs. warm router and reports deltas with confidence intervals.
**Why first:** every later phase claims "improvement" — without P0 there is no way to know.

### P1 — Index expansion: tool profiles + metadata-anchored chunks · effort **M** · ~2–3 days

One-time hosted-LLM pass (budgeted like the JEV teacher), zero added inference latency:

| Item | Source |
|---|---|
| Structured profile per resource (inputs/outputs/when-to-use/trigger line) + 3–5 synthetic queries, reindexed into FTS5 + vectors | Tool-DE 2510.22670; Re-Invoke 2408.01875 |
| Backfill the **empty `chunk_embeddings` lane**: chunk >500-token bodies into 64–128-token units, prefix each with `name — description`, max-pool to resource | Dense X 2312.06648 (+10.1 R@20); Late Chunking 2409.04701; Rethink Chunk Size 2505.21700; fixed-size ≈ complex (2606.00881) |
| Fuse chunk lane with metadata lane via **RRF k=60** (balanced sparse/dense = the favorable case) | Bruch et al. 2210.11934; Fusion Tax 2610.30612 |

**Accept (P0-gated):** Recall@5/nDCG@5 +≥10 pts on held-out body-match queries; warm p50 ≤300 ms.
**Skip:** learned fusion weights until ~50–100 labels exist (then per-cluster convex combination only where it beats RRF).

### P2 — Bandit surgery: priors, discounting, context · effort **S** · ~1 day

All local, all CPU, no latency impact:

| Item | Source |
|---|---|
| Replace uniform Beta(1,1) with doc-score-derived priors, ε-targeted at the incumbent | Dynamic Prior TS 2602.00943; LLM-derived priors 2608.03382 |
| Discounted TS (γ ≈ 0.98) so posteriors track repo updates — NEXUS swarm-pulls 109 repos, arms change | dTS 1707.09727; DS-TS 2305.10718 |
| Cluster-informed priors: share strength across the 8 clusters × 15k edges instead of 5,068 independent arms | BayesianRouter 2510.02850; CohortMix-TS 2609.37800 |

**Accept (P0-gated):** golden-set mean relevance up or flat; exploration hits (cold resources surfaced) up at equal or lower token cost.

### P3 — Rerank stage + first-stage simplification · effort **M** · ~2–3 days

| Item | Source |
|---|---|
| CPU pointwise cross-encoder (mxbai-rerank-base 184M or MiniLM-L6, ONNX INT8) reranks fused top-20–50, α-blended with fused score — never replace | 2508.16757 (FlashRank MiniLM-L12 NQ top-10 64.8 vs BM25 56.3); PAN@CLEF blend result; SmartSearch 2603.15599 (182 ms/query CPU top-62) |
| Gate the rerank: skip when intent-margin is confident (NEXUS's L1 ranker is the natural gate) | Adaptive Re-Ranking 2606.25249 (1.15–53× median latency cut) |
| No CoT/reasoning rerankers; hosted listwise only for hard top-10 cases | 2510.08985; RankZephyr 2312.02724 |
| Collapse first-stage executes (the 96-executes profile): at 5k docs exhaustive fused scoring is single-digit ms — one pass, not nineteen | Fusion Tax 2610.30612; lane 5 rec 5 |

**Accept (P0-gated):** +3 pts nDCG@5 at p95 ≤800 ms; confident-skip keeps median warm ≤300 ms.

### P4 — Activate the self-learning loop with researched gates · effort **M** · ~2 days

The EXECUTION-PLAN's P1–P3 (canonicalize → trajectories → distill) exist but run
unthresholded. Wire in the researched admission gates:

| Item | Source |
|---|---|
| Two-gate distillation: support ≥3 successful traces AND one invariant action skeleton (pairwise diff, ≤K typed slots) → register as L2 skill | TRIAGE 2609.01428 (62.3% token savings, 61.5% zero-token reuse; L2 hit 0→57% within 100 queries; ARISE 2606.02994: 2 successes → zero gain, confirming the ≥3 gate) |
| **Mine failures too, not just successes:** during the sparse-trace phase, failure-derived lessons are the signal that doesn't starve (success-pair pipelines score 0% where successes are scarce) | ReasoningBank 2509.25140 (+34.2% rel, −16% steps); SaMuLe 2509.20562 (4.4→20.0% where ExpeL = 0.0%) |
| ACE-style helpful/harmful counters on every lesson/skill; supersede-not-delete; deterministic delta merge at weekly curation | ACE 2510.04618 (label-free, fits 16% outcome coverage) |
| Versioned skills (version/parent/source-traces/last-validated) + weekly replay validation; rollback if Δ-success >20% | 2606.23127; PSN gate from 2607.10113 |
| Bounded active set per route (flat retrieval degrades at ~64–128 entries — NEXUS has 5,068) | 2607.10113 |

**Accept (P0-gated):** on first 200 real traces, ≥30% of repeat-intent queries resolve at L1/L2 with no rise in bad outcomes; an injected wrong skill auto-quarantines within N uses.

### P5 — Memory consolidation loop · effort **M** · ~2 days

| Item | Source |
|---|---|
| Weekly evidence-gated curation: ADD/UPDATE/MERGE/DELETE/NOOP ops citing run_id; CPU novelty gate (cosine ≥0.9/vMF) before any hosted call; neighbor-note evolution on update | Mem0 2504.19413; A-MEM 2502.12110; SAGE 2605.30711 |
| Bi-temporal stamps `{source_commit, verified_at, valid_from, invalidated_at}`; supersession; nightly pass invalidates lessons whose repo moved | Zep 2501.13956; 2606.26511; APEX-MEM 2604.14362 |
| **Stale-reference lint pass — cost-of-inaction is measured:** Google "Agent Distraction" (developers.googleblog.com, 2026-11-06; 913k coding-agent sessions, multi-way fixed-effects, all p<0.0001): agents acting unprompted on stale inline references (83% of such actions unrequested) cost **+70% steps, +77% tokens, +105% wait time** per session; 41% chase already-closed tickets; **higher-capability models distract more**. Countermeasure pattern: trace-verify-then-delete cleanup agents (>95% human acceptance) — apply to vault lessons at weekly curation | Agent Distraction (Google, 2026); pairs with 2606.26511 (cosine cannot separate stale from current) |
| Graph lane (15k edges + PPR) for multi-hop/associative queries only; flat FTS+vector for direct lookups | HippoRAG 2405.14831; 2601.01280 |
| Sleep-time digests: nightly per-resource lesson digests the router reads instead of raw notes | 2504.13171 (~5× test-time compute cut) |

**Accept (P0-gated):** planted stale lesson flagged within one weekly pass; 4-pass audit ≥90% ops correct; multi-hop slice +≥5 pts with warm route ≤500 ms.

### P6 — Gated fan-out + streaming MCCL benchmark · effort **M** · after P0 + ≥500 routes logged

| Item | Source |
|---|---|
| Confidence-gated parallel sessions: router-stage disagreement (already computed), decomposability, task depth | iMAD 2511.11306; 2610.06069; BenchAgent protocol = the acceptance test |
| MAST 3-category labels on failed fan-outs feeding per-category Thompson penalties | 2503.13657 |
| MCCL as a streaming benchmark (Evo-Memory style): frozen replay (retention) → param-mutated (adaptation) → unseen (transfer), tokens on the curve, holdout never tuned | 2511.20857; 2507.21046; 2407.01502 |
| Router reported as cost-quality frontier + unseen-resource zero-shot transfer (register new resources, measure degradation) | RouterBench 2403.12031; UniRoute 2502.08773; BaRP 2510.07429 |

**Accept:** gated fan-out ≥ always-fan-out accuracy at ≤0.5× tokens on a 300-task replay; warm-vs-cold delta significant at p<0.05 over ≥3 runs.

---

## 2. Dependency graph

```
P0 eval ──► P1 index ──► P3 rerank ──► P6 fan-out
   │           │
   ├──────────►┴──► P2 bandits ──► (P4 traces accrue) ──► P4 distill
   │                                                        │
   └──────────► P5 memory ◄─────────────── P4 skills feed lessons ──┘
```

P0 first, always. P1/P2/P5 are mutually independent (parallelizable). P3 wants P1's
chunk lane. P4 needs P2's instrumentation running for trace accumulation. P6 last.

## 3. Explicit non-goals (research-backed refusals)

- **No LLM/classifier over the 5,068-way label space** — validated only for 2–10 arms (lane 1).
- **No default multi-agent swarms** — 5 of 6 lose under controlled comparison (lane 6).
- **No graph-foreverything** — flat wins single-hop; graph costs maintenance (lane 3).
- **No CoT rerankers, no listwise below 3B locally** — underperform at higher cost (lane 5).
- **No growth-without-pruning memory** — worse than memoryless in 7/8 domains (lane 3).
- **No per-arm-independent Thompson beliefs** — hopeless at 5k × 16% coverage (lane 4).
- **GPU/local-LLM anything** — hardware ceiling, standing rule.

## 4. Effort summary

| Phase | Effort | Cumulative |
|---|---|---|
| P0 eval | 1–2 d | 1–2 d |
| P2 bandits | 1 d | 2–3 d |
| P1 index | 2–3 d | 4–6 d |
| P3 rerank | 2–3 d | 6–9 d |
| P4 loop | 2 d | 8–11 d |
| P5 memory | 2 d | 10–13 d |
| P6 fan-out+MCCL | 2 d | 12–15 d |

Realistic calendar: 2–3 weeks interleaved with actual usage (traces must accrue; the
loop is fed by real work, not simulation).

## 5. Definition of done

1. P0 harness frozen; every phase's claim reported with CI and noise floor.
2. Held-out Recall@5 +≥10 pts vs. today's router at warm p50 ≤300 ms.
3. ≥30% of repeat-intent traffic resolving at L1/L2 (0-token) with no outcome regression.
4. Staleness: planted-supersession test passes within one weekly curation pass.
5. MCCL streaming report: retention/adaptation/transfer with ≥3-seed significance.
6. Fan-out policy: gated ≥ always-fan-out at ≤0.5× tokens on the 300-task replay.
