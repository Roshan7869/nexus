# M6 — `server/execution_plane.py` (MODIFY, OpenCode passthrough)

## GitNexus queries
```
context({name: "open_session"})     # lines 166-251: route→broker→payload contract
context({name: "_sanitize_ide"})    # lines 45-60: allowlist + aliases
impact({target: "open_session", direction: "upstream"})   # HTTP/MCP/CLI wrappers
```
Used: `read execution_plane.py:1-160` (contract v1, `_sanitize_ide`, belief snapshot/delta, `health_v1`) + `:160-280` (open/report). **Correction to parent plan: `opencode` is ALREADY in the allowlist** (line 46) with aliases `codenomad/open-code → opencode`. No allowlist fix needed.

## Current state
`open_session(task, project, budget≤12, ide, use_embedding, ide_session_id)` → `{contract, session_id, tools, skills, model_hint, cluster, harness_*, context_block, mcp_proxy, instructions}`. `report_session` normalizes success/passed/done→correct, failed/fail/error→wrong, mixed→partial.

## Blast radius: MEDIUM — narrow, additive
Change: accept optional prebuilt local-harness session; default None = current behavior. No payload key removals.

## Pseudocode
```python
def open_session(task, project="default", *, budget=8, ide="http",
                 use_embedding=True, ide_session_id=None,
                 local_session=None):   # NEW optional, default None
    ...validate/clamp as today...
    route = route_for_task(...)          # unchanged
    if local_session is None:
        broker = get_tool_session_broker()
        session = broker.start_session(...)   # unchanged path
    else:
        session = local_session.inner    # M3 wrapper; broker already started it
        # re-apply cutover skill order as today (lines 203-214)
    ...payload identical...
```

## Build steps
1. Add `local_session=None` kwarg only; all existing callers unaffected.
2. Add contract test: `open_session(..., ide="opencode")` keeps `ide=="opencode"` (no clamp to unknown).
3. Document `budget` clamp 1–12 preserved for local harness (M5 passes budget through).

## Tests
`tests/test_execution_plane_p4.py` += `test_open_ide_opencode_preserved`, `test_open_local_session_passthrough`.
