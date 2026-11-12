# server/capability_adapters.py

Action: **Create**. Status: Proposed execution instructions, design only.

## Purpose

Connect harness-native actions without pretending they are centrally executable tools.

## Required contract and implementation instructions

Interfaces: register_harness(authenticated_identity,features)->SessionProfile; prepare_native_action(invocation)->ActionEnvelope; accept_native_receipt(envelope_id,receipt,principal)->Result.
Feature negotiation includes dynamic catalog support, skill/resource reads, supported native verbs and result media types. Profiles for Codex, Claude Code, OpenCode and Hermes start with proven capabilities only.
Shared tools run in Nexus through provider registrations. Native actions run in the owning harness and return a correlated receipt. Skills return instructions plus referenced assets; they are not Python functions or tool schemas.
Action envelopes bind target harness/session, capability version, argument hash, invocation ID, expiry and policy decision. Signing authenticates transport and correlation; it does not prove the claimed result is true.
Harness-specific built-in tools remain native. One Nexus connection centralizes configuration of shared capabilities, but cannot eliminate the harness's own built-in execution machinery.

## Algorithmic pseudocode

The block is illustrative pseudocode, not an executable implementation.

```text
PREPARE_NATIVE_ACTION(req):
  require matching supported native verb
  authorize and persist invocation as native_pending
  issue short-lived signed envelope addressed to exact harness
  return envelope and required result schema

ACCEPT_NATIVE_RECEIPT(envelope,receipt):
  verify identity, signature, expiry, hashes and replay key
  reject receipt from another harness
  record execution claim; persist terminal execution state
  send evidence to trusted verifier if present
  do not treat harness self-report as verified success automatically
```

## Acceptance checks and failure behavior

Contract fixtures for each harness prove discovery/get/invoke or explicit UNSUPPORTED. Unknown native verbs cannot fall back to arbitrary shell execution. Receipts from another session fail; duplicate receipts return previous receipt.

## Dependencies and implementation order

Depends on 01, 03, 05. Concrete host bridges require their actual callable interfaces to be verified during implementation.

Before source edits, read current contents and preserve overlapping user changes. Run required GitNexus upstream impact analysis for every modified symbol and record direct callers, affected flows and risk. If the code changed since this plan was written, reconcile the plan before applying it. Verify using temporary databases and fake providers first. Commit/deploy actions are separate from this design task.

