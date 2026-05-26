-- 007_b: Ingestion pipeline tables
-- Idempotent: uses IF NOT EXISTS
CREATE TABLE IF NOT EXISTS ingestion_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,
    source_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    total_chunks INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    last_ingested_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    metadata TEXT
);

CREATE TABLE IF NOT EXISTS ingestion_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    resource_id INTEGER,
    chunk_index INTEGER NOT NULL DEFAULT 0,
    chunk_type TEXT NOT NULL DEFAULT 'section',
    heading TEXT,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    token_count INTEGER NOT NULL DEFAULT 0,
    start_line INTEGER,
    end_line INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (source_id) REFERENCES ingestion_sources(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ingestion_chunks_source ON ingestion_chunks(source_id);
CREATE INDEX IF NOT EXISTS idx_ingestion_chunks_resource ON ingestion_chunks(resource_id);
