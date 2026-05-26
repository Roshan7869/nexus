-- 007_e: Sync resources.cluster column with cluster_membership
-- Ensures the legacy cluster column stays in sync with the authoritative cluster_membership table.

CREATE TRIGGER IF NOT EXISTS sync_resources_cluster_after_insert
AFTER INSERT ON cluster_membership
BEGIN
    UPDATE resources 
    SET cluster = NEW.cluster_name 
    WHERE id = NEW.resource_id;
END;

CREATE TRIGGER IF NOT EXISTS sync_resources_cluster_after_delete
AFTER DELETE ON cluster_membership
BEGIN
    UPDATE resources 
    SET cluster = NULL 
    WHERE id = OLD.resource_id 
    AND NOT EXISTS (
        SELECT 1 FROM cluster_membership 
        WHERE resource_id = OLD.resource_id AND cluster_name != OLD.cluster_name
    );
END;

CREATE TRIGGER IF NOT EXISTS sync_resources_cluster_after_update
AFTER UPDATE OF cluster_name ON cluster_membership
BEGIN
    UPDATE resources 
    SET cluster = NEW.cluster_name 
    WHERE id = NEW.resource_id;
END;

-- One-time sync: update resources.cluster from cluster_membership for all active resources
-- This reconciles the current divergence between the two columns.
UPDATE resources
SET cluster = (
    SELECT cm.cluster_name 
    FROM cluster_membership cm 
    WHERE cm.resource_id = resources.id 
    ORDER BY cm.created_at DESC LIMIT 1
)
WHERE EXISTS (
    SELECT 1 FROM cluster_membership WHERE resource_id = resources.id
);
