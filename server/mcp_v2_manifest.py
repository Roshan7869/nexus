"""NEXUS v2 MCP tool manifest — the 22-tool surface (6 always-load + 16 lazy).

This is the single source of truth for which tools the v2 MCP server exposes
and which are loaded into every agent's context upfront (alwaysLoad=true) vs
deferred until referenced (alwaysLoad=false). See redesign plan §3.
"""
from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class ToolSpec:
    name: str
    purpose: str
    always_load: bool

CORE = [
    ToolSpec("nexus_search",            "FTS5-primary + bit[384] re-rank + Thompson rerank; returns top-K chunks", True),
    ToolSpec("nexus_get_chunk",         "Fetch a full chunk (content + metadata) by chunk_id", True),
    ToolSpec("nexus_ingest",            "Ingest a source (dir/repo/url/vault/mcp/cli); returns ingest stats", True),
    ToolSpec("nexus_log_outcome",       "Record agent feedback (correct/wrong/partial/skip) for the RL loop", True),
    ToolSpec("nexus_health",            "Quick system check: counts, FTS integrity, embedder state", True),
    ToolSpec("nexus_resources_list",    "Paginated list of resources by type/cluster (cursor-paged)", True),
]

LAZY = [
    ToolSpec("nexus_get_resource",                "Full resource body by id", False),
    ToolSpec("nexus_similar",                     "Dense-only retrieval (loads embedder if needed)", False),
    ToolSpec("nexus_cluster_centroid_recompute",   "Recompute cluster centroids after ingest", False),
    ToolSpec("nexus_reindex",                     "Rebuild chunks_fts + vec index from ingestion_chunks", False),
    ToolSpec("nexus_get_cluster",                 "Resources in a named cluster", False),
    ToolSpec("nexus_get_neighbors",               "Graph neighbors of a resource (resource_edges)", False),
    ToolSpec("nexus_shortest_path",               "BFS shortest path between two resources", False),
    ToolSpec("nexus_coach_query",                 "Aggregated coaching dashboard (delegates to legacy)", False),
    ToolSpec("nexus_coach_antipatterns",          "Filtered anti-pattern detections (delegates to legacy)", False),
    ToolSpec("nexus_coach_skill_finder",          "Match a prompt pattern to the skill catalog (legacy)", False),
    ToolSpec("nexus_coach_sessions",              "Recent coaching sessions (delegates to legacy)", False),
    ToolSpec("nexus_learn_search",                "Search user-authored learning notes (legacy)", False),
    ToolSpec("nexus_add_learning_note",           "Persist a user-authored learning note (legacy)", False),
    ToolSpec("nexus_design_plan",                "Design a typed execution plan (legacy adaptive-imagining-cat)", False),
    ToolSpec("nexus_execute_phase",              "Execute a phase of an existing plan (legacy)", False),
    ToolSpec("nexus_verify_phase",               "Verify a phase's output (legacy)", False),
]

MANIFEST: list[ToolSpec] = CORE + LAZY

def always_loaded() -> list[ToolSpec]:
    return [t for t in MANIFEST if t.always_load]

def lazy_loaded() -> list[ToolSpec]:
    return [t for t in MANIFEST if not t.always_load]

if __name__ == "__main__":  # quick self-check (N6.1 verification)
    print(f"total={len(MANIFEST)} alwaysLoad={len(always_loaded())} lazy={len(lazy_loaded())}")
    assert len(MANIFEST) == 22 and len(always_loaded()) == 6
    print("MANIFEST_OK")