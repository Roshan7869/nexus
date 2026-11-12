# scripts/eval_capability_service.py

Action: **Create**. Status: Proposed execution instructions, design only.

## Purpose

Measure catalog completeness, task relevance and cross-harness service behavior on isolated data.

## Required contract and implementation instructions

Inputs: copied DB, canonical manifest snapshot, pinned golden suite, fixed policy version, seeds and fake/approved providers. Default mode never invokes live external writes.
Golden relevance must compare canonical IDs. Map legacy skill names via explicit aliases; report ambiguous and missing labels as dataset errors. Do not quietly discard unresolved cases or count them as successes.
Metrics: complete catalog coverage, retrieval recall@5, reciprocal rank, top-one relevance where labelled, p50/p95 warm and cold recommendation latency, execution errors, idempotency failures, posterior coverage, cross-harness identity/availability consistency.
Use the same fixed context and seed for cross-harness comparisons, except explicit compatibility differences. Test permitted execution separately from recommendations.
Do not require 95% agreement with legacy v4 as the quality definition. Keep that measure only as diagnostic divergence.

## Algorithmic pseudocode

The block is illustrative pseudocode, not an executable implementation.

```text
EVALUATE(snapshot,cases):
  validate all labels and canonical alias mappings
  verify paginated catalog equals expected source set
  for seed in fixed_seeds:
    for case in labelled_cases:
      recommend in isolated service for each supported harness profile
      score canonical relevant IDs and clusters separately
  measure warmed runs and fresh-process cold runs separately
  perform fake-provider failure/idempotency scenarios
  write JSON receipt containing hashes, revision, policy, seeds, host, measurements
  return nonzero if any required gate fails
```

## Acceptance checks and failure behavior

Proposed gates: 100% canonical catalog coverage; zero duplicate logical registrations; zero cross-session authorization leaks; zero duplicate side effects in retry tests; recall@5 >=0.80 and >=fresh baseline; warm p95 <=500ms on this host with cached indexes, measured separately from cold startup. These are proposed release criteria, not achieved measurements. If unattainable, keep rollout blocked and change criteria only through a recorded design revision.

## Dependencies and implementation order

Depends on 17–20. Output receipt is versioned and hashed; hash is provenance, not proof of immutability.

Before source edits, read current contents and preserve overlapping user changes. Run required GitNexus upstream impact analysis for every modified symbol and record direct callers, affected flows and risk. If the code changed since this plan was written, reconcile the plan before applying it. Verify using temporary databases and fake providers first. Commit/deploy actions are separate from this design task.

