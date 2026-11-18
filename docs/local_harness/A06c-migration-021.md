# A06c — `db/migrations/021_experiments.sql` (CREATE, experiment ledger)

## GitNexus queries
```
query({search_query: "tool_sessions migration schema"}) · context({name: "ensure_tool_session_schema"})
```
Used: `read tool_session_broker.py:56-100` (idempotent `executescript` + fallback DDL), `bash ls db/migrations` (latest 017 → 020 taken by M18 → next 021), OpenResearch `store.rs:365-380` (`local_experiments` shape), M18-doc.

## Research-backed context
- **OpenResearch `store.rs` tables** (`local_experiments`, `runs`, `chat_*`): experiments ≠ runs — a node holds branch+command, runs answer it; freeze is a column, not a convention.
- **Cordis Thm80 (quiescence = f(final config)):** ledger must record the full input (branch + command + parent) so any endpoint is reproducible from the row alone.
- **NEXUS RC5** (catalog is a dump): same disease applies to eval history — 021 gives eval rows freshness/lineage instead of a log pile.

## Current state
M18 covers sessions; nothing records experiment nodes, parents, frozen state, or sweep lineage.

## Blast radius: LOW (new tables; `init_db` idempotent; no existing DDL touched)

## Detailed pseudocode (SQL + guard)
```sql
-- 021_experiments.sql — experiment tree ledger (A06b writer; nothing else writes here)
CREATE TABLE IF NOT EXISTS experiments (
    id          TEXT PRIMARY KEY,            -- exp_<slug>
    parent_id   TEXT REFERENCES experiments(id),
    project     TEXT NOT NULL DEFAULT 'default',
    title       TEXT NOT NULL DEFAULT '',
    branch      TEXT NOT NULL,               -- exp/<slug> worktree branch
    run_command TEXT NOT NULL,               -- FIXED contract, inherited verbatim (rule 2)
    frozen      INTEGER NOT NULL DEFAULT 0 CHECK (frozen IN (0,1)),
    result_json TEXT NOT NULL DEFAULT '{}',  -- summary metrics + outcome
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    frozen_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_exp_parent ON experiments(parent_id);
CREATE INDEX IF NOT EXISTS idx_exp_frozen ON experiments(project, frozen);
-- freeze enforcement: answered rows immutable
CREATE TRIGGER IF NOT EXISTS trg_exp_freeze
BEFORE UPDATE ON experiments
WHEN OLD.frozen = 1
BEGIN SELECT RAISE(ABORT, 'experiment frozen: branch a child'); END;
```

## Build steps
1. Write file; double-apply via `init_db` (idempotency); verify trigger aborts direct UPDATE of frozen rows.
2. `nexus_exp.py` (A06b) is the sole writer; broker/execution_plane never touch this table.
3. Numbering note: 018/019 skipped (in-flight branches) — reclaim only if confirmed free.

## Tests
`test_021_idempotent`, `test_freeze_trigger_aborts`, `test_lineage_parent_chain`.
