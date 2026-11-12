# C2 — Real execution path with side-effect gating and approval

Source: plan §C2 · Commit: `C2: real tool dispatcher + verifier-driven outcomes + approve path` · Depends on: C1, A3 · Highest-risk change in the plan (plan risk matrix: HIGH)

## Problem (verified)

- `execute_task_run(task_run_id: str)` at `server/broker_runtime.py:671` takes **no** `allow_writes` argument. The plan's signature change is needed.
- Outcome is hardcoded `"correct"` at `broker_runtime.py:1080` (fixed by A3 first).
- No `_invoke_tool` exists. No `approve_and_execute` exists.
- `_emit_outcome_for_trace(trace_name, trace_phase, outcome, project, ide)` exists at `broker_runtime.py:648` ✅.
- `tool_traces` columns verified: `execution_mode`, `side_effect_level`, `resource_type`, `resource_name`, `status`, `outcome`, `notes` — **but the plan's `_invoke_tool` reads `trace["execution_mode"]` etc. from a `trace` row; confirm the SELECT in `execute_task_run` returns them.**

## Corrections to source plan

| Plan says | Reality |
|---|---|
| `execute_task_run` pseudocode uses `phase`, `selected`, `run`, `cwd`, `ide`, `conn` | These are **not** bound in the snippet. It is a sketch. Bind them from the current body of `execute_task_run` (line 671+) before editing. |
| `_invoke_tool` returns `{"status": "ready", "command": ["echo", …]}` for commands | This "dispatch" does not run the tool. It echoes. Decide what "real" means (see open decisions) before implementing. |
| `resource_type` values `doc, spec, design_system, craft, skill, agent, command, cli_tool` | Not verified against the `resources.resource_type` values in `db/nexus.db`. Query first. |
| `approve_and_execute` checks `status == "previewed"` | Verify the status string C1 produces. |
| Endpoint for approve | Plan has none. Add `POST /nexus/task-runs/{id}/approve` in `unified.py` (existing `task-runs` routes live there). |

## Pseudocode (corrected, still a sketch)

```python
# server/broker_runtime.py
BLOCKED_SIDE_EFFECTS = {"external_write", "operational"}

def _invoke_tool(trace, *, allow_writes: bool) -> dict:
    side = trace["side_effect_level"]
    if side in BLOCKED_SIDE_EFFECTS and not allow_writes:
        return {"status": "blocked", "reason": "requires_approval", "side_effect": side}
    rtype = trace["resource_type"]
    name = trace["resource_name"]
    if rtype in {"doc", "spec", "design_system", "craft"}:
        return {"status": "retrieved", "resource": name}
    if rtype in {"skill", "agent"}:
        return {"status": "delegated", "resource": name}
    # command / cli_tool: NOT executed in this change — see open decisions
    return {"status": "not_executed", "resource": name, "reason": "dispatch_not_implemented"}
```

```python
# server/broker_runtime.py — execute_task_run(task_run_id, allow_writes=False)
def execute_task_run(task_run_id: str, allow_writes: bool = False) -> Dict[str, Any]:
    ...existing setup, bind run/conn/cwd/project/ide...
    for phase in phases:
        selected = _select_phase_tools(phase, ...)          # existing
        results = [_invoke_tool(row, allow_writes=allow_writes) for row in selected]
        verdict = verify(phase.get("stage", "general"), project_path=cwd)   # A3
        outcome = verdict["outcome"]
        for row, inv in zip(selected, results):
            conn.execute("UPDATE tool_traces SET status='executed', outcome=?, notes=? WHERE id=?",
                         (outcome, json.dumps({"invoke": inv, "verdict": verdict}, default=str)[:500], row["id"]))
            _emit_outcome_for_trace(row["resource_name"], f"{phase['id']}: {phase['name']}",
                                    outcome, project, ide)
    ...
```

```python
# server/broker_runtime.py
def approve_and_execute(task_run_id: str) -> Dict[str, Any]:
    run = _get_run(task_run_id)
    if not run or run["status"] != "previewed":
        return {"status": "error", "message": "task_run not in previewed state"}
    return execute_task_run(task_run_id, allow_writes=True)
```

```python
# server/unified.py
@app.post("/nexus/task-runs/{task_run_id}/approve")
async def approve(task_run_id: str):
    return JSONResponse(await _run_sync(approve_and_execute, task_run_id))
```

## Steps

1. Do **not** start until A3 is merged and the verifier is real.
2. Read `execute_task_run` (671–~800) fully. Write the bound variable names into this file.
3. Query `resources.resource_type` distinct values and `tool_traces.side_effect_level` distinct values. Update the tables in `_invoke_tool` to match real values.
4. Implement `_invoke_tool` with **no command execution**. Return `not_executed` for commands. This keeps C2 honest: the plan's `echo` stub would fake execution, the same problem as A3.
5. Add `allow_writes` and `approve_and_execute`. Add the approve endpoint.
6. Tests.

## Tests

New `tests/test_dispatch_gate.py`:

```python
import pytest
from server.broker_runtime import _invoke_tool

def row(side, rtype, name="x"):
    return {"side_effect_level": side, "resource_type": rtype, "resource_name": name}

def test_write_blocked_without_approval():
    assert _invoke_tool(row("external_write", "command"), allow_writes=False)["status"] == "blocked"

def test_write_allowed_after_approval():
    assert _invoke_tool(row("external_write", "command"), allow_writes=True)["status"] != "blocked"

def test_read_doc_is_retrieved():
    assert _invoke_tool(row("read", "doc"), allow_writes=False)["status"] == "retrieved"

def test_command_never_reports_executed():
    assert _invoke_tool(row("read", "command"), allow_writes=True)["status"] == "not_executed"
```

Approve path test: create a `previewed` run in the in-memory DB, call `approve_and_execute`, assert status changed and `tool_traces.outcome` came from the verifier (monkeypatch `verify` to return a fixed verdict).

Benchmark: golden eval is unaffected (routing only). Ops gate `terminal_close_rate` may change — record it.

## Verification (live)

```bash
RUN=$(curl -s -X POST http://127.0.0.1:8000/nexus/autopilot/run -H "Content-Type: application/json" \
  -d '{"task":"write a test","project":"nexus","ide_name":"hermes"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['task_run_id'])")
curl -s -X POST http://127.0.0.1:8000/nexus/task-runs/$RUN/approve | python3 -m json.tool | head -20
```

Confirm the `task_run_id` key name in the response before running this.

## Rollback

Set `allow_writes` default to False and remove the approve route. Preview path from C1 keeps working.

## Open decisions

- **What does "real execution" mean for commands?** Options: (a) return `not_executed` and let the IDE run commands (recommended for now — NEXUS is advisory); (b) run allow-listed read-only commands via `subprocess` with a timeout; (c) defer. Pick one before implementing. Option (b) is the first real-execution risk in the project.
- Who may approve? Currently any HTTP caller. Recommend: local-only bind (`127.0.0.1`) until auth exists.
