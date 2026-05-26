"""Graphify ↔ NEXUS Bridge — live knowledge graph mapping for state analysis.

Graphify builds a 6676-node, 10168-edge knowledge graph from the codebase
(tree-sitter AST extraction + Leiden community detection). This bridge makes
that graph queryable during NEXUS planning phases so the IDE always has
complete component-relationship context without holding it in memory.

Integration points:
  1. Phase 1 (State Analysis): graphify_state_analysis() — returns full project
     component graph summary for NEXUS to understand "what exists"
  2. Phase 2 (Planning): graphify_component_context() — for any component/dir,
     returns neighbors, community, god-node status → informs plan structure
  3. Phase 3 (Execution): graphify_execution_context() — live DAG tracking
     using graphify-out's PhaseGraph and ActionLog
  4. Phase 5 (Verification): graphify_impact_analysis() — given changed files,
     returns all potentially affected components via edge traversal
  5. Edge Functions: neighbors, community, shortest_path, god_nodes —
     directly wraps graphify MCP tools for IDE clarity

Usage:
    from server.graphify_nexus_bridge import (
        graphify_state_analysis,
        graphify_component_context,
        graphify_execution_context,
        graphify_impact_analysis,
        GraphifyKnowledgeGraph,
    )

    # Phase 1 snapshot
    state = graphify_state_analysis("/home/roshan/Downloads/Algotrading")
    print(f"Project has {state['stats']['nodes']} components")

    # During planning — what depends on component X?
    ctx = graphify_component_context("src/indicators/")
    print(f"Connected to {len(ctx['neighbors'])} other components")

    # Pre-OOC/OOM snapshot
    g = GraphifyKnowledgeGraph("/home/roshan/Downloads/Algotrading/graphify-out/graph.json")
    g.load()
    context = g.get_planning_context(["src/core/", "src/strategies/"])
    # context fits in < 2KB — IDE never holds the full 10MB graph
"""

import json
import logging
import subprocess
import shlex
from pathlib import Path
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

# ─── Paths ──────────────────────────────────────────────────────────────────
GRAPHIFY_OUT = Path.home() / "Downloads" / "Algotrading" / "graphify-out"
GRAPHIFY_CLI = Path.home() / ".local" / "bin" / "graphify"
GRAPHIFY_PKG = Path.home() / "graphify"

DEFAULT_GRAPH_JSON = GRAPHIFY_OUT / "graph.json"
DEFAULT_REPORT = GRAPHIFY_OUT / "GRAPH_REPORT.md"
DEFAULT_LIVE_TRACKER = GRAPHIFY_OUT / "live_tracker.py"
DEFAULT_PHASE_TRACKER = GRAPHIFY_OUT / "phase_tracker.json"


class GraphifyKnowledgeGraph:
    """Loads and queries graphify's knowledge graph for NEXUS planning.

    The full graph is 10MB (6676 nodes, 10168 edges). We never load it
    entirely into the IDE context — only return query-sized chunks (sub-2KB).
    """

    def __init__(self, graph_path: str = None):
        self.graph_path = Path(graph_path) if graph_path else DEFAULT_GRAPH_JSON
        self._graph = None
        self._nodes = {}
        self._edges = []
        self._communities = {}
        self._god_nodes = set()

    def load(self) -> bool:
        """Load graph from JSON. Returns False if file doesn't exist."""
        if not self.graph_path.exists():
            logger.warning(f"Graph file not found: {self.graph_path}")
            return False
        try:
            with open(self.graph_path) as f:
                data = json.load(f)
            self._nodes = {n.get("id", n.get("label", f"node-{i}")): n
                           for i, n in enumerate(data.get("nodes", []))}
            self._edges = data.get("links", data.get("edges", []))
            # Build community index
            for nid, node in self._nodes.items():
                com = node.get("community", node.get("cluster", "unknown"))
                self._communities.setdefault(com, []).append(nid)
            # Detect god nodes (highest-degree nodes)
            degree = {}
            for e in self._edges:
                src = e.get("source", e.get("from"))
                tgt = e.get("target", e.get("to"))
                degree[src] = degree.get(src, 0) + 1
                degree[tgt] = degree.get(tgt, 0) + 1
            sorted_nodes = sorted(degree.items(), key=lambda x: -x[1])
            self._god_nodes = set(n for n, d in sorted_nodes[:20])
            logger.info(f"Loaded graph: {len(self._nodes)} nodes, {len(self._edges)} edges, "
                        f"{len(self._communities)} communities")
            return True
        except Exception as e:
            logger.error(f"Failed to load graph: {e}")
            return False

    @property
    def is_loaded(self) -> bool:
        return self._graph is not None or bool(self._nodes)

    def get_stats(self) -> Dict:
        """Get graph statistics without loading full graph."""
        if self._nodes:
            return {
                "nodes": len(self._nodes),
                "edges": len(self._edges),
                "communities": len(self._communities),
                "god_nodes": len(self._god_nodes),
                "file": str(self.graph_path),
            }
        # Lightweight stat extraction from file header
        try:
            with open(self.graph_path) as f:
                header = f.read(4096)
            return {
                "nodes": header.count('"id"'),
                "edges": header.count('"source"'),
                "file_size_mb": round(self.graph_path.stat().st_size / 1024 / 1024, 1),
                "file": str(self.graph_path),
            }
        except:
            return {"nodes": 0, "edges": 0, "file": str(self.graph_path)}

    def get_node(self, node_id: str) -> Optional[Dict]:
        return self._nodes.get(node_id)

    def get_neighbors(self, node_id: str, max_depth: int = 1) -> Dict:
        """Get neighbors up to max_depth hops. Shallow = fast, deep = expensive."""
        if not self._nodes:
            return {"node": node_id, "neighbors": [], "error": "Graph not loaded"}
        visited = {node_id}
        frontier = {node_id}
        by_depth = {}
        for depth in range(max_depth):
            next_frontier = set()
            for e in self._edges:
                src = e.get("source", e.get("from"))
                tgt = e.get("target", e.get("to"))
                if src in frontier and tgt not in visited:
                    next_frontier.add(tgt)
                    visited.add(tgt)
                    by_depth.setdefault(depth + 1, []).append({
                        "id": tgt,
                        "label": self._nodes.get(tgt, {}).get("label", tgt),
                        "type": self._nodes.get(tgt, {}).get("type", "unknown"),
                        "via": src,
                        "depth": depth + 1,
                    })
                if tgt in frontier and src not in visited:
                    next_frontier.add(src)
                    visited.add(src)
                    by_depth.setdefault(depth + 1, []).append({
                        "id": src,
                        "label": self._nodes.get(src, {}).get("label", src),
                        "type": self._nodes.get(src, {}).get("type", "unknown"),
                        "via": tgt,
                        "depth": depth + 1,
                    })
            frontier = next_frontier
        return {
            "node": node_id,
            "label": self._nodes.get(node_id, {}).get("label", node_id),
            "total_neighbors": len(visited) - 1,
            "by_depth": by_depth,
            "max_depth": max_depth,
        }

    def get_community(self, community_id: str) -> List[Dict]:
        """Get all nodes in a community."""
        nodes = self._communities.get(community_id, [])
        return [{"id": n, "label": self._nodes.get(n, {}).get("label", n),
                 "type": self._nodes.get(n, {}).get("type", "unknown")}
                for n in nodes[:50]]  # cap at 50 for context safety

    def get_god_nodes(self, top_k: int = 10) -> List[Dict]:
        """Get highest-degree hub nodes."""
        degree = {}
        for e in self._edges:
            src = e.get("source", e.get("from"))
            tgt = e.get("target", e.get("to"))
            degree[src] = degree.get(src, 0) + 1
            degree[tgt] = degree.get(tgt, 0) + 1
        top = sorted(degree.items(), key=lambda x: -x[1])[:top_k]
        return [{"id": nid, "label": self._nodes.get(nid, {}).get("label", nid),
                 "degree": d, "type": self._nodes.get(nid, {}).get("type", "unknown")}
                for nid, d in top]

    def get_planning_context(self, target_dirs: List[str],
                             max_neighbors: int = 15) -> Dict:
        """Return a compact (<2KB) context window for planning.

        Given a list of directories/components, returns their neighbors,
        community membership, and god-node status — enough for the IDE to
        understand relationships without loading the full 10MB graph.
        """
        if not self._nodes:
            return {"error": "Graph not loaded", "context_size_bytes": 0}

        # Find nodes matching target directories
        matched = []
        for nid, node in self._nodes.items():
            label = node.get("label", node.get("file", "") or "")
            for d in target_dirs:
                if d in label or d in nid:
                    matched.append(nid)
                    break

        if not matched:
            return {
                "target_dirs": target_dirs,
                "matched_nodes": 0,
                "context": "No graph nodes match the target directories. "
                           "Run graphify on the project first.",
            }

        # Build compact context
        context = {
            "target_dirs": target_dirs,
            "matched_nodes": len(matched),
            "matched": [],
            "top_neighbors": [],
            "communities_represented": set(),
            "god_nodes_in_context": [],
        }

        for nid in matched[:10]:  # cap at 10 matched nodes
            node = self._nodes[nid]
            com = node.get("community", node.get("cluster", "unknown"))
            context["matched"].append({
                "id": nid,
                "label": node.get("label", nid),
                "type": node.get("type", "unknown"),
                "community": com,
                "is_god_node": nid in self._god_nodes,
            })
            context["communities_represented"].add(com)
            if nid in self._god_nodes:
                context["god_nodes_in_context"].append(nid)

            # Top neighbors for first matched node only
            if len(context["top_neighbors"]) == 0:
                nb = self.get_neighbors(nid, max_depth=1)
                context["top_neighbors"] = [
                    n for depth_list in nb.get("by_depth", {}).values()
                    for n in depth_list
                ][:max_neighbors]

        context["communities_represented"] = list(context["communities_represented"])
        # Estimate size
        raw = json.dumps(context)
        context["context_size_bytes"] = len(raw)
        context["context_size_human"] = f"{len(raw)} bytes ({len(raw)/1024:.1f}KB)"

        return context


# ─── Module-Level Functions ─────────────────────────────────────────────────

_graph_cache: Dict[str, GraphifyKnowledgeGraph] = {}


def _get_graph(graph_path: str = None) -> Optional[GraphifyKnowledgeGraph]:
    """Get cached graph instance."""
    key = graph_path or str(DEFAULT_GRAPH_JSON)
    if key not in _graph_cache:
        g = GraphifyKnowledgeGraph(graph_path)
        if not g.load():
            return None
        _graph_cache[key] = g
    return _graph_cache[key]


def graphify_state_analysis(project_root: str = None) -> Dict:
    """Phase 1 integration: full project component graph summary.

    Returns a compact state snapshot the IDE can use to understand
    what exists without loading the full graph into context.

    Args:
        project_root: Project root to find graphify-out in.
                      Auto-detects from graphify-out/ path.

    Returns:
        Dict with stats, top-level structure, god nodes, communities.
    """
    g = _get_graph()
    if not g:
        return {
            "status": "no_graph",
            "message": "Graphify knowledge graph not found. Run: graphify build",
            "stats": {"nodes": 0, "edges": 0},
        }

    stats = g.get_stats()
    god_nodes = g.get_god_nodes(10)

    # Count by type
    type_counts = {}
    for nid, node in g._nodes.items():
        t = node.get("type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1

    # Top communities by size
    community_sizes = sorted(
        [(c, len(nodes)) for c, nodes in g._communities.items()],
        key=lambda x: -x[1],
    )[:10]

    return {
        "status": "available",
        "stats": stats,
        "type_distribution": type_counts,
        "top_communities": [
            {"community": c, "size": s} for c, s in community_sizes
        ],
        "god_nodes": god_nodes,
        "graph_file": str(g.graph_path),
        "context_size_bytes": len(json.dumps({
            "stats": stats,
            "type_distribution": type_counts,
            "top_god_nodes": god_nodes[:5],
        })),
    }


def graphify_component_context(component_path: str,
                                max_neighbors: int = 15) -> Dict:
    """Phase 2 integration: component context for planning.

    Given a component path, returns what it connects to, its community,
    and its role in the graph — enough to plan changes without full context.

    Args:
        component_path: Path or name of the component to analyze.
        max_neighbors: Max neighbors to return.

    Returns:
        Dict with neighbors, community, god-node status, type info.
    """
    g = _get_graph()
    if not g:
        return {"status": "no_graph"}

    planning = g.get_planning_context([component_path], max_neighbors)
    return {
        "status": "available",
        "component": component_path,
        "planning_context": planning,
        "mcp_query": f"graphify-get_node --name \"{component_path}\"",
    }


def graphify_execution_context(phase_id: str = None) -> Dict:
    """Phase 3 integration: live DAG tracking.

    Reads graphify-out's phase_tracker.json to understand the current
    execution state. This is the bridge between graphify's live tracking
    and NEXUS's plan executor.

    Args:
        phase_id: Optional phase to filter by.

    Returns:
        Dict with current phase, completed tasks, guardrail status.
    """
    tracker_path = DEFAULT_PHASE_TRACKER
    if not tracker_path.exists():
        return {"status": "no_tracker", "message": "No execution tracker found"}

    try:
        data = json.loads(tracker_path.read_text())
    except Exception as e:
        return {"status": "error", "message": str(e)}

    # Extract relevant execution state
    if phase_id:
        phase_data = data.get(phase_id, {})
    else:
        phase_data = data

    return {
        "status": "tracking",
        "source": str(tracker_path),
        "data": phase_data,
        "mcp_query": "graphify-query_graph --mode execution",
    }


def graphify_impact_analysis(changed_files: List[str]) -> Dict:
    """Phase 5 integration: impact analysis for verification.

    Given a list of changed files, find all potentially affected
    components via graph edge traversal.

    Args:
        changed_files: List of file paths that were changed.

    Returns:
        Dict with directly affected and transitively affected components.
    """
    g = _get_graph()
    if not g:
        return {"status": "no_graph", "changed_files": changed_files,
                "directly_affected": [], "transitively_affected": []}

    direct = []
    transitive = []
    seen = set()

    for cf in changed_files:
        ctx = g.get_planning_context([cf], max_neighbors=20)
        for m in ctx.get("matched", []):
            if m["id"] not in seen:
                seen.add(m["id"])
                direct.append(m)
        for nb in ctx.get("top_neighbors", []):
            if nb["id"] not in seen:
                seen.add(nb["id"])
                nb["affected_by"] = cf
                transitive.append(nb)

    return {
        "status": "analyzed",
        "changed_files": changed_files,
        "directly_affected": direct,
        "transitively_affected": transitive,
        "total_affected": len(direct) + len(transitive),
        "mcp_query": "graphify-get_neighbors --depth 2",
    }


def graphify_detect_project(project_root: str) -> Dict:
    """Detect if a project has graphify data and return its state."""
    g_out = Path(project_root) / "graphify-out"
    graph_json = g_out / "graph.json"
    report_md = g_out / "GRAPH_REPORT.md"
    tracker = g_out / "phase_tracker.json"

    return {
        "project": project_root,
        "has_graph": graph_json.exists(),
        "has_report": report_md.exists(),
        "has_tracker": tracker.exists(),
        "graph_size_mb": round(graph_json.stat().st_size / 1024 / 1024, 1) if graph_json.exists() else 0,
        "report_size_kb": round(report_md.stat().st_size / 1024, 1) if report_md.exists() else 0,
        "mcp_query": "graphify-graph_stats" if graph_json.exists() else "graphify build",
    }


def get_mcp_tool_definitions() -> List[Dict]:
    """Return MCP tool definitions for registering in NEXUS MCP bridge."""
    return [
        {
            "name": "graphify_state_analysis",
            "description": "Phase 1: Get full knowledge graph state summary for NEXUS planning. "
                           "Returns component stats, type distribution, communities, god nodes. "
                           "Compact output (< 2KB) — safe for IDE context.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "project_root": {"type": "string",
                                     "description": "Project root path for graphify-out detection"},
                },
            },
        },
        {
            "name": "graphify_component_context",
            "description": "Phase 2: Get component relationship context for planning. "
                           "Returns neighbors, community, type info for a specific component. "
                           "Helps the IDE understand dependencies without loading the full graph.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "component_path": {"type": "string",
                                       "description": "Component path or name to analyze"},
                    "max_neighbors": {"type": "integer", "default": 15},
                },
                "required": ["component_path"],
            },
        },
        {
            "name": "graphify_execution_context",
            "description": "Phase 3: Get live DAG execution state from graphify tracker.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "phase_id": {"type": "string", "description": "Optional phase filter"},
                },
            },
        },
        {
            "name": "graphify_impact_analysis",
            "description": "Phase 5: Given changed files, find all affected components via edge traversal.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "changed_files": {
                        "type": "array", "items": {"type": "string"},
                        "description": "List of changed file paths",
                    },
                },
                "required": ["changed_files"],
            },
        },
        {
            "name": "graphify_detect_project",
            "description": "Check if a project has graphify data and return its state.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "project_root": {"type": "string"},
                },
                "required": ["project_root"],
            },
        },
    ]
