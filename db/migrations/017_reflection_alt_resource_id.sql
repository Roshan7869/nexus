-- 017: Add alternative_resource_id to reflection_fixes (P4 Step 4.4 / P1 Step 1.0)
-- Replaces the fragile "Use X instead" free-text parse in the legacy router and
-- the v5 reflexion port with a proper FK to resources(id). Idempotent.
ALTER TABLE reflection_fixes ADD COLUMN alternative_resource_id INTEGER REFERENCES resources(id);

-- Backfill from existing fix_description text ("Use <name> instead") for legacy rows.
UPDATE reflection_fixes
SET alternative_resource_id = (
    SELECT r.id FROM resources r
    WHERE r.name = substr(reflection_fixes.fix_description, 5,
                          length(reflection_fixes.fix_description) - 12)
)
WHERE alternative_resource_id IS NULL
  AND fix_description LIKE 'Use % instead';