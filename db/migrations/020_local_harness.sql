-- 020_local_harness.sql — session ledger for the IDE-local harness.
-- SoT stays SQLite; Redis (M12) NEVER persists here. Idempotent.
CREATE TABLE IF NOT EXISTS local_harness_sessions (
    session_id   TEXT PRIMARY KEY,
    project      TEXT NOT NULL DEFAULT 'default',
    task         TEXT NOT NULL DEFAULT '',
    ide          TEXT NOT NULL DEFAULT 'opencode',
    kind         TEXT NOT NULL DEFAULT 'local_harness',
    realm        TEXT NOT NULL DEFAULT 'local:default',
    status       TEXT NOT NULL DEFAULT 'active',
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
