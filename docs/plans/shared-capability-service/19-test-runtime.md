# tests/test_capability_runtime.py

Action: **Create**. Status: Proposed execution instructions, design only.

## Purpose

Prove provider ownership, lifecycle and safe retries under failures.

## Required contract and implementation instructions

Fake provider records executions in a temporary durable store. Use two harness principals with overlapping sessions.
Test shared generation leases, dependency disappearance, provider upgrade, cleanup order, partial load failure, shutdown during invocation, unavailable provider, argument/schema rejection and native receipt correlation.
Existing NexusContext.dispose executes inverses at most once per context; use a fresh context for a reactivated generation rather than rearming an already disposed context.

## Algorithmic pseudocode

The block is illustrative pseudocode, not an executable implementation.

```text
TEST_UNKNOWN_WRITE():
  fake provider performs side effect then drops connection
  assert invocation becomes unknown
  retry same idempotency key
  assert no second execution and same unknown receipt

TEST_GENERATION_DRAIN():
  start two leases on old generation
  publish replacement
  assert new lease binds replacement
  release old leases; assert old disposer called once
```

## Acceptance checks and failure behavior

Every failure test asserts handle count, lease count, invocation state, provider call count and relevant event IDs. No production provider or service is killed.

## Dependencies and implementation order

Depends on 06,07.

Before source edits, read current contents and preserve overlapping user changes. Run required GitNexus upstream impact analysis for every modified symbol and record direct callers, affected flows and risk. If the code changed since this plan was written, reconcile the plan before applying it. Verify using temporary databases and fake providers first. Commit/deploy actions are separate from this design task.

