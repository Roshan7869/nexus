# Phase 2 — A/B and conditional embedder drop

Status: NOT STARTED
Depends on: Phase 0, Phase 1

## Goal
Run three retrieval arms on the curated golden set and apply D2's **adoption rule**: flip the
default to pure-lexical sections **only if it holds parity** with the hybrid baseline. This is
the phase that actually "removes RAG" — but only if the data says it's safe.

## Arms under test
| arm | corpus | vec | meaning |
|-----|--------|-----|---------|
| A (baseline) | chunks | True | today's production hybrid |
| B | sections | True | OKF corpus, hybrid intact |
| C | sections | False | OKF corpus, **pure FTS5 lexical** (the target) |

## Adoption rule (explicit, non-negotiable)
```
let base = A.recall_at_k, A.mrr
adopt C  iff  C.recall_at_k >= base.recall_at_k * (1 - EPS)   # EPS = 0.02
         and  C.mrr        >= base.mrr        * (1 - EPS)
elif B >= base:   adopt B        # OKF corpus win, keep embedder
else:             adopt A        # abort corpus move, file a note
```
If Phase 0 was skipped → treat set as untrusted → **max adoption = B** (never C).

## Files to touch
| file:line | current | change (only after verdict) |
|-----------|---------|------------------------------|
| `server/router_v2/__init__.py` | `RETRIEVAL_CORPUS="chunks"` (Phase 1) | set to `"sections"` if B/C win |
| `server/router_v2/__init__.py:278` | `vec=True` | set `vec=False` **iff** C wins |
| `server/router_v2/chunk_retrieval.py:155` | `vec=True` default | update docstring; default stays param-driven |
| `server/cluster_faiss.py` | legacy `cluster_route` FAISS | mark deprecation note if C wins (0002:22) |
| `docs/decisions/0002-okf-retrieval.md` | queued experiment | append RESULT: verdict + numbers |

## Pseudocode — the A/B run
```
cases = load_golden()                       # Phase 0 curated
results = {}
for (arm, corpus, vec) in [("A","chunks",True), ("B","sections",True), ("C","sections",False)]:
    hits, rrs, lat = [], [], []
    for c in cases:
        t0 = now()
        r = retrieve_chunks(c.query, top_k=c.k, corpus=corpus, vec=vec, clusters=c.cluster_gate)
        lat.append(now()-t0)
        hits.append(any(x.resource_name in c.expect_resource_any for x in r.results))
        rs.append(reciprocal_rank(r.results, c.expect_resource_any))
    results[arm] = {recall_at_k: mean(hits), mrr: mean(rs), p50_ms: median(lat)}
write results → evals/results/ab_okf_$(date).json
verdict = apply_adoption_rule(results)      # A | B | C
print(verdict, results)
```

## What must work (acceptance)
- [x] `evals/results/ab_okf_*.json` contains all three arms with recall@k, MRR, p50 latency.
      (`ab_okf_20260925.json` A/B/C — see STATUS Phase 2.)
- [x] A written verdict (A/B/C) with the numbers that justified it. (**A**; appended to
      0002 as RESULT.)
- [x] If C adopted: `used_semantic == False` end-to-end; embedder/FAISS not touched on hot
      path; `retrieve_chunks` p50 latency drops (log the delta — 0002:27 expects the ~5s/case
      eval latency to collapse).
      → waived (C rejected; conditional obligation discharged.)
- [x] If C adopted: 0002 appended with RESULT; `cluster_faiss` flagged legacy-only.
      → waived (C rejected; 0002 RESULT appended for verdict A anyway.)
- [x] `nexus_router_status` / eval still green after the flip.
      (No flip under verdict A; all subsequent evals — Phase 3–5, L1 — ran green.)

## Verify commands
```
python evals/run_eval.py --arm chunks+hybrid --arm sections+hybrid --arm sections+lexical
cat evals/results/ab_okf_*.json
python evals/run_eval.py                       # full suite post-flip
```

## Impact gate (before flipping defaults)
```
impact({target:"unified_route_v5", direction:"upstream"})
impact({target:"retrieve_chunks", direction:"upstream"})
```
The default flip changes production routing → treat as HIGH by policy; require the verdict
JSON as the go/no-go artifact.

## Risk & rollback
- Risk: HIGH (changes production retrieval defaults). Mitigated by: flip is a 1–2 line
  constant change, fully reversible, gated on recorded numbers.
- Rollback: `RETRIEVAL_CORPUS="chunks"`, `vec=True`. Embedder/FAISS were only *deprecated*,
  not deleted, so revert is instant.
