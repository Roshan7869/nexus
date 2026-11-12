# A4 — Gate dashboard-only endpoints; stats report loop health

Source: plan §A4 · Commit: `A4: dashboard endpoints gated; stats report loop health` · Depends on: A1 · Gate: supports G8–G10 (operator surface)

## Problem (verified)

Dashboard endpoints are mixed into the same app as the operator routes. Most live in `server/unified.py`, not the daemon:

| Endpoint | File |
|---|---|
| `/nexus/feedback/coverage`, `/nexus/kg/status`, `/nexus/router/status`, `/nexus/ops/status`, `/nexus/ops/processes`, `/nexus/research/sources`, `/nexus/research/synthesize`, `/nexus/router/ab`, `/nexus/kg/rebuild` | `server/unified.py` |
| `/nexus/stats` | both `unified.py` and `nexus_http_daemon.py` |
| Operator routes to keep: `/nexus/route`, `/nexus/search`, `/nexus/autopilot/run`, `/nexus/task-runs/*`, `/nexus/learnings` | `unified.py` |

Helpers exist: `rl_feedback_loop.get_feedback_coverage` ✅ (verified), `router_cutover.get_router_status` ✅ (verified). Returned keys (`mode`, `production_sot`, `coverage_ratio`, …) are **not verified** — read both functions first.

## Corrections to source plan

| Plan says | Reality |
|---|---|
| Edit `nexus_http_daemon.py` for dashboard endpoints | They are in **`unified.py`**. The daemon has `/stats` and `/health` only. |
| `/nexus/stats` at `nexus_http_daemon.py:370-390` | Re-locate; `/stats` also exists in `unified.py`. Decide which one is authoritative before editing. |
| `ENABLE_DASHBOARD` read from env | `CLAUDE.md` says flags go through `core.config.LearningConfig`, not `os.environ`. Add the flag there. |

## Pseudocode (corrected)

```python
# core/config.py  (LearningConfig)  — add field; YAML > env > default per CLAUDE.md
enable_dashboard: bool = False        # env: NEXUS_ENABLE_DASHBOARD

# server/unified.py
from core.config import LearningConfig
DASHBOARD_ENABLED = LearningConfig.load().enable_dashboard

def _require_dashboard():
    if not DASHBOARD_ENABLED:
        raise HTTPException(404, "dashboard disabled (NEXUS_ENABLE_DASHBOARD=1 to enable)")

@app.get("/nexus/feedback/coverage")
async def nexus_feedback_coverage():
    _require_dashboard()
    ...existing body...
```

Repeat `_require_dashboard()` as the first line of each dashboard endpoint in the table above.

```python
@app.get("/nexus/stats")
async def nexus_stats():
    from server.router_cutover import get_router_status
    from server.rl_feedback_loop import get_feedback_coverage
    r = get_router_status()          # VERIFY keys first
    c = get_feedback_coverage()      # VERIFY keys first
    return JSONResponse({
        "router_mode": r.get("mode"),
        "outcome_coverage": c.get("coverage_ratio", 0.0),
        "terminal_outcomes": c.get("terminal_outcomes", 0),
        "non_terminal_outcomes": c.get("non_terminal_outcomes", 0),
        "dashboard_enabled": DASHBOARD_ENABLED,
    })
```

## Steps

1. Read `get_router_status` and `get_feedback_coverage`; write the real key names into this file before coding.
2. Add the config field in `core/config.py`. Check `core/config.py` for the existing pattern first.
3. Add `_require_dashboard()` to each endpoint in the table. Do not delete the code.
4. Rewrite `/nexus/stats` in one place. Remove the duplicate in the daemon, or make it proxy.
5. Tests.

## Tests

New `tests/test_dashboard_gate.py`:

```python
from fastapi.testclient import TestClient
from server.unified import app

def test_dashboard_off_returns_404(monkeypatch):
    import server.unified as u
    monkeypatch.setattr(u, "DASHBOARD_ENABLED", False)
    c = TestClient(app)
    assert c.get("/nexus/feedback/coverage").status_code == 404

def test_operator_routes_stay_on(monkeypatch):
    import server.unified as u
    monkeypatch.setattr(u, "DASHBOARD_ENABLED", False)
    c = TestClient(app)
    assert c.post("/nexus/route", json={"task": "x", "project": "nexus"}).status_code != 404
```

Also check that `CLAUDE.md`'s note about `supervisorctl` is not affected (the dashboard program on :8765 is separate).

## Verification (live)

```bash
curl -s http://127.0.0.1:8000/nexus/feedback/coverage -o /dev/null -w "%{http_code}\n"   # expect 404
curl -s http://127.0.0.1:8000/nexus/stats | python3 -m json.tool
```

## Rollback

Set `NEXUS_ENABLE_DASHBOARD=1` and restart. No code revert needed.

## Open decisions

- Keep `/nexus/stats` in both apps or pick one? Recommend: `unified.py` only.
