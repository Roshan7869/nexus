-- 007_c: Deduplication candidates table
-- Idempotent: uses IF NOT EXISTS
CREATE TABLE IF NOT EXISTS dedup_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_id_a INTEGER NOT NULL,
    resource_id_b INTEGER NOT NULL,
    similarity_score REAL NOT NULL,
    dedup_method TEXT NOT NULL DEFAULT 'cosine',
    status TEXT NOT NULL DEFAULT 'pending',
    merged_into_id INTEGER,
    reviewed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (resource_id_a) REFERENCES resources(id) ON DELETE CASCADE,
    FOREIGN KEY (resource_id_b) REFERENCES resources(id) ON DELETE CASCADE,
    UNIQUE(resource_id_a, resource_id_b)
);

CREATE INDEX IF NOT EXISTS idx_dedup_status ON dedup_candidates(status);
