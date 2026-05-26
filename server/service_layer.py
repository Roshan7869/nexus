"""Canonical service-layer bundles for cluster routing.

The database is allowed to drift as resources are ingested from multiple
systems. This module defines a small set of canonical overrides that keep
cluster defaults stable for the live TUI service layer.
"""

from __future__ import annotations

from typing import Dict, List


# Canonical cluster overrides for resources that must participate in a
# specific service bundle even if the classifier or ingestion pipeline
# assigned them elsewhere.
RESOURCE_CLUSTER_OVERRIDES: Dict[str, str] = {
    "adaptive-imagining-cat": "analyzer_planner",
    "researcher": "analyzer_planner",
    "code-reviewer": "analyzer_planner",
    "autopilot": "architect",
    "architect": "architect",
    "specification": "architect",
    "frontend-ui-engineering": "frontend_ui",
    "design-html": "frontend_ui",
    "designer": "frontend_ui",
    "ds-figma": "frontend_ui",
    "backend-patterns": "backend_api",
    "database-admin": "backend_api",
    "ship": "devops_infra",
    "qa": "quality_security",
    "qa-engineer": "quality_security",
    "tdd-guide": "quality_security",
    "learn": "knowledge_wiki",
}


SERVICE_BUNDLES: Dict[str, List[str]] = {
    "analyzer_planner": [
        "adaptive-imagining-cat",
        "researcher",
        "code-reviewer",
    ],
    "architect": [
        "autopilot",
        "architect",
        "specification",
    ],
    "frontend_ui": [
        "frontend-ui-engineering",
        "design-html",
        "ds-figma",
        "designer",
    ],
    "backend_api": [
        "backend-patterns",
        "database-admin",
    ],
    "devops_infra": [
        "ship",
    ],
    "quality_security": [
        "qa",
        "qa-engineer",
        "tdd-guide",
    ],
    "knowledge_wiki": [
        "learn",
    ],
    "general": [
        "adaptive-imagining-cat",
    ],
}


def canonical_cluster_for_resource(name: str, fallback: str | None = None) -> str | None:
    return RESOURCE_CLUSTER_OVERRIDES.get(name, fallback)


def bundle_for_cluster(cluster: str | None) -> List[str]:
    if not cluster:
        return []
    return list(SERVICE_BUNDLES.get(cluster, []))
