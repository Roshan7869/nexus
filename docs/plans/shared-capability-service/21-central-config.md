# core/config.py

Action: **Modify**. Status: Proposed execution instructions, design only.

## Purpose

Add one authoritative shared-capability mode and validated policy limits using the existing config access pattern.

## Required contract and implementation instructions

Add accessor is_shared_capability_serving() and shared_capability_mode() with values legacy|shadow|serve. Default legacy until rollout gates pass. Add validated recommendation epsilon, temperature, time budget and catalog cursor TTL; provider limits remain in the validated manifest.
Follow the file's existing YAML/env/default precedence after inspecting its implementation; do not copy potentially stale documentation precedence.
Expose nonsecret effective config and policy hash for receipts. Authenticated harness identities come from credential mappings configured at deployment, never from an IDE name in the request body.
One mode controls forwarding in daemon, unified facade and MCP bridge. A stale MCP bridge must negotiate API version and receive explicit incompatible errors rather than instantiate a second runtime.

## Algorithmic pseudocode

The block is illustrative pseudocode, not an executable implementation.

```text
RESOLVE_MODE(config):
  read using existing central accessor mechanism
  reject unknown values and invalid numeric bounds
  legacy: old endpoints, catalog test service available only to opted-in principals
  shadow: compute/store new advisory comparisons without changing old responses
  serve: compatibility facades use shared service
  return effective mode with config hash
```

## Acceptance checks and failure behavior

Tests verify precedence as actually implemented, invalid values, no secret leakage, and each mode's observable endpoint behavior. Mode changes must not replay invocations. Run impact before changing LearningConfig/config accessors.

## Dependencies and implementation order

Depends on phase 0. Required before 10–14 integration.

Before source edits, read current contents and preserve overlapping user changes. Run required GitNexus upstream impact analysis for every modified symbol and record direct callers, affected flows and risk. If the code changed since this plan was written, reconcile the plan before applying it. Verify using temporary databases and fake providers first. Commit/deploy actions are separate from this design task.

