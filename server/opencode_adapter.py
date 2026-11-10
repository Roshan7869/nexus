"""OpenCode adapter — session payload + outcome mapping for the local harness.

Replaces the lost pyc-only `opencode_connector` module. Outcome semantics are
NOT forked: report_mapping reuses ToolSessionBroker._normalize_outcome.
"""
from __future__ import annotations

IDE_NAME = "opencode"


def get_opencode_config() -> dict:
    """Stdio spawn config mirroring .mcp.json nexus env + opencode-with-nexus.sh."""
    return {"transport": "stdio",
            "command": ["venv/bin/python3", "server/nexus-mcp-enhanced.py"],
            "env": {"USE_UNIFIED_ROUTER": "2", "NEXUS_IDE": IDE_NAME,
                    "NEXUS_LOCAL_HARNESS": "1"},
            "http": {"url": "http://127.0.0.1:8000", "optional": True}}


def session_payload(tool_session_dict: dict) -> dict:
    """Minimal OpenCode-facing projection; keys stable for IDE code."""
    return {"session_id": tool_session_dict["session_id"],
            "tools": tool_session_dict.get("visible_tools", []),
            "context": tool_session_dict.get("context_block", ""),
            "instructions": "Use only listed tools. Report via local_harness_report."}


def report_mapping(outcome: str) -> str:
    """Normalize an OpenCode outcome via the single broker normalizer (no fork)."""
    from server.tool_session_broker import ToolSessionBroker  # lazy: no DB at import
    return ToolSessionBroker._normalize_outcome(outcome)
