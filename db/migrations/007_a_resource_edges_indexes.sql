-- 007_a: Add additional performance indexes on resource_edges
-- Idempotent: uses IF NOT EXISTS
CREATE INDEX IF NOT EXISTS idx_edges_type_weight ON resource_edges(edge_type, weight);
CREATE INDEX IF NOT EXISTS idx_edges_created_at ON resource_edges(created_at);
