# NEXUS Remediation Execution Plan v4 — File by File

> Target repository: `/home/roshan/nexus`
> Branch: `second-brain-v2`
> Generated from: user first-principles report, `NEXUS_SECOND_BRAIN_PLAN_v2.md`, `NEXUS_SECOND_BRAIN_PLAN_v3.md`, `audit/ROOT-CAUSE-ANALYSIS.md`, `audit/FINAL-AUDIT-REPORT.md`, and live code reads of `server/router.py`, `server/router_v2/v5.py`, `server/router_cutover.py`, `server/learning.py`, `server/broker_runtime.py`, `server/unified.py`, `server/nexus_http_daemon.py`, `server/event_bridge.py`, `server/ide_manager.py`, `core/config.py`, `supervisord.conf`.

## 0. Design Brief (non-negotiable)

NEXUS is an **operator assistant for a simple TUI IDE**. Its job is to reduce cognitive load when the user works across many real projects:

1. Pick the right skill/tool.
2. Pull the right context.
3. Run the right workflow.
4. Learn from what actually worked.
5. Get better over time — without the user manually orchestrating 2,800+ resources.

### Success Criteria (C1-C5)

| ID | Criterion | Current State | Target State |
|----|-----------|---------------|--------------|
| C1 | Correct routing | 3 routers (`router.py` v4, `unified.py` v2, `router_v2/v5.py` v5) plus `find_skills` keyword path | One router path used by HTTP, MCP, TUI, and autopilot |
| C2 | Closed feedback loop | 34.8% outcome coverage; non-terminal `routed` leaks into counters; `event_bridge.py` only updates Thompson when `NEXUS_THOMPSON_ROUTING=true` | >80% coverage; one `record_outcome` API; non-terminal outcomes never update beliefs |
| C3 | Project isolation | Project defaults to `"default"`; ranker does not filter by project | Every route/session scoped to a real project; memory injected per project |
| C4 | Autonomous execution | Autopilot returns `execution_preview` only; `auto_execute` hardcoded to `correct` | Honest preview → explicit user approve → real tool execution → real outcome |
| C5 | Observable truth | Dashboard metrics; `nexus_stats` exposes feature flags but no ground-truth verification | Verify registry maps each task type to a real command/test that can be run |

### Root-Cause → File Map

| RC | Root Cause | Primary Files | Fix Summary |
|----|------------|---------------|-------------|
| RC-1 | Multiple independent operating loops | `server/router.py`, `server/router_cutover.py`, `server/router_v2/v5.py`, `server/unified.py`, `server/nexus_http_daemon.py`, `server/nexus-mcp-enhanced.py` | Delete/collapse router variants; make `route_for_task` the single entry point |
| RC-2 | Project scope ignored | `server/router.py`, `server/router_v2/v5.py`, `core/session_builder.py`*, `server/unified.py` | Add `project` filter to ranker; inject project memory; reject `"default"` in production |
| RC-3 | Feedback loop not closed | `server/learning.py`, `server/broker_runtime.py`, `server/event_bridge.py` | One `record_outcome`; non-terminal outcomes only update `routing_log`; terminal outcomes update counters/Thompson/edges |
| RC-4 | Fake verification | `server/router.py:_evaluate_outcome`, `server/broker_runtime.py:execute_task_run` | Replace eval-dataset fake verification with `verify_registry.py`; every task type has a real verifier |
| RC-5 | Dishonest autopilot | `server/broker_runtime.py`, `server/ide_manager.py` | Default `auto_execute=False`; real execution via tool wrappers; outcomes come from verifier, not hardcoded |
| RC-6 | Project memory not injected | `core/session_builder.py`*, `server/unified.py`, `server/learning.py` | Load `user_learnings` + project KG + harness context into every session prompt |
| RC-7 | Harness not in hot path | `server/router_cutover.py`, `server/router_v2/v5.py`, `server/harness_engine/attach.py` | Default `NEXUS_HARNESS_EAGER=true`; harness pre-fetch before route; harness state as ranking signal |
| RC-8 | Investment in wrong surface | All `/nexus/*` dashboard endpoints in `server/nexus_http_daemon.py` | Deprecate dashboard-only endpoints; invest in TUI IDE bridge and autopilot execution |
| RC-9 | Config/process drift | `supervisord.conf`, `server/nexus-mcp-enhanced.py`, `server/nexus_http_daemon.py` | One supervised process topology; one MCP server; HTTP uses in-process bridge; boot sanity check |

*`*` = `core/session_builder.py` referenced in v3 plan; if it does not exist, create it or use `server/session_context.py`.

---

## Phase A: Stabilize Foundation (RC-1, RC-4, RC-8 partial)

Goal: One router, one outcome API, no fake verification. Stop building dashboards until the core loop works.

### A1. Make `route_for_task` the single production router

**Files touched:** `server/router_cutover.py`, `server/nexus_http_daemon.py`, `server/nexus-mcp-enhanced.py`, `server/router.py`

**Why:** `server/nexus_http_daemon.py` already has `/nexus/route` calling `route_for_task`, but `/nexus/search` calls `find_matching_skills` directly and `/nexus/autopilot/run` calls `route_task` directly in `broker_runtime.py`. The MCP server likely also has a direct `find_skills` tool. This creates divergent rankings.

**Task A1.1 — Audit every direct call to `route_task` / `find_matching_skills` outside `router_cutover.py`.**

Command:
```bash
cd /home/roshan/nexus
rg -n "route_task\(" server/ core/ --type py | grep -v "router_cutover.py" | grep -v "router.py"
rg -n "find_matching_skills\(" server/ core/ --type py | grep -v "router_cutover.py"
```

Expected output: list of call sites. Each one must be replaced by `route_for_task` or deleted.

**Task A1.2 — Replace `broker_runtime.py:_select_phase_tools` direct `route_task` call with `route_for_task`.**

Current code (`server/broker_runtime.py:475`):
```python
routed = route_task(phase["description"], project, use_embedding=True)
```

Replace with:
```python
from server.router_cutover import route_for_task
routed = route_for_task(
    phase["description"],
    project=project,
    max_results=8,
    open_session=False,  # broker opens its own task_run, not routing_log
)
```

**Task A1.3 — Replace `/nexus/search` keyword-only path with unified route.**

File: `server/nexus_http_daemon.py:352-367`

Current:
```python
@app.post("/nexus/search")
async def nexus_search(request: Request):
    body = await request.json()
    query = body.get("query", body.get("task", ""))
    project = body.get("project", "default")
    max_results = body.get("max_results", 10)
    if not query:
        raise HTTPException(400, "Missing 'query' or 'task' field")
    if not find_matching_skills:
        raise HTTPException(500, "Router not loaded")
    skills = find_matching_skills(query, project)
    return JSONResponse({"query": query, "method": "keyword_match", "results": skills[:max_results]})
```

Replace with:
```python
@app.post("/nexus/search")
async def nexus_search(request: Request):
    body = await request.json()
    query = body.get("query", body.get("task", ""))
    project = body.get("project", "default")
    max_results = body.get("max_results", 10)
    if not query:
        raise HTTPException(400, "Missing 'query' or 'task' field")
    from server.router_cutover import route_for_task
    result = route_for_task(query, project=project, max_results=max_results, open_session=False)
    return JSONResponse(result)
```

**Task A1.4 — Collapse `server/router.py` v4 into `router_cutover.py` as fallback path.**

Keep `route_task` in `server/router.py` as the keyword fallback, but rename/collapse the public API so nothing imports it directly. The only public routing function is `route_for_task`.

Add to `server/router_cutover.py`:
```python
ROUTER_MODES = {
    "0": {"name": "v4_legacy_keyword", "path": "route_task", "production": False},
    "1": {"name": "unified_v2", "path": "unified_route", "production": False},
    "2": {"name": "unified_v5", "path": "unified_route_v5", "production": True},
}
```

Set default in `server/router_cutover.py:23`:
```python
DEFAULT_ROUTER_FLAG = "2"
```

Verification command:
```bash
curl -s -X POST http://127.0.0.1:8080/nexus/route \
  -H "Content-Type: application/json" \
  -d '{"task":"fix a login bug","project":"nexus","max_results":5}' | python3 -m json.tool | head -30
```

Expected: `method` field is `unified_router_v5` and `router_mode` is `unified_v5`.

**Commit:** `A1: single public router path via route_for_task`

---

### A2. Single `record_outcome` API

**Files touched:** `server/learning.py`, `server/broker_runtime.py`, `server/event_bridge.py`, `server/nexus-mcp-enhanced.py`

**Why:** `broker_runtime.py` has `_emit_outcome_for_trace` which calls `learning.record_outcome` but also writes its own `execution_log` and `tool_traces` with hardcoded outcomes. `event_bridge.py` has its own `record_outcome` wrapper around `NexusLearner`. There are at least three outcome paths.

**Task A2.1 — Delete `event_bridge.py`'s duplicate `record_outcome`.**

File: `server/event_bridge.py`

Remove functions:
- `record_outcome(outcome, route_result, ide)` at line 141
- `shutdown_bridge()` at line 146
- `get_learning_stats()` at line 153

Keep `EventBridge.outcome_to_event` for Thompson-only model-belief updates, but make it **only** update `NexusLearner` beliefs, never resource counters. Counters are owned by `learning.record_outcome`.

**Task A2.2 — Make `broker_runtime.py:review_task_run` use only `learning.record_outcome`.**

Search current implementation; it likely double-calls or uses an internal path. Replace with:
```python
from server.learning import record_outcome as log_outcome

def review_task_run(task_run_id: str, verdict: str, comment: str = "") -> Dict[str, Any]:
    initialize_runtime()
    conn = _get_conn()
    try:
        run = conn.execute("SELECT * FROM task_runs WHERE id=?", (task_run_id,)).fetchone()
        if not run:
            return {"status": "error", "message": f"task_run {task_run_id} not found"}
        traces = conn.execute(
            "SELECT resource_name, outcome FROM tool_traces WHERE task_run_id=? AND status='executed'",
            (task_run_id,),
        ).fetchall()
        project = run["project"]
        ide = normalize_legacy_ide(run["ide_name"])
        for row in traces:
            # One outcome per executed tool; verdict applies to all executed tools
            log_outcome(
                skill_name=row["resource_name"],
                outcome=verdict,
                task_summary=run["task_text"],
                project=project,
                ide=ide,
            )
        conn.execute(
            "INSERT INTO execution_reviews(task_run_id, verdict, comment) VALUES (?,?,?)",
            (task_run_id, verdict, comment),
        )
        conn.execute(
            "UPDATE task_runs SET review_status=?, updated_at=datetime('now') WHERE id=?",
            ("reviewed", task_run_id),
        )
        conn.commit()
        return {"status": "reviewed", "task_run_id": task_run_id, "verdict": verdict}
    finally:
        conn.close()
```

**Task A2.3 — Ensure non-terminal outcomes never touch counters/Thompson/edges.**

`server/learning.py:357-394` already does this. Add a guard that any caller passing `outcome in {'routed','pending',''}` must route through this early-return. Review all call sites:

```bash
rg -n "record_outcome\(" server/ core/ --type py
```

Expected: every call site uses the signature `(skill_name, outcome, task_summary, project, ide, ...)` and terminal outcomes only.

**Task A2.4 — Add outcome coverage test.**

New file: `tests/test_learning_loop.py`

```python
import sqlite3
from pathlib import Path
from server.learning import record_outcome
from server.db import get_db_path

def test_non_terminal_does_not_update_counters():
    db = get_db_path()
    conn = sqlite3.connect(str(db), timeout=10)
    conn.row_factory = sqlite3.Row
    before = conn.execute("SELECT times_triggered, times_correct FROM resources WHERE name='hermes-agent'").fetchone()
    record_outcome("hermes-agent", "routed", task_summary="test", project="nexus", ide="hermes")
    after = conn.execute("SELECT times_triggered, times_correct FROM resources WHERE name='hermes-agent'").fetchone()
    assert after["times_triggered"] == before["times_triggered"]
    assert after["times_correct"] == before["times_correct"]


def test_terminal_updates_counters():
    db = get_db_path()
    conn = sqlite3.connect(str(db), timeout=10)
    conn.row_factory = sqlite3.Row
    before = conn.execute("SELECT times_triggered, times_correct FROM resources WHERE name='hermes-agent'").fetchone()
    record_outcome("hermes-agent", "correct", task_summary="test", project="nexus", ide="hermes")
    after = conn.execute("SELECT times_triggered, times_correct FROM resources WHERE name='hermes-agent'").fetchone()
    assert after["times_triggered"] == before["times_triggered"] + 1
    assert after["times_correct"] == before["times_correct"] + 1
```

Command:
```bash
pytest tests/test_learning_loop.py -v
```

Expected: both tests pass.

**Commit:** `A2: single record_outcome API with non-terminal guard`

---

### A3. Replace fake verification with real verify registry

**Files touched:** `server/router.py:_evaluate_outcome`, `server/broker_runtime.py:execute_task_run`, new `server/verify_registry.py`

**Why:** `server/router.py:_evaluate_outcome` returns `correct` if the task text exists in a 56-entry eval JSON. This is not verification; it is a lookup table that teaches the system to lie. `broker_runtime.py:execute_task_run` hardcodes `outcome = "correct"` at line 692.

**Task A3.1 — Create `server/verify_registry.py`.**

```python
#!/usr/bin/env python3
"""Verify registry: map task types / clusters to real commands/tests.

Every entry must be a runnable command. Output is parsed for PASS/FAIL.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Dict, List, Callable

VERIFY_COMMANDS: Dict[str, List[str]] = {
    "coding": ["pytest", "-q", "--tb=short"],
    "devops": ["venv/bin/supervisorctl", "status"],
    "frontend": ["python3", "-m", "py_compile"],
    "quality_security": ["python3", "-m", "bandit", "-r", "server/", "-ll"],
    "general": ["python3", "-m", "compileall", "-q", "server/", "core/"],
}


def verify(task_type: str, project_path: str | None = None) -> Dict[str, str]:
    project_path = project_path or "."
    cmd = VERIFY_COMMANDS.get(task_type, VERIFY_COMMANDS["general"])
    try:
        result = subprocess.run(
            cmd,
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=120,
        )
        return {
            "verifier": task_type,
            "command": " ".join(cmd),
            "returncode": result.returncode,
            "stdout": result.stdout[:2000],
            "stderr": result.stderr[:2000],
            "outcome": "correct" if result.returncode == 0 else "wrong",
        }
    except Exception as e:
        return {"verifier": task_type, "command": " ".join(cmd), "outcome": "wrong", "error": str(e)}


def list_verifiers() -> List[str]:
    return list(VERIFY_COMMANDS.keys())
```

**Task A3.2 — Replace `server/router.py:_evaluate_outcome` to return `'routed'` when no ground truth.**

Current `server/router.py:389-411`:
```python
def _evaluate_outcome(task: str, returned_skills: list) -> str:
    task_lower = task.lower().strip()
    eval_data = _load_eval_dataset()
    if task_lower in eval_data:
        expected = eval_data[task_lower]
        if set(returned_skills) & set(expected):
            return 'correct'
        return 'wrong'
    return 'routed'
```

This is acceptable if used only for offline A/B. **Rename it** to `_evaluate_outcome_ab_only` and remove it from the production `route_task` return path. Production must never return `correct` from a static dataset.

In `route_task`, remove any line that injects `outcome = _evaluate_outcome(...)` into the result. Routing is not verification.

**Task A3.3 — Use `verify_registry.verify` in `broker_runtime.py:execute_task_run`.**

Replace hardcoded `outcome = "correct"` at line 692 with:
```python
from server.verify_registry import verify
verdict = verify(phase.get("stage", "general"), project_path=cwd)
outcome = verdict["outcome"]
```

Also store the verifier output in `tool_traces.notes`:
```python
conn.execute(
    "UPDATE tool_traces SET status='executed', outcome=?, notes=? WHERE id=?",
    (outcome, json.dumps(verdict, default=str)[:500], row["id"]),
)
```

Verification command:
```bash
python3 -c "from server.verify_registry import verify; print(verify('coding', '.'))"
```

Expected output: dict with `outcome` equal to `correct` or `wrong` based on actual pytest result.

**Commit:** `A3: real verify_registry replaces eval-dataset fake verification`

---

### A4. Deprecate dashboard-only surface

**Files touched:** `server/nexus_http_daemon.py`

**Why:** RC-8 says investment is in dashboards, not the operator surface. The following endpoints are read-only dashboards and can be moved behind an `ENABLE_DASHBOARD` flag or deleted:
- `/nexus/feedback/coverage`
- `/nexus/kg/status`
- `/nexus/router/status`
- `/nexus/ops/status`
- `/nexus/ops/processes`
- `/nexus/research/sources`
- `/nexus/research/synthesize`
- `/nexus/router/ab`
- `/nexus/kg/rebuild`

**Task A4.1 — Add `ENABLE_DASHBOARD` environment guard.**

Add near imports:
```python
ENABLE_DASHBOARD = os.environ.get("NEXUS_ENABLE_DASHBOARD", "false").lower() in ("1", "true", "yes")
```

Wrap each dashboard endpoint:
```python
@app.get("/nexus/feedback/coverage")
async def nexus_feedback_coverage():
    if not ENABLE_DASHBOARD:
        raise HTTPException(404, "dashboard disabled")
    ...
```

Keep these operator-facing endpoints always on:
- `/nexus/route`
- `/nexus/search`
- `/nexus/autopilot/run`
- `/nexus/task-runs/{id}`
- `/nexus/task-runs/{id}/review`
- `/nexus/learnings` (POST/GET)

**Task A4.2 — Update `nexus_stats` to report live loop health, not flag list.**

Replace `server/nexus_http_daemon.py:370-390` with:
```python
@app.get("/nexus/stats")
async def nexus_stats():
    from server.router_cutover import get_router_status
    from server.rl_feedback_loop import get_feedback_coverage
    router = get_router_status()
    coverage = get_feedback_coverage()
    return JSONResponse({
        "router_mode": router["mode"],
        "router_production": router["production_sot"],
        "outcome_coverage": coverage.get("coverage_ratio", 0.0),
        "terminal_outcomes": coverage.get("terminal_outcomes", 0),
        "non_terminal_outcomes": coverage.get("non_terminal_outcomes", 0),
        "dashboard_enabled": ENABLE_DASHBOARD,
    })
```

Verification:
```bash
curl -s http://127.0.0.1:8080/nexus/stats | python3 -m json.tool
```

Expected: JSON with real coverage numbers, not a list of feature flags.

**Commit:** `A4: dashboard endpoints gated; stats report loop health`

---

## Phase B: Project Isolation (RC-2, RC-6, RC-7 partial)

Goal: Every route and session is scoped to a real project. No `"default"` in production.

### B1. Add project filter to the ranker

**Files touched:** `server/router.py`, `server/router_v2/v5.py`

**Why:** `server/router.py:find_matching_skills` takes `project` but does not use it in SQL. `server/router_v2/v5.py` (read so far) does not show project filtering in ranking either.

**Task B1.1 — Add project-scoped resource table.**

New migration (run manually or add to `server/db.py`):
```sql
CREATE TABLE IF NOT EXISTS project_resources (
    project TEXT NOT NULL,
    resource_id INTEGER NOT NULL,
    priority INTEGER NOT NULL DEFAULT 50,
    PRIMARY KEY (project, resource_id)
);
CREATE INDEX IF NOT EXISTS idx_project_resources_project ON project_resources(project);
```

Command to apply:
```bash
sqlite3 db/nexus.db < server/migrations/002_project_resources.sql
```

**Task B1.2 — Modify `find_matching_skills` to join `project_resources`.**

File: `server/router.py`

After loading `filtered_ids` and before the keyword query, if `project != "default"`:
```python
project_resource_ids = set()
try:
    rows = conn.execute(
        "SELECT resource_id FROM project_resources WHERE project=?",
        (project,),
    ).fetchall()
    project_resource_ids = {r[0] for r in rows}
except Exception:
    pass

if project_resource_ids and filtered_ids:
    filtered_ids = [rid for rid in filtered_ids if rid in project_resource_ids]
elif project_resource_ids:
    filtered_ids = list(project_resource_ids)
```

**Task B1.3 — Modify `server/router_v2/v5.py:unified_route_v5` to accept and use `project`.**

Search for the function signature and add `project: str = "default"`. Add a scoring boost for resources in `project_resources`:
```python
project_boost = 0.0
if project != "default":
    project_rids = {r[0] for r in conn.execute(
        "SELECT resource_id FROM project_resources WHERE project=?", (project,)
    ).fetchall()}
    if candidate_id in project_rids:
        project_boost = 0.25
```

Add `project_boost` to the final score.

**Task B1.4 — Reject `"default"` project in production HTTP/MCP.**

File: `server/nexus_http_daemon.py`

Add a helper:
```python
def _require_project(body: dict) -> str:
    project = (body.get("project") or "default").strip()
    if project == "default":
        raise HTTPException(400, "project is required; 'default' is not allowed in production")
    return project
```

Use it in `/nexus/route`, `/nexus/search`, `/nexus/autopilot/run`.

Verification:
```bash
curl -s -X POST http://127.0.0.1:8080/nexus/route \
  -H "Content-Type: application/json" \
  -d '{"task":"fix bug","project":"default"}' | python3 -m json.tool
```

Expected: `{"detail": "project is required; 'default' is not allowed in production"}` with status 400.

**Commit:** `B1: project-scoped ranking; reject default project`

---

### B2. Project memory injection on every session

**Files touched:** `server/learning.py`, `server/broker_runtime.py`, `core/session_builder.py` (create if missing)

**Why:** RC-6. Project learnings exist in `user_learnings` table but are not automatically loaded into the router/session context.

**Task B2.1 — Add `get_project_context(project)` to `server/learning.py`.**

Add near other helpers:
```python
def get_project_context(project: str, limit: int = 10) -> dict:
    """Return project-scoped learnings and recent outcomes for prompt injection."""
    conn = _get_conn()
    try:
        learnings = conn.execute(
            "SELECT note, task_type, bundles_json, tools_json FROM user_learnings WHERE project=? ORDER BY created_at DESC LIMIT ?",
            (project, limit),
        ).fetchall()
        top_resources = conn.execute(
            "SELECT skill_name, COUNT(*) as n, SUM(CASE WHEN outcome='correct' THEN 1 ELSE 0 END) as ok "
            "FROM routing_log WHERE project=? AND outcome IN ('correct','wrong') "
            "GROUP BY skill_name ORDER BY ok DESC LIMIT ?",
            (project, limit),
        ).fetchall()
        return {
            "learnings": [
                {
                    "note": r["note"],
                    "task_type": r["task_type"],
                    "bundles": _parse_skill_list(r["bundles_json"]),
                    "tools": _parse_skill_list(r["tools_json"]),
                }
                for r in learnings
            ],
            "top_resources": [
                {"name": r["skill_name"], "uses": r["n"], "correct": r["ok"]}
                for r in top_resources
            ],
        }
    finally:
        if owns := True:
            conn.close()
```

**Task B2.2 — Inject project context into `route_for_task`.**

File: `server/router_cutover.py`

After computing the route result, attach project context:
```python
from server.learning import get_project_context
try:
    result["project_context"] = get_project_context(project)
except Exception:
    result["project_context"] = {}
```

**Task B2.3 — Inject project context into autopilot prompt.**

File: `server/broker_runtime.py:create_task_run`

Before generating phases, load and attach project context to `repo_context`:
```python
from server.learning import get_project_context
project_ctx = get_project_context(project)
repo_context.setdefault("project_learnings", project_ctx["learnings"])
repo_context.setdefault("project_top_resources", project_ctx["top_resources"])
```

Verification:
```bash
curl -s -X POST http://127.0.0.1:8080/nexus/route \
  -H "Content-Type: application/json" \
  -d '{"task":"add tests","project":"nexus"}' | python3 -m json.tool | grep -A2 project_context
```

Expected: `project_context` present with learnings and top_resources arrays.

**Commit:** `B2: project learnings injected into route and autopilot sessions`

---

## Phase C: Honest Autonomy (RC-5)

Goal: Autopilot produces a real preview, waits for approval, executes real tools, records real outcomes.

### C1. Change autopilot default to preview-only

**Files touched:** `server/broker_runtime.py`

**Why:** `create_task_run` defaults `auto_execute=True`. This is dishonest autonomy — it claims to execute but hardcodes the outcome.

**Task C1.1 — Flip default to `auto_execute=False`.**

File: `server/broker_runtime.py:553`

Change:
```python
def create_task_run(
    task_text: str,
    *,
    ide_name: str = "unknown",
    mode: str = "safemode",
    project: str = "default",
    cwd: str = "",
    repo_context: Optional[Dict[str, Any]] = None,
    auto_execute: bool = False,  # CHANGED
) -> Dict[str, Any]:
```

**Task C1.2 — Update HTTP request model default.**

File: `server/nexus_http_daemon.py` — find `AutopilotRunRequest` Pydantic model and set:
```python
auto_execute: bool = False
```

Verification:
```bash
curl -s -X POST http://127.0.0.1:8080/nexus/autopilot/run \
  -H "Content-Type: application/json" \
  -d '{"task":"write a test","project":"nexus","ide_name":"hermes"}' | python3 -m json.tool | grep status
```

Expected: `status` is `previewed`, not `completed`.

**Commit:** `C1: autopilot defaults to preview-only`

---

### C2. Real execution path with side-effect gating

**Files touched:** `server/broker_runtime.py`, `server/verify_registry.py`

**Why:** `execute_task_run` currently writes to `execution_log` and `tool_traces` but does not actually invoke tools. The `outcome` is hardcoded.

**Task C2.1 — Add a tool invocation dispatcher.**

New function in `server/broker_runtime.py`:
```python
def _invoke_tool(trace: sqlite3.Row, project: str, cwd: str) -> dict:
    """Invoke a tool based on its resource_type and execution_mode.

    For now, only 'read' side-effect tools are auto-invoked.
    'local_write' and 'operational' require explicit approval.
    """
    mode = trace["execution_mode"]
    side_effect = trace["side_effect_level"]
    resource_type = trace["resource_type"]
    name = trace["resource_name"]

    if side_effect in {"external_write", "operational"}:
        return {"status": "blocked", "reason": "requires_approval", "side_effect": side_effect}

    if resource_type in {"doc", "spec", "design_system", "craft"}:
        # Context retrieval only
        return {"status": "retrieved", "resource_name": name, "side_effect": side_effect}

    if resource_type in {"skill", "agent"}:
        return {"status": "delegated", "resource_name": name, "side_effect": side_effect}

    if resource_type in {"command", "cli_tool"}:
        if side_effect == "read":
            return {"status": "ready", "resource_name": name, "command": ["echo", f"ready:{name}"], "side_effect": side_effect}
        return {"status": "blocked", "reason": "write_side_effect_requires_approval"}

    return {"status": "unknown_resource_type", "resource_type": resource_type}
```

**Task C2.2 — Wire dispatcher into `execute_task_run`.**

After selecting tools for each phase, call `_invoke_tool` and update `tool_traces` with real status. Then run the verifier and set the real outcome.

Replace the current hardcoded outcome block with:
```python
invocation_results = [_invoke_tool(row, project, run["cwd"]) for row in selected]
outcome_notes = json.dumps(invocation_results, default=str)[:500]

# Run real verifier
from server.verify_registry import verify
verdict = verify(phase.get("stage", "general"), project_path=run["cwd"] or cwd)
outcome = verdict["outcome"]

for row, inv in zip(selected, invocation_results):
    conn.execute(
        "UPDATE tool_traces SET status='executed', outcome=?, notes=? WHERE id=?",
        (outcome, f"{outcome_notes}\nverdict={json.dumps(verdict, default=str)[:250]}", row["id"]),
    )
    _emit_outcome_for_trace(
        trace_name=row["resource_name"],
        trace_phase=f"{phase['id']}: {phase['name']}",
        outcome=outcome,
        project=project,
        ide=ide,
    )
```

**Task C2.3 — Add approve endpoint for blocked writes.**

Add to `server/broker_runtime.py`:
```python
def approve_and_execute(task_run_id: str) -> Dict[str, Any]:
    """User approves a previewed task run; execute blocked write/operational tools."""
    initialize_runtime()
    conn = _get_conn()
    try:
        run = conn.execute("SELECT * FROM task_runs WHERE id=?", (task_run_id,)).fetchone()
        if not run or run["status"] != "previewed":
            return {"status": "error", "message": "task_run not in previewed state"}
        # Re-run execute with write side effects allowed
        return execute_task_run(task_run_id, allow_writes=True)
    finally:
        conn.close()
```

Add signature change to `execute_task_run`:
```python
def execute_task_run(task_run_id: str, allow_writes: bool = False) -> Dict[str, Any]:
```

In `_invoke_tool`, allow writes when `allow_writes=True`:
```python
if side_effect in {"external_write", "operational"} and not allow_writes:
    return {"status": "blocked", ...}
```

**Commit:** `C2: real tool dispatcher + verifier-driven outcomes + approve path`

---

### C3. TUI IDE bridge default-on

**Files touched:** `server/ide_manager.py`, `server/nexus_http_daemon.py`, `server/nexus-mcp-enhanced.py`

**Why:** `IDEManager` exists but is not automatically populated when an IDE connects. Every MCP session should register itself.

**Task C3.1 — Auto-register IDE session in MCP `initialize`.**

File: `server/nexus-mcp-enhanced.py` (find `initialize` handler)

Add:
```python
from server.ide_manager import IDEManager
manager = IDEManager()
ide_type = os.environ.get("NEXUS_IDE", "unknown")
session = manager.register(ide_type=ide_type, pid=os.getpid(), project=os.environ.get("NEXUS_PROJECT", "default"))
# Return session_id in server info or store it
```

**Task C3.2 — Expose `/nexus/ide/sessions` endpoint.**

File: `server/nexus_http_daemon.py`

```python
@app.get("/nexus/ide/sessions")
async def nexus_ide_sessions():
    from server.ide_manager import IDEManager
    manager = IDEManager()
    return JSONResponse({"sessions": [s.to_dashboard_dict() for s in manager.list_active()]})
```

Verification:
```bash
curl -s http://127.0.0.1:8080/nexus/ide/sessions | python3 -m json.tool
```

Expected: list of active IDE sessions.

**Commit:** `C3: IDE sessions auto-registered and exposed`

---

## Phase D: Harness on Hot Path (RC-7)

Goal: Context harness is loaded eagerly and used as a routing signal, not a separate dashboard.

### D1. Default harness eager

**Files touched:** `server/router_cutover.py`, `server/harness_engine/attach.py`, `supervisord.conf`

**Why:** `server/router_cutover.py:277` sets `NEXUS_HARNESS_EAGER=false` by default.

**Task D1.1 — Change default to eager.**

File: `server/router_cutover.py`

Change:
```python
eager = os.environ.get("NEXUS_HARNESS_EAGER", "true").lower() in ("1", "true", "yes")
```

**Task D1.2 — Update supervisord environment blocks.**

File: `supervisord.conf`

Add to all environment lines:
```ini
NEXUS_HARNESS_EAGER="true"
```

Verification:
```bash
curl -s -X POST http://127.0.0.1:8080/nexus/route \
  -H "Content-Type: application/json" \
  -d '{"task":"deploy to prod","project":"nexus"}' | python3 -m json.tool | grep -i harness
```

Expected: harness plan/toolsets present in response.

**Commit:** `D1: harness eager by default`

---

### D2. Pre-fetch harness context before route

**Files touched:** `server/router_cutover.py`, `server/harness_engine/attach.py`

**Why:** Harness is currently attached after routing. For project state to influence ranking, it must be fetched first.

**Task D2.1 — Split `attach_harness_to_result` into `fetch_harness_context` + `attach`.**

In `server/harness_engine/attach.py`:
```python
def fetch_harness_context(task: str, project: str) -> dict:
    """Fetch project state, site registry, and relevant learnings before routing."""
    from server.harness_engine import plan_toolsets
    try:
        plan = plan_toolsets(task=task)
    except Exception:
        plan = {"toolsets": []}
    return {"plan": plan, "project": project}


def attach_harness_to_result(result: dict, task: str, execute_research: bool = False) -> dict:
    ctx = fetch_harness_context(task, result.get("project", "default"))
    result["harness"] = ctx
    return result
```

**Task D2.2 — Call `fetch_harness_context` before routing in `route_for_task`.**

File: `server/router_cutover.py`

```python
from server.harness_engine.attach import fetch_harness_context

ctx = fetch_harness_context(task, project)
# Use harness toolsets as preferred_names hint to the router
preferred_names = []
for toolset in ctx.get("plan", {}).get("toolsets", []):
    preferred_names.extend(toolset.get("resources", []))

active = flag if flag is not None else get_router_flag()
if active == "2":
    result = _shape_v5(task, project, max_results, preferred_names=preferred_names)
...
```

Also update `_shape_v5` to pass `preferred_names` into `unified_route_v5`.

Verification:
```bash
pytest tests/test_harness_hot_path.py -v
```

Test file `tests/test_harness_hot_path.py`:
```python
from server.harness_engine.attach import fetch_harness_context

def test_harness_context_before_route():
    ctx = fetch_harness_context("deploy to prod", "nexus")
    assert "plan" in ctx
    assert isinstance(ctx["plan"], dict)
```

**Commit:** `D2: harness context fetched before routing`

---

### D3. Use harness state as ranking signal

**Files touched:** `server/router_v2/v5.py`, `server/router.py`

**Task D3.1 — Boost resources listed in harness toolsets.**

In `server/router.py:find_matching_skills`, accept `preferred_names` and give a score bonus:
```python
preferred_name_set = {name.lower() for name in (preferred_names or [])}
...
name_match_bonus = 0.0
for tw in task_words:
    ...
if row['name'].lower() in preferred_name_set:
    name_match_bonus += 0.5
name_match_bonus = min(name_match_bonus, 0.8)
```

In `server/router_v2/v5.py`, apply a similar `harness_preference_boost` to `final_score`.

Verification:
```bash
curl -s -X POST http://127.0.0.1:8080/nexus/route \
  -H "Content-Type: application/json" \
  -d '{"task":"run tests","project":"nexus"}' | python3 -m json.tool | head -40
```

Expected: top results include pytest/verification-related skills when harness signals testing.

**Commit:** `D3: harness toolsets boost ranking scores`

---

## Phase E: Production Hardening (RC-9)

Goal: One supervised process topology, one MCP server, boot sanity check.

### E1. Fix supervisord to own the live MCP and HTTP

**Files touched:** `supervisord.conf`, `server/process_supervisor.py`

**Why:** `supervisord.conf` currently sets `autostart=false` for MCP and HTTP because of fear of killing the live session. For production, supervisor must own them.

**Task E1.1 — Set `autostart=true` for MCP and HTTP.**

File: `supervisord.conf`

Change:
```ini
[program:nexus-http-daemon]
autostart=true

[program:nexus-mcp-server]
autostart=true
```

**Task E1.2 — Ensure only one MCP process.**

File: `server/nexus-mcp-enhanced.py`

Add a pidfile/lock at startup:
```python
import fcntl
PIDFILE = Path("/home/roshan/nexus/run/nexus-mcp.pid")

def _acquire_singleton_lock():
    PIDFILE.parent.mkdir(parents=True, exist_ok=True)
    fd = open(PIDFILE, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError("Another NEXUS MCP server is already running")
    fd.write(str(os.getpid()))
    fd.flush()
    return fd
```

Call `_acquire_singleton_lock()` early in `main()`.

**Task E1.3 — HTTP uses in-process MCP bridge instead of spawning MCP.**

File: `server/nexus_http_daemon.py`

Do not spawn a separate MCP process from HTTP. If an MCP function is needed, import and call it directly (e.g., `from server.nexus_mcp_enhanced import find_skills_tool`).

**Commit:** `E1: supervisor owns MCP+HTTP; singleton MCP lock`

---

### E2. Boot-time sanity check

**Files touched:** `server/nexus_http_daemon.py`, `server/nexus-mcp-enhanced.py`, new `server/sanity_check.py`

**Task E2.1 — Create `server/sanity_check.py`.**

```python
#!/usr/bin/env python3
"""Boot-time sanity check for NEXUS."""
import sqlite3
from pathlib import Path
from server.db import get_db_path

def run_sanity() -> dict:
    checks = {}
    db = get_db_path()
    checks["db_exists"] = db.exists()
    if db.exists():
        conn = sqlite3.connect(str(db), timeout=5)
        conn.row_factory = sqlite3.Row
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        checks["tables"] = tables
        checks["resources_count"] = conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
        conn.close()
    else:
        checks["error"] = "db missing"
    checks["all_pass"] = checks["db_exists"] and "resources" in checks.get("tables", [])
    return checks
```

**Task E2.2 — Run sanity at HTTP startup and expose `/health`.**

File: `server/nexus_http_daemon.py`

```python
from server.sanity_check import run_sanity

@app.get("/health")
async def health():
    return JSONResponse(run_sanity())
```

Call `run_sanity()` at module load and log failures.

**Task E2.3 — Run sanity at MCP startup.**

File: `server/nexus-mcp-enhanced.py`

At top of `main()`:
```python
from server.sanity_check import run_sanity
sanity = run_sanity()
if not sanity["all_pass"]:
    logger.error(f"Sanity check failed: {sanity}")
    raise RuntimeError(f"NEXUS sanity check failed: {sanity}")
```

Verification:
```bash
curl -s http://127.0.0.1:8080/health | python3 -m json.tool
```

Expected: `all_pass: true`, `resources_count` > 0.

**Commit:** `E2: boot-time sanity check + /health endpoint`

---

## Verification Gates (G1-G10)

| Gate | What | Command / Test | Pass Criteria |
|------|------|----------------|---------------|
| G1 | Single router path | `pytest tests/test_router_unified.py` | HTTP, MCP, and broker return same top-3 for identical task/project |
| G2 | Non-terminal outcomes do not learn | `pytest tests/test_learning_loop.py` | `times_triggered` unchanged after `routed` |
| G3 | Terminal outcomes update counters | `pytest tests/test_learning_loop.py` | `times_correct` increments by 1 after `correct` |
| G4 | No fake eval verification | `pytest tests/test_no_fake_verify.py` | `route_task` never returns `correct` for production calls |
| G5 | Real verifier runs | `python3 -c "from server.verify_registry import verify; print(verify('coding','.'))"` | `outcome` matches actual pytest returncode |
| G6 | Project required | `curl /nexus/route` with `project=default` | HTTP 400 |
| G7 | Project boost works | `pytest tests/test_project_boost.py` | Resources in `project_resources` rank higher for that project |
| G8 | Autopilot preview-only default | `curl /nexus/autopilot/run` | Returns `status: previewed` |
| G9 | Harness eager | `curl /nexus/route` | Response includes `harness` key |
| G10 | Health green | `curl /health` | `all_pass: true` |

---

## Rollback Procedures

| If this breaks | Rollback action |
|----------------|-----------------|
| Router ranking degrades | `export USE_UNIFIED_ROUTER=0` and restart; v4 keyword fallback still exists in `server/router.py` |
| Outcome coverage drops | Revert `record_outcome` to previous commit; keep `routing_log` writes only |
| Project filter hides too many skills | Temporarily allow `default` project in HTTP; tune `project_boost` from 0.25 to 0.1 |
| Autopilot blocks all writes | Set `auto_execute=true` behind explicit env `NEXUS_AUTO_EXECUTE=1` for trusted projects only |
| Harness slows hot path | `export NEXUS_HARNESS_EAGER=false` and restart |
| Supervisor cannot start MCP | Revert `autostart` to `false` for `nexus-mcp-server`; keep HTTP under supervisor |

---

## Risk Matrix

| Phase | Risk | Mitigation |
|-------|------|------------|
| A1 | HIGH — changes live ranking for all entry points | Run A/B suite before cutover; keep `USE_UNIFIED_ROUTER` env flag |
| A2 | MEDIUM — database counter behavior changes | Add tests first; run on staging DB copy |
| A3 | MEDIUM — removes "easy" fake accuracy numbers | Communicate to stakeholders; replace with real verifier metrics |
| B1 | MEDIUM — project filter may over-narrow | Default to no filter if `project_resources` empty for a project |
| C2 | HIGH — first real tool execution | Only auto-invoke `read` side-effect tools; writes require approval |
| D2 | LOW-MEDIUM — harness latency in hot path | Cache harness plan per (task_hash, project) for 60s |
| E1 | HIGH — process topology change | Apply out of active IDE session; keep manual systemd fallback |

---

## Suggested Commit Order

1. `A1: single public router path via route_for_task`
2. `A2: single record_outcome API with non-terminal guard`
3. `A3: real verify_registry replaces eval-dataset fake verification`
4. `A4: dashboard endpoints gated; stats report loop health`
5. `B1: project-scoped ranking; reject default project`
6. `B2: project learnings injected into route and autopilot sessions`
7. `C1: autopilot defaults to preview-only`
8. `C2: real tool dispatcher + verifier-driven outcomes + approve path`
9. `C3: IDE sessions auto-registered and exposed`
10. `D1: harness eager by default`
11. `D2: harness context fetched before routing`
12. `D3: harness toolsets boost ranking scores`
13. `E1: supervisor owns MCP+HTTP; singleton MCP lock`
14. `E2: boot-time sanity check + /health endpoint`

---

## Notes

- All code snippets are provisional until exact line numbers are re-verified after each preceding commit.
- Run `pytest` after every commit. Do not proceed to the next commit if tests fail.
- The plan intentionally leaves dashboard/research endpoints gated, not deleted, so the user can re-enable them once C1-C5 are true.
- `core/session_builder.py` is referenced in v3 plan; if it does not exist, the project memory injection in Phase B should live in `server/learning.py` and `server/broker_runtime.py` as shown.
