-- Migration 001: Add domain clustering to NEXUS resources
-- Adds cluster assignment columns, cluster definitions, and activation logging

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- Add cluster columns to resources table
ALTER TABLE resources ADD COLUMN cluster TEXT DEFAULT NULL;
ALTER TABLE resources ADD COLUMN cluster_confidence REAL DEFAULT 0.0;

-- Cluster definitions: domain-based resource groupings
CREATE TABLE IF NOT EXISTS cluster_definitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    description TEXT NOT NULL,
    trigger_keywords TEXT NOT NULL DEFAULT '[]',
    resource_types TEXT NOT NULL DEFAULT '[]',
    name_patterns TEXT NOT NULL DEFAULT '[]',
    default_skill TEXT NOT NULL DEFAULT '',
    default_agents TEXT NOT NULL DEFAULT '[]',
    centroid_embedding BLOB,
    auto_activate_threshold REAL DEFAULT 0.35,
    max_resources INTEGER DEFAULT 200,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Cluster activation log: tracks which clusters fire for each query
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

-- Indexes for cluster lookups
CREATE INDEX IF NOT EXISTS idx_resources_cluster ON resources(cluster);
CREATE INDEX IF NOT EXISTS idx_cluster_activation_clusters ON cluster_activation_log(activated_clusters);
CREATE INDEX IF NOT EXISTS idx_cluster_definitions_name ON cluster_definitions(name);