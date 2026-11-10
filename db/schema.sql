PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- RCA-F7a/F7d: KG design gaps requiring schema evolution (add idempotently via server/db.py ensure_*):
--   F7a temporal facts   → add valid_at / invalid_at bitemporal envelope to edge/fact tables
--                          (Graphiti 2025; Snodgrass 1999). See docs/rca/F7a-temporal-fact-model.md
--   F7d provenance       → add source_ref / activity / agent to edges (W3C PROV-O 2013)
--                          See docs/rca/F7d-provenance.md
--   F7b identity resolution → new entity_resolution(entity_id, canonical_id, score, decision, method)
--                          (Fellegi-Sunter 1969). See docs/rca/F7b-identity-resolution.md

CREATE TABLE IF NOT EXISTS resources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    resource_type TEXT NOT NULL CHECK(resource_type IN ('skill','agent','command','cli_tool','mcp','hook','topic','design_system','craft','doc','spec')),
    repo_name TEXT NOT NULL DEFAULT 'unknown',
    file_path TEXT NOT NULL,
    description TEXT DEFAULT '',
    body_text TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','degraded','broken','archived')),
    tier INTEGER NOT NULL DEFAULT 2 CHECK(tier BETWEEN 1 AND 3),
    embedding BLOB,
    token_count INTEGER DEFAULT 0,
    times_triggered INTEGER NOT NULL DEFAULT 0,
    times_correct INTEGER NOT NULL DEFAULT 0,
    times_wrong INTEGER NOT NULL DEFAULT 0,
    last_used_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS routing_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_summary TEXT NOT NULL,
    project TEXT NOT NULL DEFAULT 'default',
    matched_tier INTEGER NOT NULL,
    matched_model TEXT NOT NULL,
    matched_skills TEXT NOT NULL DEFAULT '[]',
    actual_model_used TEXT,
    outcome TEXT CHECK(outcome IN ('correct','wrong','partial',NULL)),
    skill_name TEXT,
    tokens_used INTEGER DEFAULT 0,
    -- RC7 (2026-06-27): ide enum documents the REAL set of entrypoint tags.
    -- The live routing_log table predates this CHECK and already accepts the
    -- broader enum (verified via sqlite_master on db/nexus.db). This file is
    -- the documented contract; do NOT run an ALTER that rebuilds the live
    -- table to "enforce" it — that is risky on a live DB and gains nothing
    -- (the live CHECK is already broader). Tags:
    --   opencode    — tool_attention_v4 / intent_classifier entrypoints
    --   claude-code — MCP entrypoints (nexus-mcp*.py), invoked by Claude Code
    --   mcp         — generic MCP client / cold_warm exposure seeding
    --   http        — nexus_http_daemon FastAPI endpoints
    --   auto_invoke — auto_invoke.py + learning._log_auto_invoke retry path
    --   hermes,codex— legacy/other IDE clients
    --   unknown     — truly undeterminable (router.py pre-RC7 default; left
    --                 honest per Curnow 2024 — do NOT impute from matched_model)
    ide TEXT NOT NULL DEFAULT 'unknown' CHECK(ide IN ('opencode','claude-code','mcp','http','auto_invoke','hermes','codex','unknown')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal TEXT NOT NULL,
    project TEXT NOT NULL DEFAULT 'default',
    phases_json TEXT NOT NULL DEFAULT '[]',
    current_phase INTEGER NOT NULL DEFAULT 0,
    phases_completed TEXT NOT NULL DEFAULT '[]',
    phases_remaining TEXT NOT NULL DEFAULT '[]',
    artifacts TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'planning' CHECK(status IN ('planning','active','paused','completed','failed')),
    plan_type TEXT NOT NULL DEFAULT 'adaptive' CHECK(plan_type IN ('adaptive','manual','autopilot')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS execution_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL,
    phase_id TEXT NOT NULL,
    task_summary TEXT NOT NULL DEFAULT '',
    matched_model TEXT NOT NULL DEFAULT 'unknown',
    skills_used TEXT NOT NULL DEFAULT '[]',
    outcome TEXT CHECK(outcome IN ('correct','wrong','partial','skipped','timeout',NULL)),
    tokens_used INTEGER DEFAULT 0,
    -- RC7 (2026-06-27): see routing_log comment — same real-world enum. Live
    -- table predates this CHECK; documented contract only, do NOT ALTER live.
    ide TEXT NOT NULL DEFAULT 'unknown' CHECK(ide IN ('opencode','claude-code','mcp','http','auto_invoke','hermes','codex','unknown')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (plan_id) REFERENCES plans(id)
);

CREATE TABLE IF NOT EXISTS embeddings_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text_hash TEXT NOT NULL UNIQUE,
    text_snippet TEXT NOT NULL,
    model_name TEXT NOT NULL DEFAULT 'nomic-embed-text',
    embedding BLOB NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_resources_type ON resources(resource_type);
CREATE INDEX IF NOT EXISTS idx_resources_status ON resources(status);
CREATE INDEX IF NOT EXISTS idx_resources_tier ON resources(tier);
CREATE INDEX IF NOT EXISTS idx_routing_log_outcome ON routing_log(outcome);
CREATE INDEX IF NOT EXISTS idx_routing_log_created ON routing_log(created_at);
CREATE INDEX IF NOT EXISTS idx_plans_status ON plans(status);
CREATE INDEX IF NOT EXISTS idx_plans_project ON plans(project);
CREATE INDEX IF NOT EXISTS idx_execution_log_plan ON execution_log(plan_id);

-- Phase 4: Embeddings cache (dynamically created by phase4_build_faiss.py)
CREATE TABLE IF NOT EXISTS resource_embeddings (
    resource_id INTEGER PRIMARY KEY,
    model_name TEXT,
    embedding BLOB,
    created_at TEXT
);

-- Phase 10: Reflection tables (dynamically created by reflection.py)
CREATE TABLE IF NOT EXISTS reflection_failures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task TEXT NOT NULL,
    resource_name TEXT NOT NULL,
    resource_id INTEGER,
    expected TEXT,
    actual TEXT,
    root_cause TEXT,
    fix_attempted TEXT,
    fix_successful INTEGER,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reflection_fixes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_id INTEGER,
    failure_summary TEXT,
    fix_description TEXT,
    times_applied INTEGER DEFAULT 0,
    times_successful INTEGER DEFAULT 0,
    last_applied_at TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_reflection_resource ON reflection_failures(resource_id);

-- Phase 8: MCP sync log
CREATE TABLE IF NOT EXISTS mcp_sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT,
    tool_name TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Broker/runtime tables
CREATE TABLE IF NOT EXISTS task_runs (
    id TEXT PRIMARY KEY,
    task_text TEXT NOT NULL,
    project TEXT NOT NULL DEFAULT 'default',
    ide_name TEXT NOT NULL DEFAULT 'unknown',
    mode TEXT NOT NULL DEFAULT 'safemode',
    cwd TEXT NOT NULL DEFAULT '',
    repo_context_json TEXT NOT NULL DEFAULT '{}',
    executor_skill TEXT NOT NULL DEFAULT 'adaptive-imagining-cat',
    status TEXT NOT NULL DEFAULT 'previewed',
    task_type TEXT NOT NULL DEFAULT 'general',
    cluster TEXT,
    bundle_graph_json TEXT NOT NULL DEFAULT '{}',
    plan_json TEXT NOT NULL DEFAULT '{}',
    execution_preview_json TEXT NOT NULL DEFAULT '{}',
    final_report_json TEXT,
    review_status TEXT NOT NULL DEFAULT 'pending',
    plan_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tool_traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_run_id TEXT NOT NULL,
    phase_id TEXT NOT NULL,
    subtask_name TEXT NOT NULL,
    bundle_name TEXT,
    resource_name TEXT NOT NULL,
    resource_type TEXT NOT NULL DEFAULT 'unknown',
    execution_mode TEXT NOT NULL DEFAULT 'review_only',
    side_effect_level TEXT NOT NULL DEFAULT 'read',
    tool_role TEXT NOT NULL DEFAULT 'candidate',
    trace_order INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'selected',
    outcome TEXT,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (task_run_id) REFERENCES task_runs(id)
);

CREATE TABLE IF NOT EXISTS bundle_membership (
    resource_id INTEGER NOT NULL,
    bundle_name TEXT NOT NULL,
    bundle_kind TEXT NOT NULL DEFAULT 'capability',
    priority INTEGER NOT NULL DEFAULT 50,
    is_canonical INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (resource_id, bundle_name)
);

CREATE TABLE IF NOT EXISTS resource_policies (
    resource_id INTEGER PRIMARY KEY,
    execution_mode TEXT NOT NULL DEFAULT 'review_only',
    side_effect_level TEXT NOT NULL DEFAULT 'read',
    mode_allowance TEXT NOT NULL DEFAULT 'both',
    stage_allowance TEXT NOT NULL DEFAULT '[]',
    allowed_ides TEXT NOT NULL DEFAULT '[]',
    preconditions_json TEXT NOT NULL DEFAULT '[]',
    fallback_priority INTEGER NOT NULL DEFAULT 50,
    confidence_floor REAL NOT NULL DEFAULT 0.55,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS user_learnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL DEFAULT 'task_type',
    task_type TEXT NOT NULL DEFAULT 'general',
    project TEXT NOT NULL DEFAULT 'default',
    note TEXT NOT NULL,
    bundles_json TEXT NOT NULL DEFAULT '[]',
    tools_json TEXT NOT NULL DEFAULT '[]',
    created_by TEXT NOT NULL DEFAULT 'user',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS execution_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_run_id TEXT NOT NULL,
    verdict TEXT NOT NULL,
    comment TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (task_run_id) REFERENCES task_runs(id)
);

CREATE TABLE IF NOT EXISTS mistake_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    condition_type TEXT NOT NULL,
    condition_key TEXT NOT NULL,
    pattern_text TEXT NOT NULL,
    recovery_advice TEXT NOT NULL DEFAULT '',
    source_task_run_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_task_runs_status ON task_runs(status);
CREATE INDEX IF NOT EXISTS idx_task_runs_project ON task_runs(project);
CREATE INDEX IF NOT EXISTS idx_tool_traces_task_run ON tool_traces(task_run_id);
CREATE INDEX IF NOT EXISTS idx_bundle_membership_bundle ON bundle_membership(bundle_name);
CREATE INDEX IF NOT EXISTS idx_user_learnings_project ON user_learnings(project);
CREATE INDEX IF NOT EXISTS idx_execution_reviews_run ON execution_reviews(task_run_id);
CREATE INDEX IF NOT EXISTS idx_mistake_patterns_key ON mistake_patterns(condition_type, condition_key);
