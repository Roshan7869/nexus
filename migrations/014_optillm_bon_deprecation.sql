-- Migration 014 — optillm-bon deprecation (2026-06-28, T6 of second-brain-v2 cutover)
--
-- Status 'deprecated' is the semantic intent per the locked plan, but the
-- current `resources.status` CHECK constraint only allows
-- ('active','degraded','broken','archived'). A full table rebuild to
-- extend the CHECK is risky on a 2,000-row live table; 'archived' carries
-- the same semantic outcome — excluded from the unified_route candidate
-- pool (server/router_v2/__init__.py: filters
-- status NOT IN ('disabled','deprecated','archived')).
--
-- MIGRATION NOTE: when a future migration extends the CHECK to include
-- 'deprecated', the row should be re-tagged for clarity:
--   UPDATE resources SET status='deprecated' WHERE name='optillm-bon';
-- Until then, status='archived' is the durable representation of T6.
--
-- Belief history (routing_log.skill_name='optillm-bon' rows) is preserved
-- unchanged so Thompson Sampling can still query past performance if
-- re-warm is ever desired.

-- Re-tag optillm-bon from 'degraded' to 'archived' (exclusion status).
UPDATE resources
   SET status = 'archived',
       updated_at = datetime('now')
 WHERE name = 'optillm-bon';