"""AI Engineering Coach — NEXUS Integration Module

This module bridges the AI-Engineering-Coach VS Code extension with NEXUS's
routing and learning pipeline. It enables:

1. Practice scores → NEXUS Thompson Sampling beliefs
2. Anti-pattern detections → NEXUS reflection pipeline
3. Session analytics → NEXUS MCP tools
4. Conversational coaching via NEXUS LLM providers

The Coach cache JSON (written by the extension to disk) is the bridge format.
All code gracefully degrades if the extension is not installed or has no data.
"""

from server.coach.bridge import (
    CoachBridge,
    find_coach_cache,
    get_bridge,
)
from server.coach.schema import ensure_coach_schema, DBTables

__all__ = [
    "CoachBridge",
    "find_coach_cache",
    "get_bridge",
    "ensure_coach_schema",
    "DBTables",
]