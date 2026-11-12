-- 021_experiments.sql — experiment tree ledger (A06b writer; nothing else writes here).
-- SoT stays SQLite. Freeze enforced by trigger: answered rows immutable (branch a child).
CREATE TABLE IF NOT EXISTS experiments (
    id           TEXT PRIMARY KEY,
    parent_id    TEXT REFERENCES experiments(id),
    project      TEXT NOT NULL DEFAULT 'default',
    title        TEXT NOT NULL DEFAULT '',
    branch       TEXT NOT NULL,
    run_command  TEXT NOT NULL,
    frozen       INTEGER NOT NULL DEFAULT 0 CHECK (frozen IN (0,1)),
    result_json  TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    frozen_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_exp_parent ON experiments(parent_id);
CREATE INDEX IF NOT EXISTS idx_exp_frozen ON experiments(project, frozen);
CREATE TRIGGER IF NOT EXISTS trg_exp_freeze
BEFORE UPDATE ON experiments
WHEN OLD.frozen = 1
BEGIN SELECT RAISE(ABORT, 'experiment frozen: branch a child'); END;
