# M8 — `server/opencode_adapter.py` (CREATE, replace lost connector)

## GitNexus queries
```
query({search_query: "opencode connector session payload"})
glob **/*connector*.py   # only venv + pyc remains
```
Used: `glob` (no source connectors in repo), `read opencode-with-nexus.sh` (44 lines: starts `uvicorn server.unified:app` :8000 bg, launches `opencode "$@"`, trap cleanup), `read .mcp.json` (nexus stdio entry, env passthrough), `read execution_plane.py:254-280` outcome normalizer to reuse.

## Current state
`__pycache__/opencode_connector.cpython-312.pyc` exists but **source is gone** — any `import opencode_connector` is broken. Shell wrapper works (HTTP + TUI co-launch) but has no session-payload mapping.

## Blast radius: NONE (new module; shell untouched until M17)

## Pseudocode
```python
"""OpenCode adapter — session payload + outcome mapping for the local harness."""
IDE_NAME = "opencode"

def get_opencode_config() -> dict:
    # mirrors .mcp.json nexus env + opencode-with-nexus.sh defaults
    return {"transport": "stdio",
            "command": ["venv/bin/python3", "server/nexus-mcp-enhanced.py"],
            "env": {"USE_UNIFIED_ROUTER": "2", "NEXUS_IDE": IDE_NAME,
                    "NEXUS_LOCAL_HARNESS": "1"},
            "http": {"url": "http://127.0.0.1:8000", "optional": True}}

def session_payload(tool_session_dict: dict) -> dict:
    # minimal OpenCode-facing projection; keys stable for IDE code
    return {"session_id": tool_session_dict["session_id"],
            "tools": tool_session_dict.get("visible_tools", []),
            "context": tool_session_dict.get("context_block", ""),
            "instructions": "Use only listed tools. Report via local_harness_report."}

def report_mapping(outcome: str) -> str:
    # REUSE execution_plane normalizer semantics (do not fork):
    # success/passed/done/complete(d)->correct; failed/fail/error->wrong; mixed->partial
    ...
```

## Build steps
1. Create module with zero `server.*` top-level imports (lazy where needed).
2. `python3 -c "from server.opencode_adapter import get_opencode_config"` smoke.
3. Audit other pyc-only modules: `find . -name "*.pyc" | while read f; do src=${f/__pycache__\//}; src=${src%.cpython-*.pyc}.py; [ -f $src ] || echo "ORPHAN $f"; done`.

## Tests
`test_opencode_adapter_projection`, `test_report_mapping_matches_execution_plane`.
