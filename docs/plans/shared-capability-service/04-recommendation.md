# server/capability_recommender.py

Action: **Create**. Status: Proposed execution instructions, design only.

## Purpose

Return a reproducible probability distribution over eligible capabilities while preserving catalog access.

## Required contract and implementation instructions

Interfaces: recommend(session_id,query,k,seed=None)->Recommendation; classify_niche(query,project)->Niche; score_distribution(candidates,beliefs)->Distribution.
Use existing taxonomy and retrieval interfaces after auditing them; do not build another embedder. For v1, candidate generation is lexical FTS plus cached semantic retrieval when ready. A lookup by canonical ID always bypasses ranking.
Context niche labels are versioned. Project affects candidate relevance and policy explicitly. Harness affects compatibility; keep initial reward beliefs pooled across compatible harnesses until sufficient evidence supports splitting.
Niche posterior mean uses observed counts and a global per-capability prior:
  global_mean=(1+global_success)/(2+global_success+global_failure)
  alpha=2*global_mean+niche_success
  beta=2*(1-global_mean)+niche_failure
  predicted_success=alpha/(alpha+beta)
This is a hierarchical heuristic, not a claimed exact Bayesian contextual model or calibrated success probability.
Recommendation distribution:
  utility=0.65*normalized_relevance+0.35*predicted_success
  p=(1-epsilon)*softmax(utility/temperature)+epsilon/eligible_count
Initial proposal: epsilon=0.05, temperature=0.20. Store both in a versioned policy. The p values are selection probabilities, not probabilities of task success.
Use full eligible IDs in the distribution. Relevance scores may be sparse; unscored entries get zero relevance but retain exploration mass. Explicit catalog queries remain unlimited via pagination. k is a response budget, not a capability allowlist.

## Algorithmic pseudocode

The block is illustrative pseudocode, not an executable implementation.

```text
RECOMMEND(session,query,k):
  validate 1<=k<=50 and query <=16384 UTF-8 bytes
  pin catalog revision, policy version, niche version
  eligible=filter all visible IDs using cached availability and policy
  if empty: return [] with reasons and discovery handle
  relevance=bounded_retrieve(query,project,eligible,deadline=250ms)
  if retrieval deadline expires: lexical fallback; mark degraded
  compute finite utility and stable softmax (subtract max)
  remaining=eligible
  repeat min(k,len(remaining)):
    compute p conditional on remaining
    draw one ID using stored seed; append ID and conditional p
    remove selected ID from remaining
  persist decision and exact conditional distributions/candidate snapshot
  return recommendations + catalog discovery handle + degradation metadata

Missing beliefs -> prior; invalid model output -> deterministic lexical fallback with policy mode recorded. No learning writes occur here.
```

## Acceptance checks and failure behavior

Check p sums to one within 1e-9, probabilities are finite, eligible cold skills have positive probability, seed replay is identical, and nondefault project fixtures change relevance. Full-policy propensity logging must match each draw. Do not use top-k slate probabilities as single-action propensities.

## Dependencies and implementation order

Depends on 01–03. Default remains shadow until 20 passes.

Before source edits, read current contents and preserve overlapping user changes. Run required GitNexus upstream impact analysis for every modified symbol and record direct callers, affected flows and risk. If the code changed since this plan was written, reconcile the plan before applying it. Verify using temporary databases and fake providers first. Commit/deploy actions are separate from this design task.

