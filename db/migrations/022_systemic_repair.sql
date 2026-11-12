-- db/migrations/022_systemic_repair.sql
-- Migration 022: Systemic Repair & Ledger Reconciliation
-- 1. Restore/merge edges from resource_edges_backup into resource_edges
-- 2. Add missing composite indexes to routing_log
-- 3. Safely add lifecycle columns to resources
-- 4. Clean empty temporary tables
-- 5. Record migration in schema_migrations

BEGIN TRANSACTION;

-- 1. Restore/merge any unlinked edges from resource_edges_backup
INSERT OR IGNORE INTO resource_edges (source_id, target_id, edge_type, weight, co_trigger_count, created_at, updated_at)
SELECT source_id, target_id, edge_type, weight, co_trigger_count, created_at, updated_at 
FROM resource_edges_backup;

-- 2. Add missing indexes to routing_log
CREATE INDEX IF NOT EXISTS idx_routing_log_skill_outcome ON routing_log(skill_name, outcome);
CREATE INDEX IF NOT EXISTS idx_routing_log_intent ON routing_log(intent);

-- 3. Safely add lifecycle columns to resources for deduplication
ALTER TABLE resources ADD COLUMN archive_reason TEXT;
ALTER TABLE resources ADD COLUMN lifecycle_state TEXT;

-- 4. Clean empty temporary tables
DROP TABLE IF EXISTS routing_log_new;

-- 5. Record migration in schema_migrations
INSERT OR IGNORE INTO schema_migrations (version, description)
VALUES ('022_systemic_repair', 'Restore edges from backup, routing_log composite indexes, resources lifecycle columns, drop routing_log_new');

COMMIT;
