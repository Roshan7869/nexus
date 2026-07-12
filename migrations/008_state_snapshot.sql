-- 008: Verified-state snapshot table (L7 — claims-vs-reality reconciliation).
-- Pattern-backed (Great Expectations / dbt tests), not paper-grounded.
-- Holds the current BELIEF about each metric + when it was last verified
-- against the live DB + the source query that produced it + drift status.
-- Run scripts/verify_state.py before authoring any NEXUS plan baseline.
CREATE TABLE IF NOT EXISTS state_snapshot (
    metric          TEXT NOT NULL,
    value           REAL NOT NULL,
    verified_at     REAL NOT NULL,           -- epoch seconds
    source_query    TEXT NOT NULL,            -- the SQL that produced value
    drift_status    TEXT NOT NULL DEFAULT 'unverified',  -- ok|drifted|unverified
    prev_value      REAL,
    tolerance       REAL DEFAULT 0.0,
    PRIMARY KEY (metric)
);