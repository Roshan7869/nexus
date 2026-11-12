# C1 — Autopilot defaults to preview-only

Source: plan §C1 · Commit: `C1: autopilot defaults to preview-only` · Depends on: A3 (so the preview path has real verification) · Gate: G8

## Problem (verified)

- `server/broker_runtime.py:555` — `create_task_run(..., auto_execute: bool = True)`. Plan said line 553; **re-locate**.
- `server/broker_runtime.py:643` — `if auto_execute:` runs execution immediately.
- `server/unified.py:149` — `class AutopilotRunRequest(BaseModel)`; `auto_execute: bool = True` at **`unified.py:156`**. Plan said "find the Pydantic model" — it is `AutopilotRunRequest`.
- `server/unified.py:464` passes `req.auto_execute` through to `create_task_run`.

## Corrections to source plan

| Plan says | Reality |
|---|---|
| `broker_runtime.py:553` | `:555`. |
| `nexus_http_daemon.py` `AutopilotRunRequest` | Model is in **`unified.py:149`**; daemon does not define it. |
| `curl /nexus/autopilot/run` on `:8080` | Use **`:8000`**. |
| Expect `status: previewed` | Verify the actual status string returned by `create_task_run` when `auto_execute=False` — grep for `"previewed"`. |

## Pseudocode (corrected)

```python
# server/broker_runtime.py:555
def create_task_run(task_text, *, ide_name="unknown", mode="safemode", project="default",
                    cwd="", repo_context=None, auto_execute: bool = False):
    ...unchanged...
```

```python
# server/unified.py:149-157
class AutopilotRunRequest(BaseModel):
    ...
    auto_execute: bool = False           # was True
```

Environment escape hatch (plan rollback table): `NEXUS_AUTO_EXECUTE=1` sets the default **for trusted projects only**. Read it through `core/config.py` (CLAUDE.md), not `os.environ` directly:

```python
# core/config.py
auto_execute_default: bool = False     # env: NEXUS_AUTO_EXECUTE
```

## Steps

1. Grep callers of `create_task_run` and `execute_task_run` to see who relies on `auto_execute=True` implicitly:
   `grep -rn "create_task_run(\|auto_execute" server core --include=*.py`
2. Flip both defaults (broker and request model).
3. Check any internal caller that *needs* execution (e.g., dashboard "run now") and pass `auto_execute=True` explicitly there.
4. Tests.

## Tests

New `tests/test_autopilot_preview.py` (G8):

```python
from fastapi.testclient import TestClient
from server.unified import app

def test_autopilot_defaults_to_preview(monkeypatch):
    import server.broker_runtime as br
    called = {"exec": False}
    monkeypatch.setattr(br, "execute_task_run",
                        lambda *a, **k: called.__setitem__("exec", True) or {})
    c = TestClient(app)
    r = c.post("/nexus/autopilot/run", json={"task": "write a test", "project": "nexus", "ide_name": "hermes"})
    assert r.json()["status"] == "previewed"
    assert called["exec"] is False
```

Use an in-memory DB for `create_task_run`; do not write `task_runs` rows into `db/nexus.db`.

## Verification (live)

```bash
curl -s -X POST http://127.0.0.1:8000/nexus/autopilot/run -H "Content-Type: application/json" \
  -d '{"task":"write a test","project":"nexus","ide_name":"hermes"}' | python3 -m json.tool | grep status
```

Expected: `"status": "previewed"`.

## Rollback

`NEXUS_AUTO_EXECUTE=1` restores old behaviour without a code revert (once the config flag exists).

## Open decisions

- Does any scheduled job depend on `auto_execute=True`? Check `supervisord.conf` programs and `scripts/` before flipping.
