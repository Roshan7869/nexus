-- AI Engineering Coach — NEXUS SQLite Schema
-- Mirrors the Coach cache JSON shapes into NEXUS tables.
-- All tables gracefully support empty/missing cache data.

-- Tracks when the coach cache was last synced to NEXUS
CREATE TABLE IF NOT EXISTS coach_cache_meta (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version INTEGER NOT NULL DEFAULT 1,
    cache_path TEXT NOT NULL DEFAULT '',
    last_sync_at TEXT,
    cache_updated_at TEXT,
    session_count INTEGER NOT NULL DEFAULT 0,
    workspace_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Per-project practice scores across 5 dimensions
CREATE TABLE IF NOT EXISTS coach_practice_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL DEFAULT 'default',
    date TEXT NOT NULL,
    prompt_quality REAL NOT NULL DEFAULT 0.0,
    session_hygiene REAL NOT NULL DEFAULT 0.0,
    code_review REAL NOT NULL DEFAULT 0.0,
    tool_mastery REAL NOT NULL DEFAULT 0.0,
    context_management REAL NOT NULL DEFAULT 0.0,
    total_score REAL NOT NULL DEFAULT 0.0,
    source TEXT NOT NULL DEFAULT 'cache',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(project, date)
);

-- Anti-pattern detections from Coach rules
CREATE TABLE IF NOT EXISTS coach_antipatterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL DEFAULT 'default',
    rule_id TEXT NOT NULL,
    rule_name TEXT NOT NULL DEFAULT '',
    group_name TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL DEFAULT 'low' CHECK(severity IN ('high', 'medium', 'low')),
    count INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0,
    ratio REAL NOT NULL DEFAULT 0.0,
    description TEXT NOT NULL DEFAULT '',
    suggestion TEXT NOT NULL DEFAULT '',
    details_json TEXT DEFAULT '[]',
    weekly_hist_json TEXT DEFAULT '[]',
    last_detected TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(project, rule_id)
);

-- Aggregated session summaries from Coach cache
CREATE TABLE IF NOT EXISTS coach_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL DEFAULT 'default',
    session_id TEXT NOT NULL,
    harness TEXT NOT NULL DEFAULT '',
    workspace_id TEXT NOT NULL DEFAULT '',
    workspace_name TEXT NOT NULL DEFAULT '',
    date TEXT,
    request_count INTEGER NOT NULL DEFAULT 0,
    model_usage_json TEXT DEFAULT '{}',
    first_activity INTEGER,
    last_activity INTEGER,
    end_reason TEXT DEFAULT 'unknown',
    source TEXT NOT NULL DEFAULT 'cache',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(session_id)
);

-- Prompt-pattern to NEXUS-skill mapping
CREATE TABLE IF NOT EXISTS coach_skill_finder (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL DEFAULT 'default',
    pattern TEXT NOT NULL,
    matched_skill TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.0,
    description TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(pattern, matched_skill)
);

-- Daily activity summary per project
CREATE TABLE IF NOT EXISTS coach_daily_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL DEFAULT 'default',
    date TEXT NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 0,
    session_count INTEGER NOT NULL DEFAULT 0,
    total_loc INTEGER NOT NULL DEFAULT 0,
    unique_models TEXT DEFAULT '[]',
    unique_tools TEXT DEFAULT '[]',
    total_tokens_input INTEGER NOT NULL DEFAULT 0,
    total_tokens_output INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(project, date)
);

-- Weekly burndown tracking
CREATE TABLE IF NOT EXISTS coach_burndown (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL DEFAULT 'default',
    month TEXT NOT NULL,
    budget REAL NOT NULL DEFAULT 0.0,
    consumed REAL NOT NULL DEFAULT 0.0,
    projected REAL NOT NULL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'on-track' CHECK(status IN ('on-track', 'warning', 'over-budget', 'no-data')),
    daily_json TEXT DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(project, month)
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_coach_scores_project ON coach_practice_scores(project);
CREATE INDEX IF NOT EXISTS idx_coach_scores_date ON coach_practice_scores(date);
CREATE INDEX IF NOT EXISTS idx_coach_antipatterns_group ON coach_antipatterns(group_name);
CREATE INDEX IF NOT EXISTS idx_coach_antipatterns_severity ON coach_antipatterns(severity);
CREATE INDEX IF NOT EXISTS idx_coach_sessions_harness ON coach_sessions(harness);
CREATE INDEX IF NOT EXISTS idx_coach_sessions_date ON coach_sessions(date);
CREATE INDEX IF NOT EXISTS idx_coach_daily_date ON coach_daily_activity(date);
