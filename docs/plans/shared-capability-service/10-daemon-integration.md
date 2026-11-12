# server/nexus_http_daemon.py

Action: **Modify**. Status: Proposed execution instructions, design only.

## Purpose

Make the persistent daemon the single owner of new catalog and provider runtime services.

## Required contract and implementation instructions

Modify startup/lifespan initialization and register capability_api.APIRouter explicitly. Retain existing health payload fields; add capability_service readiness, catalog_revision, active provider generations and degraded reasons.
Create one service graph per process after successful DB schema validation. Bound shutdown drains active invocation leases before disposing providers.
Replace production /find_skills and /route recommendation internals with a compatibility adapter to new recommendation service only under a versioned migration flag. Keep legacy response keys. A routing query must not invoke a tool or fabricate learning.
Default deployment stays one daemon process; do not add a second owner on port 8000. Remove new API paths from the generic unified-app route-copy mechanism; explicit include is the authoritative registration.

## Algorithmic pseudocode

The block is illustrative pseudocode, not an executable implementation.

```text
STARTUP():
  validate DB migration; construct catalog/policy/events/runtime/recommender
  reconcile manifest; publish ready only after catalog usable
  include router once through app construction

LEGACY_ROUTE(task,project,max_results):
  if shared service flag enabled: translate recommendation into legacy skills shape
  otherwise retain current routing
  preserve method, router_mode, session correlation fields

SHUTDOWN():
  stop new invocation admission; drain; mark unresolved calls unknown
  dispose provider generations; close database handles
```

## Acceptance checks and failure behavior

Route registration test catches duplicate path+method pairs. /find_skills and /route return nonempty matching ordered recommendations when using fixed seed and same context. Native unavailable adapter yields explicit status. Startup failure makes readiness 503. Existing gateway routes remain present.

## Dependencies and implementation order

Depends on 09. Run GitNexus impact on startup, route_task_endpoint, find_skills_endpoint and health before edits.

Before source edits, read current contents and preserve overlapping user changes. Run required GitNexus upstream impact analysis for every modified symbol and record direct callers, affected flows and risk. If the code changed since this plan was written, reconcile the plan before applying it. Verify using temporary databases and fake providers first. Commit/deploy actions are separate from this design task.

