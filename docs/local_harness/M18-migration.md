# M18 — `db/migrations/020_local_harness.sql` (CREATE, session ledger)

## GitNexus queries
```
query({search_query: "tool_sessions migration schema"})
context({name: "ensure_tool_session_schema"})  # idempotent executescript pattern
```
Used: `read tool_session_broker.py:56-100` (idempotent `executescript`, fallback DDL for `tool_sessions` + `project_contexts`), `bash ls db/migrations` (latest `017_reflection_alt_resource_id.sql` → next is `020_*`; 018/019 skipped deliberately to avoid collision with in-flight branches — document if taken).

## Current state
Tool sessions persist via `015_tool_sessions.sql` (or fallback DDL). No local-harness ledger (realm, kind, reconcilations).

## Blast radius: LOW (new tables only; `init_db` applies idempotently)

## Pseudocode (SQL)
```sql
-- 020_local_harness.sql — session ledger for the IDE-local harness (M3/M4 audit)
-- SoT stays SQLite; Redis (M12) NEVER persists here.
CREATE TABLE IF NOT EXISTS local_harness_sessions (
    session_id   TEXT PRIMARY KEY,
    project      TEXT NOT NULL DEFAULT 'default',
    task         TEXT NOT NULL DEFAULT '',
    ide          TEXT NOT NULL DEFAULT 'opencode',
    kind         TEXT NOT NULL DEFAULT 'local_harness',  -- vs 'legacy_task'
    realm        TEXT NOT NULL DEFAULT 'local:default',
    status       TEXT NOT NULL DEFAULT 'active',          -- active|completed|abandoned
    tools_json   TEXT NOT NULL DEFAULT '[]',
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_lhs_project_status ON local_harness_sessions(project, status);

CREATE TABLE IF NOT EXISTS local_harness_reconciles (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project      TEXT NOT NULL DEFAULT 'default',
    added        INTEGER NOT NULL DEFAULT 0,
    removed      INTEGER NOT NULL DEFAULT 0,
    changed      INTEGER NOT NULL DEFAULT 0,
    report_json  TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
```

## Build steps
1. Write file; `python3 scripts/init_db.py` (or broker ensure-path) applies cleanly twice (idempotency).
2. M3 `dispose()` writes `ended_at`+status; M4 `reconcile()` appends a row. No skill/resource DDL touched.
3. Downgrade = `DROP TABLE IF EXISTS` (document, do not automate).

## Tests
`test_migration_020_idempotent`, `test_session_row_lifecycle`.
