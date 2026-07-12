-- 006_okf: Resource sections — structured section-level content for NEXUS resources
-- Splits each resource's body into ordered sections with headings, depth, and token counts.
-- Provides FTS5 search and vec embeddings at the section grain.

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- Core sections table: one row per logical section within a resource
CREATE TABLE IF NOT EXISTS resource_sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_id INTEGER NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    section_order INTEGER NOT NULL DEFAULT 0,
    depth INTEGER NOT NULL DEFAULT 0,
    heading TEXT,
    content TEXT NOT NULL,
    token_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (resource_id) REFERENCES resources(id)
);

CREATE INDEX IF NOT EXISTS idx_resource_sections_resource_id ON resource_sections(resource_id);
CREATE INDEX IF NOT EXISTS idx_resource_sections_order ON resource_sections(resource_id, section_order);

-- FTS5 virtual table for section-level full-text search
-- Uses content='resource_sections' for external content storage (no duplication)
CREATE VIRTUAL TABLE IF NOT EXISTS sections_fts USING fts5(
    content,
    heading,
    resource_id UNINDEXED,
    content='resource_sections',
    content_rowid='id'
);

-- vec index for section-level embedding search (mirrors vec_chunks at section grain)
CREATE TABLE IF NOT EXISTS vec_sections_content(
    rowid INTEGER PRIMARY KEY REFERENCES resource_sections(id),
    vec_content bit(384)
);

-- Triggers: keep sections_fts in sync with resource_sections

CREATE TRIGGER IF NOT EXISTS sync_sections_fts_after_insert
AFTER INSERT ON resource_sections
BEGIN
    INSERT INTO sections_fts(rowid, content, heading, resource_id)
    VALUES (NEW.id, NEW.content, NEW.heading, NEW.resource_id);
END;

CREATE TRIGGER IF NOT EXISTS sync_sections_fts_after_delete
AFTER DELETE ON resource_sections
BEGIN
    INSERT INTO sections_fts(sections_fts, rowid, content, heading, resource_id)
    VALUES ('delete', OLD.id, OLD.content, OLD.heading, OLD.resource_id);
END;

CREATE TRIGGER IF NOT EXISTS sync_sections_fts_after_update
AFTER UPDATE ON resource_sections
BEGIN
    INSERT INTO sections_fts(sections_fts, rowid, content, heading, resource_id)
    VALUES ('delete', OLD.id, OLD.content, OLD.heading, OLD.resource_id);
    INSERT INTO sections_fts(rowid, content, heading, resource_id)
    VALUES (NEW.id, NEW.content, NEW.heading, NEW.resource_id);
END;
