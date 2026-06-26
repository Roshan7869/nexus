-- Migration 010 — FTS5 chunks + outcome_log + schema_migrations registry
-- Author: NEXUS redesign execution (2026-06-25)
-- Additive only: no existing data touched. Fully idempotent (IF NOT EXISTS).
-- Companion to: /home/roshan/nexus-atomised-plan.md N2.1/N2.2/N2.4

-- 0. Migration registry (created here because it does not yet exist)
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  REAL NOT NULL DEFAULT (strftime('%s','now')),
    description TEXT
);

-- 1. FTS5 virtual table over ingestion chunks.
--    rowid mirrors ingestion_chunks.id (set explicitly at insert time) so callers
--    can JOIN back to ingestion_chunks / resources for hydration.
--    resource_id / chunk_type / source_id are UNINDEXED (stored, not searched).
--    unicode61 remove_diacritics 2 suits our identifier-heavy, multilingual corpus.
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    content,
    heading,
    resource_id UNINDEXED,
    chunk_type  UNINDEXED,
    source_id   UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 2'
);

-- 2. Explicit agent feedback for the RL loop (ROS reward signal).
--    alternative_resource_ids stored as JSON-encoded TEXT (read back as text).
CREATE TABLE IF NOT EXISTS outcome_log (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_id              INTEGER NOT NULL,
    cluster_id               INTEGER,
    task_intent              TEXT,
    outcome                  TEXT NOT NULL CHECK(outcome IN ('correct','wrong','partial','skip')),
    duration_ms              INTEGER,
    alternative_resource_ids TEXT,   -- JSON array of ints
    notes                    TEXT,
    session_id               TEXT,
    created_at               REAL NOT NULL DEFAULT (strftime('%s','now')),
    FOREIGN KEY(resource_id) REFERENCES resources(id)
);

CREATE INDEX IF NOT EXISTS idx_outcome_resource        ON outcome_log(resource_id);
CREATE INDEX IF NOT EXISTS idx_outcome_cluster_intent   ON outcome_log(cluster_id, task_intent);
CREATE INDEX IF NOT EXISTS idx_outcome_recent           ON outcome_log(created_at DESC);

-- 3. Record this migration. INSERT OR IGNORE keeps it idempotent.
INSERT OR IGNORE INTO schema_migrations (version, description)
VALUES ('010_fts5_outcome_log', 'FTS5 chunks virtual table + outcome_log + schema_migrations registry');