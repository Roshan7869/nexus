# server/capability_catalog.py

Action: **Create**. Status: Proposed execution instructions, design only.

## Purpose

Build a versioned canonical catalog and provide complete paginated discovery.

## Required contract and implementation instructions

Interfaces: reconcile_sources(manifest)->revision; list_capabilities(principal,cursor,limit,query=None)->Page; get_capability(id,version=None)->Descriptor; read_skill(id,version,path)->Content.
Descriptor includes kind, canonical_id, aliases, version, requirements, execution_location, adapter_id, schema_hash, availability and reason.
Stable identity = publisher + logical key; content hash identifies a version, never identity. Equal names from different publishers are not duplicates. Exact mirrored files may share identity only when provenance and policy are equivalent.
The authoritative skill scan starts at /home/roshan/.hermes/skills. Add explicit canonical plugin roots from the provider manifest; follow configured roots once and deduplicate realpaths. Do not recursively scan copied mirrors.
Keep catalog visibility separate from execution authorization. Unauthorized entries must not disclose secrets or private descriptions.

## Algorithmic pseudocode

The block is illustrative pseudocode, not an executable implementation.

```text
RECONCILE(manifest):
  validate manifest and bounded roots
  stage descriptors from filesystem skills and provider tool lists
  resolve symlinks; reject files escaping their configured root
  assign stable IDs; preserve aliases and provenance
  validate collisions, schemas, dependencies, full content hashes
  BEGIN IMMEDIATE
  insert immutable versions; update current-version pointers
  mark missing sources unavailable (retain history)
  publish one catalog revision
  COMMIT

LIST(query,cursor):
  pin catalog revision in opaque signed cursor
  select visible matching descriptors ORDER BY capability_id
  return next page and availability reasons
  if cursor revision expired: return CURSOR_EXPIRED, never silently skip entries

READ_SKILL(id,version,path):
  require kind=skill and visible version
  resolve requested relative asset beneath its version root
  verify hash; return full selected file with citation/provenance
  changed or escaped path -> VERSION_CHANGED or INVALID_PATH
```

## Acceptance checks and failure behavior

Prove traversal across every page equals the canonical source set. A skill outside recommendations remains retrievable. Name collisions remain distinct; mirrors collapse once. Reconcile failure leaves previous revision intact; removal retains historical versions.

## Dependencies and implementation order

Depends on 01, 08. Used by 03–07.

Before source edits, read current contents and preserve overlapping user changes. Run required GitNexus upstream impact analysis for every modified symbol and record direct callers, affected flows and risk. If the code changed since this plan was written, reconcile the plan before applying it. Verify using temporary databases and fake providers first. Commit/deploy actions are separate from this design task.

