# Phase 0 — Curate the golden eval set (gate for Phase 2)

Status: NOT STARTED
Depends on: —

## Goal
Produce a golden routing set large enough (≥40–50 labeled cases across clusters) that a
recall@k / MRR comparison between retrieval arms is trustworthy, and capture a **baseline
number for the current `chunks+hybrid` arm**. Without this, Phase 2's "adopt lexical only
on parity" rule (D2) has nothing to stand on.

## Files to touch
| file | current | change |
|------|---------|--------|
| `evals/golden/routing_cases.yaml` | ~2KB, thin | expand / merge curated cases |
| `evals/golden/drafts/coding.yaml` | draft | promote → curated |
| `evals/golden/drafts/general.yaml` | draft | promote → curated |
| `evals/golden/drafts/reasoning.yaml` | draft | promote → curated |
| `evals/golden/drafts/ood.yaml` | draft | promote → curated (OOD sanity) |
| `evals/run_eval.py` | runs eval | confirm/​add `--arm` tag + per-arm metrics |
| `evals/scorer.py` | scores | confirm recall@k + MRR emitted |

## Case schema (target)
```yaml
# evals/golden/routing_cases.yaml
- id: coding-001
  query: "fix a login registration 500 error"
  cluster: quality_security        # expected cluster gate
  expect_resource_any:             # ANY of these = hit (recall@k)
    - investigate
    - gstack-openclaw-investigate
  k: 8
```

## Pseudocode — baseline capture
```
load cases from evals/golden/*.yaml (curated only, not drafts/ after promotion)
for arm in ["chunks+hybrid"]:                      # baseline only in Phase 0
    for case in cases:
        res = retrieve_chunks(case.query, top_k=case.k, corpus="chunks", vec=True)
        hit = any(r.resource_name in case.expect_resource_any for r in res.results)
        rr  = reciprocal_rank(res.results, case.expect_resource_any)
        record(arm, case.id, hit, rr)
report recall@k = mean(hit), MRR = mean(rr) per arm
write baseline → evals/results/baseline_chunks_hybrid.json
```

## What must work (acceptance)
- [x] ≥40 curated cases, each with `cluster` + `expect_resource_any` + `k`.
      (105 merged cases — see STATUS Phase 0.)
- [x] `python evals/run_eval.py --arm chunks+hybrid` runs clean and writes
      `evals/results/baseline_chunks_hybrid.json` with `recall_at_k` + `mrr`.
      (recall@k=0.1905, mrr=0.1373.)
- [x] OOD cases score near-zero recall (sanity: retrieval isn't hallucinating hits).
      (scored OOD recall=0.0; bare-OOD health-only by design.)

## Verify commands
```
python evals/run_eval.py --arm chunks+hybrid
cat evals/results/baseline_chunks_hybrid.json
```

## Impact gate
- Read-only phase w.r.t. `server/`. No `impact()` needed (only eval data + harness flags).

## Risk & rollback
- Risk: LOW (eval-only; no runtime path touched).
- Rollback: `git checkout evals/`.
