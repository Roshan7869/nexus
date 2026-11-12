-- BASELINE.sql — characterized live DDL of KG/edge tables
-- Generated 2026-09-25 from db/nexus.db (624 MB, 89 tables) via Python sqlite3.
-- Purpose (F8 §6 step 1): pin the REAL live schema before any migration reconciliation.
-- This closes N3 ('which resource_edges definition is real?'). Non-destructive: read-only dump.

CREATE TABLE resource_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    target_id INTEGER NOT NULL,
    edge_type TEXT NOT NULL DEFAULT 'related_to',
    weight REAL NOT NULL DEFAULT 1.0,
    co_trigger_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (source_id) REFERENCES resources(id) ON DELETE CASCADE,
    FOREIGN KEY (target_id) REFERENCES resources(id) ON DELETE CASCADE,
    UNIQUE(source_id, target_id, edge_type)
);
CREATE INDEX idx_edges_source ON resource_edges(source_id);
CREATE INDEX idx_edges_target ON resource_edges(target_id);
CREATE INDEX idx_edges_type ON resource_edges(edge_type);
CREATE INDEX idx_edges_type_weight ON resource_edges(edge_type, weight);
CREATE INDEX idx_edges_created_at ON resource_edges(created_at);

CREATE TABLE kg_communities (
    id INTEGER PRIMARY KEY,
    resolution REAL NOT NULL,
    size INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE kg_community_membership (
    resource_id INTEGER NOT NULL,
    community_id INTEGER NOT NULL,
    PRIMARY KEY (resource_id, community_id)
);
CREATE INDEX idx_kg_membership_community ON kg_community_membership(community_id);

CREATE TABLE cluster_affinity_history (id INTEGER PRIMARY KEY AUTOINCREMENT, cluster_name TEXT, trigger_query TEXT, old_affinity REAL, new_affinity REAL, reason TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);

CREATE TABLE schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  REAL NOT NULL DEFAULT (strftime('%s','now')),
    description TEXT
);

-- schema_migrations current rows (the 'dead registry' — F8/N2):
--   ('010_fts5_outcome_log', 1782333805.0, 'FTS5 chunks virtual table + outcome_log + schema_migrations registry')
