-- Production-table parity for the test DB (generated 2026-11-19).
-- WHY: conftest.py's hand-written SCHEMA_SQL declared only 21 of the live
-- DB's 98 tables. Tests exercising newer features died on 'no such table:
-- outcome_log / tool_sessions / note_ledger / hygiene_flags / resource_edges'
-- — 86 of the 90 pre-existing suite failures. This file carries the missing
-- definitions verbatim from the live schema so the test DB matches production.
-- Regenerate with: python scripts/regen_test_schema.py
--
-- Applied by tests/conftest.py::_init_test_database after SCHEMA_SQL.

CREATE TABLE IF NOT EXISTS capability_beliefs (
    capability_id TEXT NOT NULL, version TEXT NOT NULL, niche_version TEXT NOT NULL, niche TEXT NOT NULL,
    successes INTEGER NOT NULL DEFAULT 0 CHECK(typeof(successes)='integer' AND successes>=0),
    failures INTEGER NOT NULL DEFAULT 0 CHECK(typeof(failures)='integer' AND failures>=0),
    PRIMARY KEY(capability_id,version,niche_version,niche),
    FOREIGN KEY(capability_id,version) REFERENCES capability_versions(capability_id,version)
);
CREATE INDEX IF NOT EXISTS capability_beliefs_niche ON capability_beliefs(niche_version,niche);

CREATE TABLE IF NOT EXISTS capability_catalog (
    capability_id TEXT NOT NULL PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('skill','tool','native_action')),
    publisher TEXT NOT NULL, logical_key TEXT NOT NULL, current_version TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}', UNIQUE(publisher, logical_key),
    FOREIGN KEY(capability_id,current_version) REFERENCES capability_versions(capability_id,version)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE IF NOT EXISTS capability_events (
    event_id TEXT NOT NULL PRIMARY KEY, invocation_id TEXT REFERENCES capability_invocations(invocation_id),
    session_id TEXT NOT NULL REFERENCES capability_sessions(session_id), type TEXT NOT NULL,
    payload_json TEXT NOT NULL, created_at REAL NOT NULL DEFAULT (unixepoch())
);
CREATE INDEX IF NOT EXISTS capability_events_session ON capability_events(session_id,created_at);

CREATE TABLE IF NOT EXISTS capability_invocations (
    invocation_id TEXT NOT NULL PRIMARY KEY, principal_id TEXT NOT NULL,
    session_id TEXT NOT NULL REFERENCES capability_sessions(session_id), capability_id TEXT NOT NULL,
    version TEXT NOT NULL, provider_generation INTEGER, idempotency_key TEXT NOT NULL,
    args_hash TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('planned','running','succeeded','failed','unknown','native_pending')),
    decision_id TEXT REFERENCES recommendation_decisions(decision_id), result_ref TEXT,
    UNIQUE(principal_id,idempotency_key),
    FOREIGN KEY(capability_id,version) REFERENCES capability_versions(capability_id,version)
);
CREATE INDEX IF NOT EXISTS capability_invocations_session ON capability_invocations(session_id,state);

CREATE TABLE IF NOT EXISTS capability_migrations (
    version TEXT NOT NULL PRIMARY KEY, checksum TEXT NOT NULL, applied_at REAL NOT NULL DEFAULT (unixepoch())
);

CREATE TABLE IF NOT EXISTS capability_rewards (
    invocation_id TEXT NOT NULL PRIMARY KEY REFERENCES capability_invocations(invocation_id),
    verdict TEXT NOT NULL CHECK(verdict IN ('correct','wrong')), evidence_ref TEXT NOT NULL,
    verified_by TEXT NOT NULL, created_at REAL NOT NULL DEFAULT (unixepoch())
);

CREATE TABLE IF NOT EXISTS capability_sessions (
    session_id TEXT NOT NULL PRIMARY KEY, principal_id TEXT NOT NULL, harness_id TEXT NOT NULL,
    project TEXT NOT NULL, context_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL CHECK(status IN ('active','closed')), expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS capability_sessions_owner ON capability_sessions(principal_id,status);

CREATE TABLE IF NOT EXISTS capability_sources (
    source_id TEXT NOT NULL PRIMARY KEY, capability_id TEXT NOT NULL REFERENCES capability_catalog(capability_id),
    real_path TEXT, original_path TEXT, resource_id INTEGER, provenance_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS capability_sources_capability ON capability_sources(capability_id);

CREATE TABLE IF NOT EXISTS capability_versions (
    capability_id TEXT NOT NULL REFERENCES capability_catalog(capability_id), version TEXT NOT NULL,
    content_hash TEXT NOT NULL, descriptor_json TEXT NOT NULL,
    created_at REAL NOT NULL DEFAULT (unixepoch()), PRIMARY KEY(capability_id,version)
);

CREATE TABLE IF NOT EXISTS catalog_revision_members (
    revision INTEGER NOT NULL REFERENCES catalog_revisions(revision), capability_id TEXT NOT NULL,
    version TEXT NOT NULL, availability TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(revision,capability_id),
    FOREIGN KEY(capability_id,version) REFERENCES capability_versions(capability_id,version)
);

CREATE TABLE IF NOT EXISTS catalog_revisions (
    revision INTEGER PRIMARY KEY, manifest_hash TEXT NOT NULL,
    created_at REAL NOT NULL DEFAULT (unixepoch())
);

CREATE TABLE IF NOT EXISTS chunk_embed_cache (
        text_hash   TEXT PRIMARY KEY,
        embedding   BLOB NOT NULL,
        created_at  TEXT NOT NULL
    );

CREATE TABLE IF NOT EXISTS chunk_embeddings (
        chunk_id   INTEGER PRIMARY KEY,
        text_hash  TEXT NOT NULL,
        embedding  BLOB NOT NULL,
        created_at TEXT NOT NULL,
        resource_id INTEGER
    );
CREATE INDEX IF NOT EXISTS idx_chunk_emb_hash ON chunk_embeddings(text_hash);
CREATE INDEX IF NOT EXISTS idx_chunk_emb_resource ON chunk_embeddings(resource_id);

CREATE TABLE IF NOT EXISTS cluster_activation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT NOT NULL,
    project TEXT NOT NULL DEFAULT 'default',
    activated_clusters TEXT NOT NULL DEFAULT '[]',
    affinity_scores TEXT NOT NULL DEFAULT '{}',
    total_resources_scanned INTEGER DEFAULT 0,
    latency_ms REAL DEFAULT 0.0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cluster_activation_clusters ON cluster_activation_log(activated_clusters);

CREATE TABLE IF NOT EXISTS dedup_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_id_a INTEGER NOT NULL,
    resource_id_b INTEGER NOT NULL,
    similarity_score REAL NOT NULL,
    dedup_method TEXT NOT NULL DEFAULT 'cosine',
    status TEXT NOT NULL DEFAULT 'pending',
    merged_into_id INTEGER,
    reviewed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (resource_id_a) REFERENCES resources(id) ON DELETE CASCADE,
    FOREIGN KEY (resource_id_b) REFERENCES resources(id) ON DELETE CASCADE,
    UNIQUE(resource_id_a, resource_id_b)
);
CREATE INDEX IF NOT EXISTS idx_dedup_status ON dedup_candidates(status);

CREATE TABLE IF NOT EXISTS embeddings_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text_hash TEXT NOT NULL UNIQUE,
    text_snippet TEXT NOT NULL,
    model_name TEXT NOT NULL DEFAULT 'nomic-embed-text',
    embedding BLOB NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
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
CREATE INDEX IF NOT EXISTS idx_execution_log_plan ON execution_log(plan_id);

CREATE TABLE IF NOT EXISTS execution_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_run_id TEXT NOT NULL,
    verdict TEXT NOT NULL,
    comment TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (task_run_id) REFERENCES task_runs(id)
);
CREATE INDEX IF NOT EXISTS idx_execution_reviews_run ON execution_reviews(task_run_id);

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

CREATE TABLE IF NOT EXISTS fts_meta (key TEXT PRIMARY KEY, val TEXT);

CREATE TABLE IF NOT EXISTS hygiene_flags (
    resource_id INTEGER NOT NULL,
    flag TEXT NOT NULL,
    detail TEXT,
    found_at TEXT NOT NULL,
    resolved_at TEXT,
    PRIMARY KEY (resource_id, flag)
);

CREATE TABLE IF NOT EXISTS ingestion_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    resource_id INTEGER,
    chunk_index INTEGER NOT NULL DEFAULT 0,
    chunk_type TEXT NOT NULL DEFAULT 'section',
    heading TEXT,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    token_count INTEGER NOT NULL DEFAULT 0,
    start_line INTEGER,
    end_line INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')), source_file_path TEXT,
    FOREIGN KEY (source_id) REFERENCES ingestion_sources(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ingestion_chunks_source ON ingestion_chunks(source_id);
CREATE INDEX IF NOT EXISTS idx_ingestion_chunks_resource ON ingestion_chunks(resource_id);
CREATE INDEX IF NOT EXISTS idx_chunks_source_file
    ON ingestion_chunks(source_id, source_file_path);

CREATE TABLE IF NOT EXISTS ingestion_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,
    source_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    total_chunks INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    last_ingested_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    metadata TEXT
);

CREATE TABLE IF NOT EXISTS intent_learning (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                verb TEXT NOT NULL,
                correct_skills TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 1,
                timestamp TEXT NOT NULL DEFAULT (datetime('now'))
            );

CREATE TABLE IF NOT EXISTS intent_weights (
                verb TEXT NOT NULL,
                skill_name TEXT NOT NULL,
                adjustment REAL NOT NULL DEFAULT 0.0,
                sample_count INTEGER NOT NULL DEFAULT 1,
                last_updated TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (verb, skill_name)
            );

CREATE TABLE IF NOT EXISTS invalidation_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_id INTEGER NOT NULL,
    reason TEXT NOT NULL,
    invalidated_at TEXT NOT NULL,
    reopened_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_invaudit_res ON invalidation_audit(resource_id);

CREATE TABLE IF NOT EXISTS jev_budget (
    day       TEXT PRIMARY KEY,
    calls     INTEGER NOT NULL DEFAULT 0,
    max_calls INTEGER NOT NULL DEFAULT 40
);

CREATE TABLE IF NOT EXISTS jev_labels (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_hash     TEXT NOT NULL,
    task_summary  TEXT NOT NULL,
    question      TEXT NOT NULL,
    label         TEXT NOT NULL,
    confidence    REAL,
    local_label   TEXT,
    local_conf    REAL,
    agreed        INTEGER,
    mode          TEXT,
    source        TEXT DEFAULT 'jev',
    created_at    TEXT DEFAULT (datetime('now')),
    UNIQUE(task_hash, question)
);
CREATE INDEX IF NOT EXISTS idx_jev_labels_question ON jev_labels(question, label);

CREATE TABLE IF NOT EXISTS kg_communities (
    id INTEGER PRIMARY KEY,
    resolution REAL NOT NULL,
    size INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kg_community_membership (
    resource_id INTEGER NOT NULL,
    community_id INTEGER NOT NULL,
    PRIMARY KEY (resource_id, community_id)
);
CREATE INDEX IF NOT EXISTS idx_kg_membership_community ON kg_community_membership(community_id);

CREATE TABLE IF NOT EXISTS local_harness_reconciles (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project      TEXT NOT NULL DEFAULT 'default',
    added        INTEGER NOT NULL DEFAULT 0,
    removed      INTEGER NOT NULL DEFAULT 0,
    changed      INTEGER NOT NULL DEFAULT 0,
    report_json  TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

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

CREATE TABLE IF NOT EXISTS marker_cache (    ref TEXT PRIMARY KEY,    state TEXT NOT NULL,    checked_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS mcp_sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT,
    tool_name TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
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
CREATE INDEX IF NOT EXISTS idx_mistake_patterns_key ON mistake_patterns(condition_type, condition_key);

CREATE TABLE IF NOT EXISTS note_ledger (
    note_id INTEGER PRIMARY KEY,
    resource_name TEXT,
    claim_key TEXT,
    body_hash TEXT,
    valid_from TEXT NOT NULL,
    invalidated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_note_ledger_key
    ON note_ledger(resource_name, claim_key, invalidated_at);

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
    created_at               REAL NOT NULL DEFAULT (strftime('%s','now')), action_type TEXT, repeat_count_1h INTEGER DEFAULT 0, efc_informativeness REAL, efc_validity REAL, efc_redundancy REAL, efc_memory REAL, efc_score REAL, outcome_source TEXT,
    FOREIGN KEY(resource_id) REFERENCES resources(id)
);
CREATE INDEX IF NOT EXISTS idx_outcome_resource        ON outcome_log(resource_id);
CREATE INDEX IF NOT EXISTS idx_outcome_cluster_intent   ON outcome_log(cluster_id, task_intent);
CREATE INDEX IF NOT EXISTS idx_outcome_recent           ON outcome_log(created_at DESC);

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
CREATE INDEX IF NOT EXISTS idx_plans_status ON plans(status);
CREATE INDEX IF NOT EXISTS idx_plans_project ON plans(project);

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

CREATE TABLE IF NOT EXISTS provider_generations (
    provider_id TEXT NOT NULL, generation INTEGER NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('inactive','loading','active','draining','failed')),
    descriptor_json TEXT NOT NULL, lease_count INTEGER NOT NULL DEFAULT 0 CHECK(lease_count>=0),
    PRIMARY KEY(provider_id,generation)
);

CREATE TABLE IF NOT EXISTS recommendation_decisions (
    decision_id TEXT NOT NULL PRIMARY KEY, session_id TEXT NOT NULL REFERENCES capability_sessions(session_id),
    catalog_revision INTEGER NOT NULL REFERENCES catalog_revisions(revision),
    policy_version TEXT NOT NULL, niche_version TEXT NOT NULL, candidate_hash TEXT NOT NULL,
    draws_json TEXT NOT NULL, created_at REAL NOT NULL DEFAULT (unixepoch())
);

CREATE TABLE IF NOT EXISTS resource_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    target_id INTEGER NOT NULL,
    edge_type TEXT NOT NULL, -- 'dependency', 'alternative', 'documentation_for'
    weight REAL DEFAULT 1.0,
    updated_at TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP, co_trigger_count INTEGER DEFAULT 0,
    FOREIGN KEY (source_id) REFERENCES resources(id) ON DELETE CASCADE,
    FOREIGN KEY (target_id) REFERENCES resources(id) ON DELETE CASCADE,
    UNIQUE(source_id, target_id, edge_type)
);
CREATE INDEX IF NOT EXISTS idx_edges_source ON resource_edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON resource_edges(target_id);

CREATE TABLE IF NOT EXISTS resource_embeddings (
    resource_id INTEGER PRIMARY KEY,
    model_name TEXT,
    embedding BLOB,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS resource_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_id INTEGER NOT NULL,
    body_hash TEXT NOT NULL,
    absorbed_at_commit TEXT,
    absorbed_at_date TEXT,
    valid_from TEXT NOT NULL,
    invalidated_at TEXT,
    UNIQUE(resource_id, valid_from)
);
CREATE INDEX IF NOT EXISTS idx_rv_resource ON resource_versions(resource_id, invalidated_at);

CREATE TABLE IF NOT EXISTS section_embeddings (section_id INTEGER PRIMARY KEY, text_hash TEXT NOT NULL, embedding BLOB NOT NULL);

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
CREATE INDEX IF NOT EXISTS idx_task_runs_status ON task_runs(status);
CREATE INDEX IF NOT EXISTS idx_task_runs_project ON task_runs(project);

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

CREATE TABLE IF NOT EXISTS watchdog_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    route_id TEXT NOT NULL,
    anomaly TEXT NOT NULL,
    ts TEXT NOT NULL,
    detail TEXT
);
