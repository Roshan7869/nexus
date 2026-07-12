-- Ephemeral tool sessions for NEXUS Second Brain (Phase 1)
-- Load specialized tools per task; dump after task_complete.

CREATE TABLE IF NOT EXISTS tool_sessions (
    session_id TEXT PRIMARY KEY,
    project TEXT NOT NULL DEFAULT 'default',
    query TEXT NOT NULL,
    cluster TEXT,
    task_type TEXT NOT NULL DEFAULT 'general',
    tools_json TEXT NOT NULL DEFAULT '[]',
    context_block TEXT NOT NULL DEFAULT '',
    context_hash TEXT,
    ide TEXT NOT NULL DEFAULT 'claude-code',
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active', 'completed', 'abandoned', 'failed')),
    outcome TEXT CHECK(outcome IN ('correct', 'wrong', 'partial', NULL)),
    summary TEXT NOT NULL DEFAULT '',
    model TEXT,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_tool_sessions_project_status
    ON tool_sessions(project, status);

CREATE INDEX IF NOT EXISTS idx_tool_sessions_active
    ON tool_sessions(status) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS project_contexts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL,
    context_type TEXT NOT NULL DEFAULT 'session_archive'
        CHECK(context_type IN ('session_archive', 'learning', 'research', 'docs')),
    title TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    source_session_id TEXT,
    tags_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (source_session_id) REFERENCES tool_sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_project_contexts_project
    ON project_contexts(project, created_at DESC);