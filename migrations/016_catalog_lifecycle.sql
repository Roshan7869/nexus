-- 016: Catalog lifecycle metadata (P2 system design plan)
-- status remains CHECK(active|degraded|broken|archived); sub-reasons live here.
-- Idempotent: ignore duplicate column errors when re-run.

-- archive_reason: zero_token_stub | cold_Nd | multi_cluster_prune | manual | ...
ALTER TABLE resources ADD COLUMN archive_reason TEXT;

-- When status flipped to archived
ALTER TABLE resources ADD COLUMN archived_at TEXT;

-- Free-form lifecycle tag without rewriting CHECK enum
-- Values used by code: draft | active | degraded | archived | purged_pending
ALTER TABLE resources ADD COLUMN lifecycle_state TEXT;

CREATE INDEX IF NOT EXISTS idx_resources_status_tokens
  ON resources(status, token_count);

CREATE INDEX IF NOT EXISTS idx_resources_status_triggered
  ON resources(status, times_triggered);

CREATE INDEX IF NOT EXISTS idx_resources_archive_reason
  ON resources(archive_reason);
