-- NEXUS Phase 0: Unify learner tables into nexus.db
-- These tables support Thompson Sampling, IDE events, and self-learning

-- Thompson Sampling model beliefs per (provider, model, task_type)
CREATE TABLE IF NOT EXISTS model_beliefs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    task_type TEXT NOT NULL DEFAULT 'general',
    alpha REAL NOT NULL DEFAULT 1.0,
    beta REAL NOT NULL DEFAULT 1.0,
    total_requests INTEGER NOT NULL DEFAULT 0,
    total_reward REAL NOT NULL DEFAULT 0.0,
    avg_reward REAL NOT NULL DEFAULT 0.0,
    last_updated TEXT NOT NULL DEFAULT (datetime('now')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(provider, model, task_type)
);

-- IDE feedback events (from Claude Code, Aider, OpenCode, etc.)
CREATE TABLE IF NOT EXISTS ide_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    ide TEXT NOT NULL DEFAULT 'unknown',
    model TEXT NOT NULL,
    provider TEXT NOT NULL,
    task_type TEXT NOT NULL DEFAULT 'general',
    reward REAL NOT NULL DEFAULT 0.0,
    metadata TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Routing decisions with Thompson Sampling scores
CREATE TABLE IF NOT EXISTS routing_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    task_summary TEXT NOT NULL DEFAULT '',
    cluster TEXT DEFAULT NULL,
    task_type TEXT NOT NULL DEFAULT 'general',
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    sampled_score REAL NOT NULL DEFAULT 0.0,
    benchmark_prior REAL NOT NULL DEFAULT 0.0,
    approach TEXT DEFAULT 'none',
    candidates_json TEXT DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Task classifications with confidence scores
CREATE TABLE IF NOT EXISTS task_classifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_hash TEXT NOT NULL UNIQUE,
    task_summary TEXT NOT NULL,
    task_type TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.0,
    cluster TEXT DEFAULT NULL,
    hit_count INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Benchmark results (MMLU, HumanEval, MATH500, etc.)
CREATE TABLE IF NOT EXISTS benchmark_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    benchmark TEXT NOT NULL,
    score REAL NOT NULL,
    samples INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(provider, model, benchmark)
);

-- Cluster resource mapping (replaces missing cluster_membership)
CREATE TABLE IF NOT EXISTS cluster_membership (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_name TEXT NOT NULL,
    resource_id INTEGER NOT NULL,
    affinity REAL NOT NULL DEFAULT 0.0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(cluster_name, resource_id),
    FOREIGN KEY (resource_id) REFERENCES resources(id)
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_model_beliefs_lookup ON model_beliefs(provider, model, task_type);
CREATE INDEX IF NOT EXISTS idx_ide_events_type ON ide_events(event_type);
CREATE INDEX IF NOT EXISTS idx_ide_events_model ON ide_events(model);
CREATE INDEX IF NOT EXISTS idx_routing_decisions_model ON routing_decisions(model);
CREATE INDEX IF NOT EXISTS idx_routing_decisions_cluster ON routing_decisions(cluster);
CREATE INDEX IF NOT EXISTS idx_task_classifications_type ON task_classifications(task_type);
CREATE INDEX IF NOT EXISTS idx_benchmark_results_model ON benchmark_results(provider, model);
CREATE INDEX IF NOT EXISTS idx_cluster_membership_cluster ON cluster_membership(cluster_name);