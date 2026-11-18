# E5 — Query-informed Thompson priors: a NEGATIVE result (kept as evidence)

**Date:** 2026-11-19 · **Status:** implemented, measured, **default OFF** (`NEXUS_TS_PRIOR_STRENGTH=0`)

## What was tried

Replace the flat 0.5 Thompson posterior for *cold* (never-triggered) resources with a
query-informed Beta prior derived from the resource's fused retrieval score:

```
cold resource:  alpha = 1 + S·m ,  beta = 1 + S·(1−m)     S = NEXUS_TS_PRIOR_STRENGTH
warm resource:  unchanged (pure data posterior, 1+correct / 1+wrong)
```

Motivation: with **4 of 5,068 resources carrying any outcome**, the 0.15 Thompson
weight was a *constant* and discriminated nothing (measured this session). Sourced to
Dynamic Prior TS (arXiv:2602.00943), LLM-derived priors for TS (2608.03382), and
BayesianRouter's offline-prior→online-TS split (2510.02850).

## Measured outcome — gate failed

Golden set, 105 cases (84 skill-asserting), 3 seeds, cross-seed spread 0.000:

| Metric | Baseline (after E4) | With E5 priors (S=2.0) | Δ |
|---|---|---|---|
| hit@k | **0.286** | 0.274 | **−0.012 (13→14 misses; −1 case)** |
| top-1 | 0.119 | 0.119 | 0 |
| top-3 | 0.274 | 0.262 | −0.012 |
| MRR | 0.1895 | 0.1855 | −0.004 |
| pass | 0.267 | 0.267 | 0 |

Receipts: `evals/results/perf/golden-after-e4-fix.json` vs `golden-after-e5.json`.

## Why it failed (the mechanism's design flaw)

The prior mean `m` is min-max normalized **fused RRF score**. The L3 blend already
consumes that same signal through its 0.40 ISO term. So the change did not add
information — it **double-weighted the existing retrieval ranking**, amplifying both
its correct orderings and its errors. Net effect ≈ noise, direction negative.

The real Dynamic Prior TS result targets *exploration probability* on the **Thompson
sd/draw**, not the posterior mean. NEXUS's L3 uses the mean (`scoring.py:69`), so the
paper's mechanism has no surface to act on here. To use it correctly, the blend would
have to sample from the posterior (introducing per-request variance) rather than read
the mean — a larger change, gated behind the same eval harness.

## Decision

Code and rationale retained in `server/router_v2/scoring.py`; **strength defaults to 0**
(flat 0.5 prior, pre-E5 behavior). Re-test with `NEXUS_TS_PRIOR_STRENGTH=2.0` only after
a sampled-posterior blend exists.

This negative result is recorded rather than deleted: it is the first NEXUS change that
the E2 harness *rejected*, which is exactly what the harness was built for.
