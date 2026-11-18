# M7 — `server/tool_session_broker.py` (MODIFY, effect-tracked sessions)

## GitNexus queries
```
context({name: "ToolSessionBroker"})  # :102 start/end/get/_route/_select_tools/_persist
impact({target: "start_session", direction: "upstream"})  # execution_plane + MCP callers
impact({target: "end_session", direction: "upstream"})
```
Used: `read tool_session_broker.py:1-102` (ToolSession dataclass, MAX_SESSION_TOOLS=8, `ensure_tool_session_schema`, `to_dict` visible_tools merge) + `:102-221` (start: route→cluster/task_type→≤8 tools→harness≤16→uuid16→persist→ide link; end: normalize→archive→record_learning→persist→pop→ide outcome).

## Current state
No `ctx` param; `end_session` pops `_active` without running disposers.

## Blast radius: HIGH — fan-in from execution_plane + MCP
Rules: additive kwarg only (`ctx=None`); when None, byte-identical behavior. `dispose()` runs AFTER `_record_learning`, BEFORE `_active.pop`. Never let a disposer exception break learning/persist.

## Pseudocode
```python
def start_session(self, project, query, *, ide="claude-code", use_embedding=False,
                  max_tools=MAX_SESSION_TOOLS, ide_session_id=None,
                  ctx=None):   # NEW
    ...existing route/select/persist...
    if ctx is not None:
        # register inverses for handles acquired in this path (Cordis temporal)
        ctx.effect(lambda: self._inverse_for_persisted(session.session_id))
        # declare coeffects implied by cluster (spatial hint; enforced in M9)
        for k in keys_for_cluster(cluster): ctx.require(k)
    return session

def end_session(self, session_id, outcome="correct", summary="", ide_session_id=None, *,
                record_learning=True, ctx=None):  # NEW
    ...existing resolve/archive/learn/persist...
    if ctx is not None:
        try: ctx.dispose()      # LIFO, armed/at-most-once, swallow+log per-inverse errors
        except Exception: logger.exception(...)
    self._active.pop(session_id, None)
    ...
```

## Build steps
1. Add kwargs; guard all `ctx` uses with `is not None`.
2. Chaos test via M3 session wrapper: 100 starts with forced mid-failures → `_active` empty, no dangling rows.
3. `detect_changes({scope:"compare",base_ref:"main"})` before commit.

## Tests
`tests/test_tool_session_broker.py` += `test_ctx_none_identical`, `test_ctx_dispose_runs`, `test_disposer_error_does_not_break_learn`.
