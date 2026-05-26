-- Add resource graph topology
CREATE TABLE IF NOT EXISTS resource_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    target_id INTEGER NOT NULL,
    edge_type TEXT NOT NULL, -- 'dependency', 'alternative', 'documentation_for'
    weight REAL DEFAULT 1.0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_id) REFERENCES resources(id) ON DELETE CASCADE,
    FOREIGN KEY (target_id) REFERENCES resources(id) ON DELETE CASCADE,
    UNIQUE(source_id, target_id, edge_type)
);

CREATE INDEX IF NOT EXISTS idx_edges_source ON resource_edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON resource_edges(target_id);
