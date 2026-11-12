# server/capability_api.py

Action: **Create**. Status: Proposed execution instructions, design only.

## Purpose

Expose one versioned HTTP service facade that both MCP and HTTP harnesses use.

## Required contract and implementation instructions

APIRouter prefix /capabilities/v1:
POST /sessions -> authenticated session
GET /catalog?cursor=&limit=&query= -> paginated visible descriptors
GET /catalog/{id}?version= -> descriptor
GET /skills/{id}/content?version=&path= -> full selected skill/asset
POST /recommend -> probability-ranked advisory slate
POST /invoke -> shared execution or native action envelope
POST /native-receipts -> correlated harness execution claim
POST /outcomes -> trusted verified feedback
POST /sessions/{id}/close -> durable session close
GET /health -> readiness per catalog/runtime/learning; HTTP 503 if required serving component is unavailable.
No process starts or model loads at module import. Facade depends on injected service handles owned by app lifespan. Every response includes request_id, api_version, catalog_revision where applicable.
Typed errors: INVALID_ARGUMENT 422, UNAUTHORIZED 401, FORBIDDEN 403, NOT_FOUND 404, CONFLICT/VERSION_CHANGED 409, EXPIRED 410, UNAVAILABLE 503, PROVIDER_TIMEOUT 504. Unknown execution outcome remains explicit in its invocation receipt.
Recommended initial bounds: query 16KiB, JSON invocation body 1MiB, list page 1..100, recommendations 1..50; validate individual tool schemas.

## Algorithmic pseudocode

The block is illustrative pseudocode, not an executable implementation.

```text
HANDLE(req):
  authenticate transport identity
  validate body and session/project ownership
  call corresponding catalog/recommender/runtime/events service
  execute blocking SQLite/provider work off the event loop
  map typed errors; redact credentials
  preserve request/decision/invocation IDs end to end
```

## Acceptance checks and failure behavior

Use injected temporary DB and fake providers. All endpoints enforce ownership. Invalid args cannot execute a provider. No empty 200 response hides unavailable components. HTTP and MCP return equivalent normalized descriptors and receipts.

## Dependencies and implementation order

Depends on 02–07.

Before source edits, read current contents and preserve overlapping user changes. Run required GitNexus upstream impact analysis for every modified symbol and record direct callers, affected flows and risk. If the code changed since this plan was written, reconcile the plan before applying it. Verify using temporary databases and fake providers first. Commit/deploy actions are separate from this design task.

