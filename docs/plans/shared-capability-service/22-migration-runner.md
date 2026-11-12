# scripts/migrate_capability_service.py

Action: **Create**. Status: Proposed execution instructions, design only.

## Purpose

Apply the additive schema through a checked migration path with receipts.

## Required contract and implementation instructions

Arguments: --db absolute_path, --dry-run, --expected-preimage-sha256, --receipt absolute_path. No default production database path.
Verify version availability and existing ledger schema before planning SQL. Use SQLite backup API to produce a consistent pre-migration backup; a main-file hash without WAL is insufficient.
Hash the consistent backup, schema and migration bytes. Compare expected preimage against that snapshot, then require a write lock and validate schema again before applying. Record live data_version/change evidence; do not claim snapshot hash alone excludes intervening writes.
Dry run applies migration to the backup copy and emits schema diff, integrity checks and row-count deltas. Real execution requires deployment window/write coordination established by the operator; never stop a production service merely to test rollback.

## Algorithmic pseudocode

The block is illustrative pseudocode, not an executable implementation.

```text
MIGRATE(args):
  require explicit target and writable receipt location
  open target; inspect ledger and schema
  create consistent backup via SQLite API; verify expected backup hash
  if dry_run: apply SQL to disposable copy; validate and report; return
  acquire migration/write coordination and BEGIN IMMEDIATE
  recheck schema, ledger checksum and preconditions
  execute additive DDL; record migration once
  COMMIT; verify foreign keys and integrity; write receipt
  on error: ROLLBACK and return nonzero with explicit failed stage
```

## Acceptance checks and failure behavior

Do not use sqlite3.executescript inside a transaction you expect to retain: its transaction behavior must be accounted for explicitly. Execute parsed individual migration statements on the already locked connection, or let one migration script own its BEGIN/COMMIT, with tests proving rollback on failed DDL.

Dry run changes zero live tables. Repeat same migration returns already-applied with matching checksum; different bytes fail. Schema mismatch and failed DDL leave live data unchanged. Backup/receipt creation failure blocks real migration.

## Dependencies and implementation order

Depends on 01; completes phase 1 deployment path.

Before source edits, read current contents and preserve overlapping user changes. Run required GitNexus upstream impact analysis for every modified symbol and record direct callers, affected flows and risk. If the code changed since this plan was written, reconcile the plan before applying it. Verify using temporary databases and fake providers first. Commit/deploy actions are separate from this design task.

