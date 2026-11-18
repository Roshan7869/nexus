# M5 — `server/nexus-mcp-enhanced.py` (MODIFY, register local-harness tools)

## GitNexus queries
```
context({name: "task_start"})      # lines 686-714: broker.start_session + mcp_proxy payload
context({name: "task_complete"})   # lines 718-742: report_session preferred, broker fallback
impact({target: "task_start", direction: "upstream"})       # IDE callers — must not break
impact({target: "initialize_runtime", direction: "upstream"})
```
Used: `read nexus-mcp-enhanced.py:670-789` (report/task_start/task_complete/session_open/report/dashboard tools, `@mcp.tool()` pattern, `initialize_runtime()` preamble, `json.dumps` return).

## Current state
Tools registered via `@mcp.tool()` decorators; `task_start` returns `{..., mcp_proxy, instructions}`; `task_complete` prefers `execution_plane.report_session`. No local-harness tools.

## Blast radius: MEDIUM — additive only, no existing signature changes
Rules: append new `@mcp.tool()` fns at end near harness_dashboard; lazy-import `server.local_harness` inside each fn; never change `task_start/task_complete` bodies in this step (M6/M7 do narrowly-scoped edits separately).

## Pseudocode
```python
@mcp.tool()
def local_harness_open(task: str, project: str = "default", budget: int = 8, ide: str = "opencode") -> str:
    initialize_runtime()
    if os.environ.get("NEXUS_LOCAL_HARNESS", "1") != "1":
        # flag-off: delegate to existing task_start (compat)
        return task_start(query=task, project=project, ide=ide, max_tools=budget)
    from server.local_harness.session import open_local_session  # lazy
    lh = open_local_session(task, project, budget, ide)
    payload = {**lh.inner.to_dict(), "session_kind": "local_harness",
               "realm": lh.ctx.realm, "instructions": "Use only visible_tools. Call local_harness_report when finished."}
    return json.dumps(payload, indent=2)

@mcp.tool()
def local_harness_report(session_id: str, outcome: str = "correct", notes: str = "", quality: float = 1.0, skill_name: str = "") -> str:
    initialize_runtime()
    from server.local_harness.session import dispose_by_id  # looks up + dispose() + broker end/report
    return json.dumps(dispose_by_id(session_id, outcome, notes, quality, skill_name), indent=2)

@mcp.tool()
def local_harness_reconcile(desired_json: str) -> str:
    initialize_runtime()
    from server.local_harness.loader import EntryTree, reconcile
    ... # parse, diff vs current snapshot, apply via callbacks, return report

@mcp.tool()
def local_harness_status() -> str:
    initialize_runtime()
    ... # {flag, realm_count, active_sessions, redis: on/off, router_mode}
```

## Build steps
1. Append the four tools; no edits above line 789.
2. `NEXUS_LOCAL_HARNESS=0` → `local_harness_open` behaves exactly like `task_start` (compat test).
3. Stdio smoke: spawn MCP, list tools, open→report round-trip as `ide=opencode`.

## Tests
Extend `tests/test_tool_session_broker.py` pattern: `test_local_open_delegates_when_flag_off`, `test_local_open_report_roundtrip`.
