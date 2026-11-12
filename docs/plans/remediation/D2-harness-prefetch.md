# D2 — Fetch harness context before routing

Source: plan §D2 · Commit: `D2: harness context fetched before routing` · Depends on: D1

## Problem (verified)

- `server/harness_engine/attach.py:13` `attach_harness_to_result(result, task, *, execute_research=False)` — attaches harness **after** routing. ✅ exists.
- `fetch_harness_context` **does not exist** — must be created.
- `server/harness_engine/toolsets.py:84` `plan_toolsets(task, *, always_memory=True) -> list[str]` — returns toolset **names**, not a dict.
- `server/harness_engine/toolsets.py:108` `tools_for_toolsets(toolsets) -> list[str]` — maps names to resource names.
- `server/router_cutover.py` — `route_for_task` is the single entry point (A1).

## Corrections to source plan

| Plan says | Reality |
|---|---|
| `plan = plan_toolsets(task=task)`; `plan.get("toolsets")` | `plan_toolsets` returns `list[str]`. There is no `"toolsets"` key. |
| `toolset.get("resources", [])` | Wrong. Use `tools_for_toolsets(plan_toolsets(task))` to get names. |
| `from server.harness_engine import plan_toolsets` | Import from `server.harness_engine.toolsets` (verify `__init__` re-exports, or use the full path). |
| `_shape_v5(…, preferred_names=…)` | `unified_route_v5` signature must be checked; `find_matching_skills` already accepts `preferred_names` (verified `router.py:417`). |

## Pseudocode (corrected)

```python
# server/harness_engine/attach.py  — NEW function, keep attach_harness_to_result unchanged
from server.harness_engine.toolsets import plan_toolsets, tools_for_toolsets

def fetch_harness_context(task: str, project: str) -> dict:
    try:
        toolsets = plan_toolsets(task)                # list[str]
        names = tools_for_toolsets(toolsets)          # list[str]
    except Exception:
        toolsets, names = [], []
    return {"toolsets": toolsets, "preferred_names": names, "project": project}
```

```python
# server/router_cutover.py  — in route_for_task, before ranking
from server.harness_engine.attach import fetch_harness_context
ctx = fetch_harness_context(task, project)
preferred = ctx["preferred_names"]
# pass preferred into the ranker (both v4 and v5 paths)
result = _shape_v5(task, project, max_results, preferred_names=preferred)   # verify _shape_v5 signature
result["harness"] = ctx        # still attach for G9
```

## Steps

1. Confirm `plan_toolsets` and `tools_for_toolsets` behaviour on three sample tasks (run in a REPL, record output in the commit body).
2. Add `fetch_harness_context` to `attach.py` (keep file under 500 lines — check size first).
3. Thread `preferred_names` into `route_for_task` → `_shape_v5` → `unified_route_v5`. Check each signature.
4. Keep `attach_harness_to_result` for callers that still use it.
5. Tests.

## Tests

`tests/test_harness_hot_path.py` (the plan's file, corrected):

```python
from server.harness_engine.attach import fetch_harness_context

def test_context_has_expected_keys():
    ctx = fetch_harness_context("deploy to prod", "nexus")
    assert set(ctx) >= {"toolsets", "preferred_names", "project"}
    assert isinstance(ctx["toolsets"], list)
    assert isinstance(ctx["preferred_names"], list)

def test_failure_degrades_to_empty(monkeypatch):
    import server.harness_engine.attach as a
    monkeypatch.setattr(a, "plan_toolsets", lambda *_a, **_k: 1 / 0)
    ctx = fetch_harness_context("x", "nexus")
    assert ctx["preferred_names"] == []
```

Note: the failure test patches `attach.plan_toolsets`, so `attach.py` must import the name at module level (as in the pseudocode).

Benchmark: golden eval pass rate must not drop; latency change recorded (see D1).

## Verification (live)

```bash
curl -s -X POST http://127.0.0.1:8000/nexus/route -H "Content-Type: application/json" \
  -d '{"task":"deploy to prod","project":"nexus"}' | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('harness',{}).keys())"
```

## Rollback

Stop passing `preferred_names`; `fetch_harness_context` can stay.

## Open decisions

- Cache the harness plan per (task, project) for 60 s (plan's D2 mitigation). Recommend: yes if D1 latency measurement shows > 300 ms p95.
