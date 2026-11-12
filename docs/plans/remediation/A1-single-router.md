# A1 — Make `route_for_task` the single production router

Source: plan §A1 · Commit: `A1: single public router path via route_for_task` · Depends on: — · Gate: G1

## Problem (verified)

Several entry points rank skills with different functions, so the same task can return different results:

- `/nexus/route` (`server/unified.py:347`) calls `route_for_task` ✅
- `/nexus/search` (`server/unified.py:357`) calls `find_matching_skills` directly ❌
- `server/broker_runtime.py:475` already calls `route_for_task` ✅ — **plan's A1.2 is already done**
- `route_task` (v4 keyword router) is still called from more places than the plan lists:
  - `server/tool_session_broker.py:289`
  - `server/router_cutover.py:82` (v4 mode branch — keep)
  - `server/orchestrator.py:103`, `:294`
  - `server/rl_feedback_loop.py:383`
  - `server/router.py:1403`
- `find_matching_skills` is also called from `server/learning.py:1019`, `server/tool_attention_v4.py:361`, `server/coach/mcp_handlers.py:167`, and inside `router.py` itself.

## Corrections to source plan

| Plan says | Reality |
|---|---|
| A1.2: replace `route_task` at `broker_runtime.py:475` | Already `route_for_task`. Skip. |
| A1.3: edit `nexus_http_daemon.py:352-367` | `/nexus/search` is in **`server/unified.py:357`**. |
| Verify with `curl … :8080/nexus/route` | Use **`:8000`** (unified). |
| A1.1 audit lists 2 patterns | Audit must also cover `tool_session_broker.py:289` and `orchestrator.py`. |

## Pseudocode (corrected)

```python
# server/unified.py — /nexus/search
@app.post("/nexus/search")
async def nexus_search(request: Request):
    body = await request.json()
    query = body.get("query", body.get("task", ""))
    if not query:
        raise HTTPException(400, "Missing 'query' or 'task' field")
    project = body.get("project", "default")
    max_results = int(body.get("max_results", 10))
    result = await _run_sync(route_for_task, query, project, max_results)
    return JSONResponse(result)          # same shape as /nexus/route
```

```python
# server/router_cutover.py — make the mode table explicit (no behaviour change)
ROUTER_MODES = {
    "0": {"name": "v4_legacy_keyword", "path": "route_task", "production": False},
    "1": {"name": "unified_v2",        "path": "unified_route", "production": False},
    "2": {"name": "unified_v5",        "path": "unified_route_v5", "production": True},
}
DEFAULT_ROUTER_FLAG = "2"   # already true in router_cutover — verify, do not change
```

## Steps

1. **Audit** (no edits):
   ```bash
   cd /home/roshan/nexus
   grep -rn "route_task(" server --include=*.py | grep -v "def route_task"
   grep -rn "find_matching_skills(" server --include=*.py | grep -v "def find_matching"
   ```
   Write the list into the commit body. Classify each caller: *replace*, *keep (v4 fallback)*, *keep (internal)*.
2. Replace `/nexus/search` body (`unified.py:357`) with the pseudocode above.
3. For `tool_session_broker.py:289` and `orchestrator.py:103/294`: switch to `route_for_task` **only if** the caller only needs a skill list. If it needs v4-specific fields, leave it and note it.
4. Add `ROUTER_MODES` to `router_cutover.py`. Do not change `DEFAULT_ROUTER_FLAG`.
5. Add a test (below).

## Tests

New file `tests/test_router_unified.py`:

```python
import pytest
from server.router_cutover import route_for_task

QUERY = "fix a login bug"
PROJECT = "nexus"

def top3(result):
    return [r["name"] for r in result.get("results", [])[:3]]

def test_search_and_route_agree(client_unified):
    route = client_unified.post("/nexus/route", json={"task": QUERY, "project": PROJECT}).json()
    search = client_unified.post("/nexus/search", json={"query": QUERY, "project": PROJECT}).json()
    assert top3(route) == top3(search)

def test_route_for_task_is_v5_by_default():
    res = route_for_task(QUERY, PROJECT, 5, open_session=False)
    assert res.get("router_mode") == "unified_v5"
```

`client_unified` is a fixture to add to `tests/conftest.py` using `fastapi.testclient.TestClient(server.unified.app)`. Use the in-memory DB fixtures; do not hit `db/nexus.db`.

Run: `pytest tests/test_router_unified.py -v`

## Verification (live)

```bash
curl -s -X POST http://127.0.0.1:8000/nexus/search \
  -H "Content-Type: application/json" \
  -d '{"query":"fix a login bug","project":"nexus"}' | python3 -m json.tool | head -20
```

Expected: `method` is the v5 method, not `keyword_match`.

Benchmark: golden eval must stay at pass ≥ 0.600 (baseline).

## Rollback

`export USE_UNIFIED_ROUTER=0` and restart the MCP/unified server. `route_task` stays in `router.py` as the v4 path.

## Open decisions

- Should `/nexus/search` keep the `keyword_match` response shape for existing clients? Recommend: no — return the `/nexus/route` shape and document it.
