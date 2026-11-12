# tests/test_capability_catalog.py

Action: **Create**. Status: Proposed execution instructions, design only.

## Purpose

Verify canonical identity and complete access beyond any recommendation slate.

## Required contract and implementation instructions

Fixtures: two explicit publisher roots; one symlink mirror; two same-name different-publisher skills; versions with referenced assets; a provider returning paginated tool descriptors.
Tests: stable IDs on content update, new version hashes, alias retention, private visibility, search pagination completeness, deterministic cursor revision handling, provider-list reconciliation, missing root and parse failure rollback.
Use temporary directories and DB only; never scan full live skill roots from unit tests.

## Algorithmic pseudocode

The block is illustrative pseudocode, not an executable implementation.

```text
TEST_ALL_AVAILABLE():
  seed 25 capabilities; recommend only 8
  traverse every catalog page; assert exactly 25 canonical IDs
  select ID absent from recommendation slate
  read its full skill file or invoke its fake provider
  assert successful access

TEST_SOURCE_ESCAPE():
  create asset symlink outside allowed version root
  assert read fails before returning any content
```

## Acceptance checks and failure behavior

Assertions must be nonempty and compare exact IDs and counts. Test stale cursor behavior on concurrent revision publication and no mixed snapshots.

## Dependencies and implementation order

Depends on 01–03,08.

Before source edits, read current contents and preserve overlapping user changes. Run required GitNexus upstream impact analysis for every modified symbol and record direct callers, affected flows and risk. If the code changed since this plan was written, reconcile the plan before applying it. Verify using temporary databases and fake providers first. Commit/deploy actions are separate from this design task.

