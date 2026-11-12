# B2 — Inject project memory into route and autopilot sessions

Source: plan §B2 · Commit: `B2: project learnings injected into route and autopilot sessions` · Depends on: B1 · Gate: none in G1–G10 (supports G7 quality)

## Problem (verified)

- `user_learnings` exists with columns `scope, task_type, project, note, bundles_json, tools_json, created_by, created_at` ✅
- `routing_log` has `skill_name, project, outcome` ✅ — the plan's SQL is valid.
- `learning.get_project_context` **does not exist** — must be created.
- `core/session_builder.py` **does not exist**.
- `learning.py:_get_conn` (line 52) and `_parse_skill_list` (line 185) exist ✅.
- `router_cutover.route_for_task` and `broker_runtime.create_task_run` are the two injection points.

## Corrections to source plan

| Plan says | Reality |
|---|---|
| `finally: if owns := True: conn.close()` | Invalid intent. Use plain `finally: conn.close()`. |
| `core/session_builder.py` | Missing. Put helpers in `server/learning.py` (plan's own fallback). |
| `broker_runtime.create_task_run` `repo_context.setdefault(…)` | `create_task_run` signature verified at `broker_runtime.py:553`-ish — re-locate. Keep `repo_context` as the carrier. |
| `route_for_task` attaches `project_context` | Adds payload size. Verify MCP/HTTP clients tolerate an extra key (they should; it's additive). |

## Pseudocode (corrected)

```python
# server/learning.py  (NEW function)
def get_project_context(project: str, limit: int = 10) -> dict:
    conn = _get_conn()
    try:
        learnings = conn.execute(
            """SELECT note, task_type, bundles_json, tools_json
               FROM user_learnings WHERE project=? ORDER BY created_at DESC LIMIT ?""",
            (project, limit)).fetchall()
        top = conn.execute(
            """SELECT skill_name, COUNT(*) AS n,
                      SUM(CASE WHEN outcome='correct' THEN 1 ELSE 0 END) AS ok
               FROM routing_log
               WHERE project=? AND outcome IN ('correct','wrong')
               GROUP BY skill_name ORDER BY ok DESC LIMIT ?""",
            (project, limit)).fetchall()
        return {
            "learnings": [{"note": r["note"], "task_type": r["task_type"],
                           "bundles": _parse_skill_list(r["bundles_json"]),
                           "tools": _parse_skill_list(r["tools_json"])} for r in learnings],
            "top_resources": [{"name": r["skill_name"], "uses": r["n"], "correct": r["ok"]} for r in top],
        }
    finally:
        conn.close()
```

```python
# server/router_cutover.py  — after building result
try:
    from server.learning import get_project_context
    result["project_context"] = get_project_context(project)
except Exception:
    result["project_context"] = {"learnings": [], "top_resources": []}
```

```python
# server/broker_runtime.py  — inside create_task_run, before phases are generated
from server.learning import get_project_context
ctx = get_project_context(project)
repo_context.setdefault("project_learnings", ctx["learnings"])
repo_context.setdefault("project_top_resources", ctx["top_resources"])
```

## Steps

1. Confirm `user_learnings` has rows for at least one project (`SELECT project, COUNT(*) FROM user_learnings GROUP BY project`). If empty, the feature is a no-op — say so in the commit.
2. Add `get_project_context`. Keep under 500 lines in `learning.py` — it is already 69 KB; consider putting the helper in a new `server/project_memory.py` instead. **Decide before editing.**
3. Wire into `route_for_task` and `create_task_run`.
4. Tests.

## Tests

New `tests/test_project_memory.py`:

```python
from server.learning import get_project_context

def test_empty_project_returns_empty_lists(mock_ro_conn):
    ctx = get_project_context("no-such-project")
    assert ctx == {"learnings": [], "top_resources": []}

def test_learnings_parsed(seeded_learnings_db):   # in-memory fixture with one user_learnings row
    ctx = get_project_context("probe")
    assert ctx["learnings"][0]["note"]
    assert isinstance(ctx["learnings"][0]["bundles"], list)
```

Benchmark: golden eval must not drop. Payload-size check: `project_context` ≤ 4 KB per response.

## Verification (live)

```bash
curl -s -X POST http://127.0.0.1:8000/nexus/route -H "Content-Type: application/json" \
  -d '{"task":"add tests","project":"nexus"}' | python3 -c "import sys,json;print('project_context' in json.load(sys.stdin))"
```

Expected: `True`.

## Rollback

Remove the two `setdefault`/`result[...]` lines. Function can stay unused.

## Open decisions

- New module `server/project_memory.py` vs `learning.py`. Recommend the new module (size limit).
