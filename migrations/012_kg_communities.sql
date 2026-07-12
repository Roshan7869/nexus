-- 012: KG community detection tables (P7e/P7g — RC8 densify work).
-- Persisted partition from networkx Louvain resolution sweep (dep-free stand-in
-- for Leiden-CPM, Traag 2019, doi:10.1038/s41598-019-41695-z — out-of-session).
-- These are GRAPH communities over resource_edges; distinct from cluster_membership
-- which holds the 8 manually-tuned domain clusters.

CREATE TABLE IF NOT EXISTS kg_communities (
    id INTEGER PRIMARY KEY,
    resolution REAL NOT NULL,
    size INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kg_community_membership (
    resource_id INTEGER NOT NULL,
    community_id INTEGER NOT NULL,
    PRIMARY KEY (resource_id, community_id)
);

CREATE INDEX IF NOT EXISTS idx_kg_membership_community ON kg_community_membership(community_id);