# server/capability_events.py

Action: **Create**. Status: Proposed execution instructions, design only.

## Purpose

Record usage and update quality beliefs once per verified capability invocation.

## Required contract and implementation instructions

Interfaces: append_event(event)->id; record_verified_outcome(invocation_id,verdict,evidence,verifier)->Result; metrics(context)->Counters.
Events include recommended, selected, skill_read, invocation_started, invocation_completed, outcome_verified, session_closed. Reading skill instructions is not proof that the skill was used.
Usage and quality are separate: exposure count, selection count, invocation count, terminal outcome count, unknown outcome count, success posterior. A task with several capabilities does not assign its verdict to all of them. Initially only per-invocation verified evidence updates beliefs; session feedback is retained without automatic credit assignment.
Outcome types correct/wrong update quality; partial/skip/timeout/cancelled are recorded without binary reward. Transport failures are availability metrics, not automatically evidence of a poor skill.
Do not repair or import the existing corrupted counters as trusted priors. Keep legacy metrics visible and labelled legacy.

## Algorithmic pseudocode

The block is illustrative pseudocode, not an executable implementation.

```text
RECORD_OUTCOME(invocation,verdict,evidence,verifier):
  authenticate verifier and validate evidence binds invocation/version
  reject if invocation did not execute or is native_pending
  BEGIN IMMEDIATE
  INSERT reward with UNIQUE invocation_id
  if existing identical reward: return previous receipt
  if conflicting verdict: return CONFLICT; do not overwrite
  increment one integer success OR failure for pinned capability/version/niche
  append verified event
  COMMIT
  return receipt with event ID and posterior inputs

CLOSE_SESSION(session,task_verdict):
  append session event; close status
  leave unverified invocations unknown
  do not spread task_verdict into capability beliefs
```

## Acceptance checks and failure behavior

Submit the same reward concurrently and prove exactly one increment. Replayed events remain idempotent. Fractional rewards fail validation. A multi-tool task without individual evidence creates zero rewards. Demonstrate successes+failures equals distinct verified invocations for each belief key.

## Dependencies and implementation order

Depends on 01, 03. Owns new belief writes; legacy learning remains independent.

Before source edits, read current contents and preserve overlapping user changes. Run required GitNexus upstream impact analysis for every modified symbol and record direct callers, affected flows and risk. If the code changed since this plan was written, reconcile the plan before applying it. Verify using temporary databases and fake providers first. Commit/deploy actions are separate from this design task.

