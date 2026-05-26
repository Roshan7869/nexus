-- 007_d: Cluster count auto-update triggers
-- These keep cluster_definitions.resource_count in sync automatically.
-- Idempotent: uses IF NOT EXISTS (SQLite 3.x supports this for triggers)

CREATE TRIGGER IF NOT EXISTS update_cluster_count_after_insert
AFTER INSERT ON cluster_membership
BEGIN
    UPDATE cluster_definitions 
    SET resource_count = (
        SELECT COUNT(*) FROM cluster_membership WHERE cluster_name = NEW.cluster_name
    )
    WHERE name = NEW.cluster_name;
END;

CREATE TRIGGER IF NOT EXISTS update_cluster_count_after_delete
AFTER DELETE ON cluster_membership
BEGIN
    UPDATE cluster_definitions 
    SET resource_count = (
        SELECT COUNT(*) FROM cluster_membership WHERE cluster_name = OLD.cluster_name
    )
    WHERE name = OLD.cluster_name;
END;

CREATE TRIGGER IF NOT EXISTS update_cluster_count_after_update
AFTER UPDATE OF cluster_name ON cluster_membership
BEGIN
    -- decrement old cluster
    UPDATE cluster_definitions 
    SET resource_count = (
        SELECT COUNT(*) FROM cluster_membership WHERE cluster_name = OLD.cluster_name
    )
    WHERE name = OLD.cluster_name;
    -- increment new cluster
    UPDATE cluster_definitions 
    SET resource_count = (
        SELECT COUNT(*) FROM cluster_membership WHERE cluster_name = NEW.cluster_name
    )
    WHERE name = NEW.cluster_name;
END;
