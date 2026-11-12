# server/capability_runtime.py

Action: **Create**. Status: Proposed execution instructions, design only.

## Purpose

Run registered provider tools through one owner with version pinning, dependency leases, cleanup and idempotency.

## Required contract and implementation instructions

Interfaces: acquire(capability_id,version,session)->Lease; invoke(request)->Invocation; release(lease); reconcile_provider(provider_id,new_descriptor); recover_inflight().
Reuse NexusContext effect/dispose for owned registrations and handles. Maintain provider dependency graph and generation IDs. A context disposer cleans timers, subscriptions, clients and registrations; it cannot undo emails, remote writes or arbitrary tool effects.
A provider is shared by compatible sessions; each invocation holds a generation lease. A configuration change creates a new generation, drains the old one and releases it only after leases finish or bounded cancellation completes.
Invocation request requires capability ID/version, session ID, arguments, idempotency key and optional approval. Bind idempotency to principal; same key with different arguments/version fails.

## Algorithmic pseudocode

The block is illustrative pseudocode, not an executable implementation.

```text
INVOKE(req):
  resolve pinned descriptor; authorize before acquiring resources
  transaction: insert planned invocation or return matching existing result
  acquire ACTIVE provider-generation lease under lifecycle lock
  transaction: mark running and append start event
  execute registered method under provider-specific timeout/bulkhead
  success -> store result reference and succeeded state
  observed provider failure -> failed state and structured error
  lost acknowledgement/timeout after possible side effect -> unknown
  finally release lease; dispose only invocation-owned context
  return stable receipt; never automatically replay an unknown write

RECONCILE_PROVIDER(new):
  reject dependency cycles; validate config and availability
  load new generation with rollback disposers on partial failure
  atomically publish ACTIVE generation
  mark previous generation DRAINING
  reject new leases to old generation; finish existing leases
  dispose old generation exactly once

RECOVER_INFLIGHT():
  mark orphaned running calls unknown; reconcile provider clients
  reconstruct leases from durable invocation records where possible
  require provider reconciliation or manual resolution before retrying writes
```

## Acceptance checks and failure behavior

Failure injection: simultaneous sessions, provider removal, dependency loss, partial initialization, crash after remote side effect, repeated dispose. One shared provider instance per active generation; no leaked leases; unknown writes never execute twice.

## Dependencies and implementation order

Depends on 01–03, 05, 08. Native actions use adapter handshake in 07.

Before source edits, read current contents and preserve overlapping user changes. Run required GitNexus upstream impact analysis for every modified symbol and record direct callers, affected flows and risk. If the code changed since this plan was written, reconcile the plan before applying it. Verify using temporary databases and fake providers first. Commit/deploy actions are separate from this design task.

