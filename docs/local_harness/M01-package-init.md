# M1 — `server/local_harness/__init__.py` (CREATE, package root)

## GitNexus queries to run (MCP available) / equivalents used here
```
context({name: "ToolSessionBroker"})   # 360° callers/callees — broker is the wrap target
query({search_query: "ephemeral tool session lifecycle"})  # processes: open_session → start_session → report/end
READ gitnexus://repo/nexus/cluster/<session-or-routing-cluster>
```
Equivalents used (MCP gitnexus not exposed in this harness): `read server/tool_session_broker.py:102-221`, `read server/execution_plane.py:166-280`, `repo_map` (ToolSessionBroker, open/report_session hubs). Index stale (2026-08-18, KuzuDB v43 skew) — re-run `impact` before edit.

## Current state
No `server/local_harness/` package exists. Session logic lives in `server/tool_session_broker.py` + `server/execution_plane.py`, tools registered in `server/nexus-mcp-enhanced.py`. No Cordis-style context type.

## Blast radius: NONE (new package, no dependents)
No `impact()` needed. Risk: import cycles — must import broker/execution_plane lazily inside functions, never at module top.

## Pseudocode
```python
# server/local_harness/__init__.py
"""Local harness — Cordis-style sessions for IDEs (OpenCode-first)."""
from server.local_harness.context import NexusContext, CoeffectSpec, Inverse
from server.local_harness.session import LocalHarnessSession
from server.local_harness.loader import EntryTree, reconcile

__all__ = ["NexusContext", "CoeffectSpec", "Inverse",
           "LocalHarnessSession", "EntryTree", "reconcile"]
# NOTE: no imports from server.tool_session_broker / execution_plane here (lazy inside session.py/loader.py)
```

## Build steps
1. `mkdir -p server/local_harness`; write this file exactly.
2. `python3 -c "import server.local_harness; print(server.local_harness.__all__)"` → must succeed with no DB touch.
3. Leave M2–M4 stubs failing until their docs land (TDD: package imports, submodules pending).

## Tests
`tests/test_local_harness.py::test_package_imports` — import only, no fixtures.
