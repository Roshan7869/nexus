# db/migrations/023_capability_service.sql

Action: **Create**. Status: Proposed execution instructions, design only.

## Purpose

Add an additive schema; preserve every existing resource, routing row, and belief.

## Required contract and implementation instructions

Tables and constraints:
- capability_catalog(capability_id PK, kind CHECK skill|tool|native_action, publisher, logical_key, current_version, metadata_json, UNIQUE(publisher,logical_key)).
- capability_versions(capability_id,version,content_hash,descriptor_json,created_at, PK(capability_id,version)); immutable.
- capability_sources(source_id PK, capability_id, real_path, original_path, resource_id NULL, provenance_json). One source may refer to only one capability; aliases retain original provenance.
- provider_generations(provider_id,generation PK,state CHECK inactive|loading|active|draining|failed,descriptor_json,lease_count CHECK >=0).
- capability_sessions(session_id PK, principal_id, harness_id, project,context_json,status CHECK active|closed, expires_at).
- recommendation_decisions(decision_id PK,session_id,catalog_revision,policy_version,niche_version,candidate_hash,draws_json,created_at). draws_json contains each draw's remaining set, conditional probability and RNG seed.
- capability_invocations(invocation_id PK,principal_id,session_id,capability_id,version,provider_generation,idempotency_key,args_hash,state CHECK planned|running|succeeded|failed|unknown|native_pending,decision_id NULL,result_ref NULL,UNIQUE(principal_id,idempotency_key)).
- capability_events(event_id PK,invocation_id NULL,session_id,type,payload_json,created_at). Append only.
- capability_rewards(invocation_id PRIMARY KEY, verdict CHECK correct|wrong,evidence_ref,verified_by,created_at). Only a trusted verifier can insert.
- capability_beliefs(capability_id,version,niche_version,niche,successes INTEGER CHECK >=0,failures INTEGER CHECK >=0, PK(capability_id,version,niche_version,niche)).
- catalog_revisions(revision INTEGER PRIMARY KEY,manifest_hash,created_at).
- catalog_revision_members(revision,capability_id,version,availability,metadata_json, PK(revision,capability_id)); pins cursor-visible catalog snapshots with foreign keys to revisions and versions. Retain snapshot rows for cursor TTL=15 minutes and all revisions referenced by decisions; expire only unreferenced snapshots.
- capability_migrations(version PK,checksum,applied_at). Use the existing migration ledger for the migration application receipt; do not invent receipts for earlier migrations.

Use foreign keys, bound parameters, and indexes on sessions(principal_id,status), invocations(session_id,state), events(session_id,created_at), sources(capability_id), beliefs(niche_version,niche).
The implementation must store real_path only for local skills; remote tool descriptors retain provider IDs and schema hashes.

## Algorithmic pseudocode

The block is illustrative pseudocode, not an executable implementation.

```text
APPLY_MIGRATION(copy_db, sql):
  require version 023 is unused; validate current schema against expected preconditions
  BEGIN IMMEDIATE
  create the new tables and indexes
  record version and migration checksum in the existing ledger
  COMMIT
  reopen read-only; validate foreign_key_check and integrity_check
Failure: rollback the whole transaction. Do not execute historical migrations or rewrite resources.
```

## Acceptance checks and failure behavior

Run on a temporary copy and a clean fixture database. A second application must be a no-op only when stored checksum matches. Changed checksum must fail. Unique invocation and reward constraints must reject duplicates. Rollback means disable new readers; retain the additive tables for audit.

## Dependencies and implementation order

Depends on preflight. Unlocks all new modules.

Before source edits, read current contents and preserve overlapping user changes. Run required GitNexus upstream impact analysis for every modified symbol and record direct callers, affected flows and risk. If the code changed since this plan was written, reconcile the plan before applying it. Verify using temporary databases and fake providers first. Commit/deploy actions are separate from this design task.

