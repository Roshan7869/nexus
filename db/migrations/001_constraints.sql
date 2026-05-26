-- Apply DB constraint cascades migration in SQLite

PRAGMA foreign_keys = OFF;

BEGIN TRANSACTION;

-- 1. Migrate cluster_membership
CREATE TABLE new_cluster_membership (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_name TEXT NOT NULL,
    resource_id INTEGER NOT NULL,
    affinity REAL NOT NULL DEFAULT 0.0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(cluster_name, resource_id),
    FOREIGN KEY (resource_id) REFERENCES resources(id) ON DELETE CASCADE
);
INSERT INTO new_cluster_membership (id, cluster_name, resource_id, affinity, created_at)
SELECT id, cluster_name, resource_id, affinity, created_at FROM cluster_membership;
DROP TABLE cluster_membership;
ALTER TABLE new_cluster_membership RENAME TO cluster_membership;
CREATE INDEX IF NOT EXISTS idx_cluster_membership_cluster ON cluster_membership(cluster_name);

-- 2. Migrate bundle_membership
CREATE TABLE new_bundle_membership (
    resource_id INTEGER NOT NULL,
    bundle_name TEXT NOT NULL,
    bundle_kind TEXT NOT NULL DEFAULT 'capability',
    priority INTEGER NOT NULL DEFAULT 50,
    is_canonical INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (resource_id, bundle_name),
    FOREIGN KEY (resource_id) REFERENCES resources(id) ON DELETE CASCADE
);
INSERT INTO new_bundle_membership (resource_id, bundle_name, bundle_kind, priority, is_canonical, created_at)
SELECT resource_id, bundle_name, bundle_kind, priority, is_canonical, created_at FROM bundle_membership;
DROP TABLE bundle_membership;
ALTER TABLE new_bundle_membership RENAME TO bundle_membership;
CREATE INDEX IF NOT EXISTS idx_bundle_membership_bundle ON bundle_membership(bundle_name);

-- 3. Migrate resource_policies
CREATE TABLE new_resource_policies (
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
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (resource_id) REFERENCES resources(id) ON DELETE CASCADE
);
INSERT INTO new_resource_policies (resource_id, execution_mode, side_effect_level, mode_allowance, stage_allowance, allowed_ides, preconditions_json, fallback_priority, confidence_floor, created_at, updated_at)
SELECT resource_id, execution_mode, side_effect_level, mode_allowance, stage_allowance, allowed_ides, preconditions_json, fallback_priority, confidence_floor, created_at, updated_at FROM resource_policies;
DROP TABLE resource_policies;
ALTER TABLE new_resource_policies RENAME TO resource_policies;

COMMIT;

PRAGMA foreign_keys = ON;
