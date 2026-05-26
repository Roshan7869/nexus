"""NEXUS v4 Auto-Trigger Engine.

Signal-based cluster activation. When a task arrives, detect which
domain cluster(s) should activate based on trigger keywords and patterns,
then return the cluster name, affinity, default skill, and default agents.

This is the dispatch mechanism that maps user intent to NEXUS worker teams.
"""

import logging
import re
from typing import Optional

from server.cluster_config import CLUSTER_DEFINITIONS, CLUSTER_ORDER, DEFAULT_SKILL_MAP, DEFAULT_AGENT_MAP

logger = logging.getLogger(__name__)

# Expanded trigger patterns with weights for fine-grained matching
TRIGGER_PATTERNS = {
    "analyzer_planner": {
        "signals": [
            "analyze", "plan", "review", "investigate", "debug", "audit",
            "benchmark", "assess", "diagnose", "evaluate", "measure",
            "optimize", "plan-eng", "plan-design", "plan-ceo",
            "code review", "performance", "token", "retro", "retrospective",
            "health", "repair", "integrity", "orchestrate",
            "heal", "nexus", "recalibrate", "auto-repair",
            "ruflo", "swarm", "memory", "hooks", "agent spawn",
            "gstack review", "gstack investigate",
            "plan executor", "resume plan", "checkpoint", "long running",
            "phase execution", "task tracking", "subtask", "graphify",
            "nexus executor", "auto-resume",
            "knowledge graph", "component map", "state analysis",
            "impact analysis", "graph neighbor", "component context",
        ],
        "default_skill": "adaptive-imagining-cat",
        "default_agents": ["researcher", "code-reviewer", "hierarchical-coordinator", "mesh-coordinator", "plan-executor"],
    },
    "architect": {
        "signals": [
            "architecture", "design", "ADR", "spec", "implement", "build",
            "autopilot", "v3-core", "v3-ddd", "v3-integration", "v3-security",
            "blueprint", "pattern", "model", "bounded context", "DDD",
            "domain-driven", "structure",
            "ruflo ADR", "swarm architecture", "multi-agent", "agent topology",
        ],
        "default_skill": "autopilot",
        "default_agents": ["architect", "specification", "system-architect"],
    },
    "frontend_ui": {
        "signals": [
            "UI", "UX", "frontend", "component", "CSS", "design system",
            "layout", "styling", "responsive", "accessibility", "animation",
            "React", "Next", "wireframe", "prototype", "dashboard",
            "landing page", "slide", "deck", "HTML", "Tailwind", "figma",
            "button", "modal", "form",
            "gstack browse", "design shotgun", "design review",
        ],
        "default_skill": "frontend-ui-engineering",
        "default_agents": ["designer"],
    },
    "backend_api": {
        "signals": [
            "API", "endpoint", "database", "auth", "middleware", "server",
            "schema", "migration", "Supabase", "backend", "Django", "Spring",
            "PostgreSQL", "SQL", "ORM", "REST", "GraphQL", "RLS", "payment",
            "serializer", "view", "controller", "route",
            "ruflo MCP", "MCP server", "tool registry", "MCP transport",
        ],
        "default_skill": "backend-patterns",
        "default_agents": ["database-admin", "backend-dev"],
    },
    "devops_infra": {
        "signals": [
            "deploy", "ship", "CI/CD", "Docker", "Kubernetes", "infrastructure",
            "monitoring", "canary", "release", "devops", "Vercel", "gstack",
            "setup", "teardown", "config", "health check", "pipeline",
            "staging", "production", "container", "nginx",
            "gstack ship", "gstack deploy", "land and deploy",
        ],
        "default_skill": "ship",
        "default_agents": ["performance-engineer"],
    },
    "quality_security": {
        "signals": [
            "test", "QA", "verify", "coverage", "security scan", "lint", "TDD",
            "e2e", "integration test", "unit test", "verification", "quality",
            "security audit", "hardening", "guard", "OWASP", "XSS", "CSRF",
            "vulnerability", "penetration", "fuzz",
            "gstack qa", "gstack test", "gstack health",
            "ruflo security", "aidefence",
        ],
        "default_skill": "qa",
        "default_agents": ["qa-engineer", "tdd-guide", "security-architect", "security-auditor"],
    },
    "knowledge_wiki": {
        "signals": [
            "document", "wiki", "learn", "memory", "knowledge", "search",
            "context", "article", "writing", "content", "compile", "ingestion",
            "vault", "README", "CHANGELOG", "notes", "summarize",
            "ruflo memory", "memory store",
            "graphify", "knowledge graph", "graph query", "component graph",
        ],
        "default_skill": "learn",
        "default_agents": [],
    },
    "general": {
        "signals": [],
        "default_skill": "adaptive-imagining-cat",
        "default_agents": ["adaptive-coordinator"],
    },
}

# Regex patterns for fast multi-keyword matching
_COMPILED_PATTERNS: dict[str, list[re.Pattern]] = {}


def _get_compiled_patterns(cluster_name: str) -> list[re.Pattern]:
    """Get compiled regex patterns for a cluster's trigger signals."""
    if cluster_name not in _COMPILED_PATTERNS:
        signals = TRIGGER_PATTERNS.get(cluster_name, {}).get("signals", [])
        patterns = [re.compile(re.escape(s), re.IGNORECASE) for s in signals]
        _COMPILED_PATTERNS[cluster_name] = patterns
    return _COMPILED_PATTERNS[cluster_name]


class AutoTrigger:
    """Signal-based cluster activation engine.

    Detects trigger keywords in task descriptions and activates
    the appropriate domain cluster(s).
    """

    def __init__(self):
        self.patterns_cache = _COMPILED_PATTERNS

    def detect_signal(self, task: str) -> list[dict]:
        """Detect which clusters should activate based on task signals.

        Args:
            task: Task description to analyze.

        Returns:
            List of dicts with keys:
                - cluster: Cluster name
                - affinity: Confidence score (0.0 - 1.0)
                - matched_signals: List of matched trigger keywords
                - default_skill: Default skill for this cluster
                - default_agents: Default agents for this cluster
        """
        results = []
        task_lower = task.lower()

        for cluster_name in CLUSTER_ORDER:
            if cluster_name == "general":
                continue

            config = TRIGGER_PATTERNS.get(cluster_name, {})
            signals = config.get("signals", [])
            if not signals:
                continue

            # Count matched signals
            matched = []
            for signal in signals:
                if signal.lower() in task_lower:
                    matched.append(signal)

            if not matched:
                continue

            # Compute affinity: more matches = higher confidence
            # Normalize: 1 match = 0.5, 2 = 0.65, 3+ = 0.75-0.85
            base_affinity = min(0.5 + len(matched) * 0.15, 0.85)

            results.append({
                "cluster": cluster_name,
                "affinity": base_affinity,
                "matched_signals": matched,
                "default_skill": config.get("default_skill", "adaptive-imagining-cat"),
                "default_agents": config.get("default_agents", []),
            })

        # Sort by affinity descending
        results.sort(key=lambda x: -x["affinity"])

        # If no cluster activates, return general
        if not results:
            results.append({
                "cluster": "general",
                "affinity": 0.0,
                "matched_signals": [],
                "default_skill": "adaptive-imagining-cat",
                "default_agents": [],
            })

        return results

    def activate_clusters(self, clusters: list[str]) -> dict:
        """Activate specific clusters and return their default resources.

        Args:
            clusters: List of cluster names to activate.

        Returns:
            Dict with keys:
                - clusters: List of activated cluster configs
                - default_skills: Set of default skills to invoke
                - default_agents: Set of default agents to activate
        """
        activated_clusters = []
        default_skills = set()
        default_agents = set()

        for cluster_name in clusters:
            config = CLUSTER_DEFINITIONS.get(cluster_name)
            if not config:
                logger.warning(f"Unknown cluster: {cluster_name}")
                continue

            activated_clusters.append({
                "name": cluster_name,
                "display_name": config["display_name"],
                "description": config["description"],
                "default_skill": config["default_skill"],
                "default_agents": config.get("default_agents", []),
            })

            default_skills.add(config["default_skill"])
            default_agents.update(config.get("default_agents", []))

        return {
            "clusters": activated_clusters,
            "default_skills": list(default_skills),
            "default_agents": list(default_agents),
        }

    def route_task(self, task: str, max_clusters: int = 2) -> dict:
        """Full routing pipeline: detect signals, activate clusters, return defaults.

        This is the main entry point for auto-triggering. It:
        1. Detects which clusters should activate based on task signals
        2. Limits to max_clusters activations
        3. Returns cluster configs with default skills and agents

        Args:
            task: Task description to route.
            max_clusters: Maximum number of clusters to activate.

        Returns:
            Dict with cluster activations, default skills, and default agents.
        """
        # Step 1: Detect signals
        detected = self.detect_signal(task)

        # Step 2: Limit to max_clusters
        top_clusters = detected[:max_clusters]
        cluster_names = [d["cluster"] for d in top_clusters]

        # Step 3: Activate clusters
        activation = self.activate_clusters(cluster_names)

        # Merge detected affinity data into activation
        for i, cluster in enumerate(activation["clusters"]):
            if i < len(top_clusters):
                cluster["affinity"] = top_clusters[i]["affinity"]
                cluster["matched_signals"] = top_clusters[i]["matched_signals"]

        return activation


def detect_signal(task: str) -> list[dict]:
    """Module-level compatibility wrapper used by tests and older callers."""
    return AutoTrigger().detect_signal(task)
