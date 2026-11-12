# config/capability_providers.yaml

Action: **Create**. Status: Proposed execution instructions, design only.

## Purpose

Configure shared tools once, alongside canonical skill roots and authenticated harness profiles.

## Required contract and implementation instructions

Manifest version 1:
skill_roots: [{publisher,root,include_patterns}]
providers: [{id,transport,endpoint_or_argv,credential_ref,requires,timeout_ms,max_inflight,method_policy}]
harness_profiles: [{id,adapter,permitted_native_verbs}]
recommendation_policy: {version,epsilon:0.05,temperature:0.20}
Use /home/roshan/.hermes/skills as the canonical shared skill root; add other verified canonical bundles explicitly. Do not list copied .agents/.cursor mirrors as independent publishers.
Credentials are references to existing secret storage, never raw secret values. Command transports use argv arrays, no shell interpolation. Provider child process environment is allowlisted.
Default local binding stays loopback; each harness gets a distinct bearer credential and project scopes. New credential setup is a deployment action, never embedded in the plan.
Inventory existing config/mcp_servers.json before migrating; every provider gets keep/import/unsupported disposition. Do not assume existing JSON can simply be deleted.

## Algorithmic pseudocode

The block is illustrative pseudocode, not an executable implementation.

```text
LOAD_MANIFEST(path):
  parse schema; reject duplicate provider IDs and unknown transports
  resolve configured roots and dependency IDs
  reject cycles and literal secret fields
  validate method policy and per-provider limits
  compute manifest hash; stage reconciliation
  publish only after complete validation
```

## Acceptance checks and failure behavior

Duplicate providers fail rather than register twice. Invalid update preserves last-known-good manifest. Credentials do not appear in logs, descriptors or errors. Every existing configured provider is accounted for.

## Dependencies and implementation order

Depends on preflight; used by 02,03,06.

Before source edits, read current contents and preserve overlapping user changes. Run required GitNexus upstream impact analysis for every modified symbol and record direct callers, affected flows and risk. If the code changed since this plan was written, reconcile the plan before applying it. Verify using temporary databases and fake providers first. Commit/deploy actions are separate from this design task.

