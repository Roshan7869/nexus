# tests/test_capability_learning.py

Action: **Create**. Status: Proposed execution instructions, design only.

## Purpose

Verify recommendation math, learning evidence and duplicate protection.

## Required contract and implementation instructions

Use a seeded RNG and synthetic capability fixtures; test catalog sizes above the legacy cap. Niche labels and project fixtures must be explicit.
Assert distribution normalization, nonzero eligible exploration mass, no probability for ineligible IDs, correct conditional probabilities after each draw, stable seed replay, finite fallback when relevance values are invalid.
Test feedback duplicates, conflicting verdicts, multi-capability task attribution, unknown outcomes and model version pinning. Verify old corrupted times_* values cannot initialize new priors.

## Algorithmic pseudocode

The block is illustrative pseudocode, not an executable implementation.

```text
TEST_VERIFIED_REWARD_ONCE():
  insert one executed invocation
  submit same verified outcome concurrently
  assert exactly one reward row and one integer increment

TEST_TASK_FEEDBACK_NOT_CREDIT():
  close task with five recommended IDs and verdict=correct
  assert zero capability_rewards
  assert quality priors unchanged

TEST_PROJECT_CONTEXT():
  seed project-specific relevance differences
  assert expected canonical skill ranks higher in its project
```

## Acceptance checks and failure behavior

Run without network, model download or live DB access. Include property checks for normalized distributions and replay, with fixed failing seeds retained.

## Dependencies and implementation order

Depends on 04,05.

Before source edits, read current contents and preserve overlapping user changes. Run required GitNexus upstream impact analysis for every modified symbol and record direct callers, affected flows and risk. If the code changed since this plan was written, reconcile the plan before applying it. Verify using temporary databases and fake providers first. Commit/deploy actions are separate from this design task.

