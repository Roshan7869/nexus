# server/capability_policy.py

Action: **Create**. Status: Proposed execution instructions, design only.

## Purpose

Make discoverability, recommendation eligibility, and execution permission separate explicit decisions.

## Required contract and implementation instructions

Interfaces: visibility(principal,descriptor); eligibility(context,descriptor,provider_snapshot); authorize_invocation(principal,session,descriptor,args,approval)->Decision.
Context includes project, niche, harness ID, native capabilities, policy version and caller identity. Identity comes from authenticated transport; ignore body-supplied identity claims.
Execution policies reference provider methods and argument schemas. Native adapters declare supported verbs. An approval is bound to principal, capability version, argument hash, project and expiry.
Policies never use rank or recommendation membership as authorization. Missing compatibility yields an explicit reason, and missing execution permission denies invocation.

## Algorithmic pseudocode

The block is illustrative pseudocode, not an executable implementation.

```text
ELIGIBILITY(ctx,cap):
  if not visible(ctx.principal,cap): return hidden
  if cap.kind=native_action and no matching adapter(ctx.harness): return incompatible
  if declared dependencies unavailable: return unavailable
  if execution policy disallows requested context: return disallowed
  return eligible

AUTHORIZE(request,cap):
  validate session owner and active expiry
  validate current policy and pinned version
  validate bounded arguments against registered schema
  enforce project scope and approval requirements
  return explicit allowed decision or typed denial
```

## Acceptance checks and failure behavior

Tests: nonrecommended permitted capability succeeds; recommended denied capability fails; another harness cannot reuse an approval or session; unknown adapter methods fail; unavailable capabilities remain discoverable with reasons.

## Dependencies and implementation order

Depends on 01, 02, 08.

Before source edits, read current contents and preserve overlapping user changes. Run required GitNexus upstream impact analysis for every modified symbol and record direct callers, affected flows and risk. If the code changed since this plan was written, reconcile the plan before applying it. Verify using temporary databases and fake providers first. Commit/deploy actions are separate from this design task.

