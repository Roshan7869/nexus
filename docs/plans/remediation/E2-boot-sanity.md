# E2 — Boot-time sanity check and `/health`

Source: plan §E2 · Commit: `E2: boot-time sanity check + health checks` · Depends on: **none** (independent of E1; see Corrections) · Gate: G10

Revised after EGM Goldfish review (2026-11-10). Findings were checked against the code before editing.

## Problem (verified)

- Liveness endpoints already exist and must keep working:
  - `server/unified.py:1008` `@app.get("/health")` — documented liveness probe (`unified.py:14`); asserted by `tests/test_unified.py:244-248`.
  - `server/nexus_http_daemon.py:352` `@app.get("/health")` — returns `status`, `uptime_seconds`, `embedding_model`, `db_available`.
  - `server/nexus_http_daemon.py:153` `@app.get("/v1/health")` — execution-plane health (separate contract).
- `server/execution_plane.py` has **no** `/health` route (only `health_v1()` in its docstring contract, `:8`/`:157`). The plan's step to change it is dropped.
- `server/sanity_check.py` does not exist (new).
- `server/db.py:86` `get_db_path` exists.
- The live DB has all five required tables and `resources` has 3,254 rows (read-only check).

## Corrections to source plan

| Plan says | Reality | Decision |
|---|---|---|
| Replace the daemon `/health` body with `run_sanity()` | Existing body returns four fields that clients may read. Replacing it removes them. | **Merge**: keep the four keys, add `sanity` as a nested key. |
| Make `unified.py` `/health` call `run_sanity()` | It is the liveness probe and a test asserts it exists. Making it DB-dependent changes liveness semantics, and `JSONResponse` returns 200 even when the check fails. | **Keep `/health` as liveness** (unchanged). Add `/health/deep` for the sanity check, which returns **503** on failure. |
| Make `execution_plane.py` `/health` call `run_sanity()` | No such route. | **Dropped.** |
| `logger.error` in MCP startup | `nexus-mcp-enhanced.py` has no logger and no `logging` import. Stdout is the stdio MCP channel. | Write to `sys.stderr`. |
| Insert the check in MCP `main()` | No `main()` exists. The entry point is `if __name__ == "__main__": mcp.run(transport="stdio")` at `nexus-mcp-enhanced.py:1759-1760`. | Insert the check before line 1760. |
| E2 depends on E1 | E2 does not use anything from E1. E1 is blocked on a CLAUDE.md conflict. | **E2 is independent** and can ship before E1. Gate G10 is not blocked. |
| `sqlite3.connect(path)` | Opens the live DB read-write just to read it. | Use a `mode=ro` URI. |

## Pseudocode (corrected)

```python
# server/sanity_check.py  (NEW, < 150 lines)
from __future__ import annotations
import sqlite3
from pathlib import Path
from server.db import get_db_path

REQUIRED_TABLES = {"resources", "routing_log", "user_learnings", "tool_traces", "task_runs"}

def run_sanity() -> dict:
    checks: dict = {"db_path": None, "db_exists": False, "missing_tables": [], "resources_count": 0}
    db = Path(get_db_path())
    checks["db_path"] = str(db)
    checks["db_exists"] = db.exists()
    if not checks["db_exists"]:
        checks["all_pass"] = False
        checks["error"] = "db missing"
        return checks
    # read-only: this check must never write
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        checks["missing_tables"] = sorted(REQUIRED_TABLES - tables)
        if "resources" in tables:
            checks["resources_count"] = conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
    finally:
        conn.close()
    checks["all_pass"] = (not checks["missing_tables"]) and checks["resources_count"] > 0
    return checks
```

```python
# server/nexus_http_daemon.py:352  — MERGE, keep existing keys
from server.sanity_check import run_sanity

@app.get("/health")
async def health():
    body = {...existing four keys, unchanged...}
    body["sanity"] = run_sanity()
    return JSONResponse(body)          # liveness semantics unchanged: 200 if process is up
```

```python
# server/unified.py:1008  — liveness UNCHANGED; add deep check
@app.get("/health/deep")
async def health_deep():
    from server.sanity_check import run_sanity
    s = run_sanity()
    return JSONResponse(s, status_code=200 if s["all_pass"] else 503)
```

```python
# server/nexus-mcp-enhanced.py  — insert before line 1760 (mcp.run)
import sys
from server.sanity_check import run_sanity

if __name__ == "__main__":
    _s = run_sanity()
    if not _s["all_pass"]:
        # stderr only: stdout is the stdio MCP channel
        print(f"NEXUS sanity check failed: {_s}", file=sys.stderr)
        sys.exit(1)
    mcp.run(transport="stdio")
```

Startup refusal is intentional: a broken DB should not serve bad routes. If you prefer log-only, change `sys.exit(1)` to a stderr warning (see open decisions).

## Steps

1. Create `server/sanity_check.py` with the read-only URI.
2. Merge `run_sanity()` into the daemon `/health` body, keeping the four existing keys. Do not change the status code.
3. Add `/health/deep` to `unified.py`. Leave `/health` unchanged.
4. Add the startup check before `mcp.run` in `nexus-mcp-enhanced.py:1759-1760`.
5. Do not touch `execution_plane.py` or `/v1/health`.
6. Tests (below).

## Tests

New `tests/test_sanity_check.py`:

```python
import pytest
from server.sanity_check import run_sanity

@pytest.mark.integration          # reads the live DB; skipped unless --run-integration
def test_all_pass_on_real_db():
    s = run_sanity()
    assert s["db_exists"] is True
    assert s["missing_tables"] == []
    assert s["resources_count"] > 0

def test_missing_db_fails(monkeypatch, tmp_path):
    import server.sanity_check as sc
    monkeypatch.setattr(sc, "get_db_path", lambda: tmp_path / "nope.db")
    assert run_sanity()["all_pass"] is False

def test_missing_table_fails(monkeypatch, tmp_path):
    import sqlite3, server.sanity_check as sc
    db = tmp_path / "t.db"
    sqlite3.connect(db).execute("CREATE TABLE resources (id INTEGER)").connection.commit()
    monkeypatch.setattr(sc, "get_db_path", lambda: db)
    s = run_sanity()
    assert s["all_pass"] is False and "routing_log" in s["missing_tables"]
```

Add to `tests/test_unified.py` (or a new file) a check that `/health` still returns 200 with the original routes, and that `/health/deep` returns 503 when `run_sanity` is patched to `all_pass=False`.

## Verification (live) — gate G10

```bash
curl -s -o /dev/null -w "liveness %{http_code}\n" http://127.0.0.1:8000/health       # expect 200
curl -s http://127.0.0.1:8000/health/deep | python3 -m json.tool                     # all_pass true
curl -s http://127.0.0.1:8080/health | python3 -m json.tool                          # four old keys + sanity
```

Expected: `all_pass: true` and `resources_count` > 0 on the deep check.

## Rollback

Remove the `/health/deep` route and the startup check. The merged daemon `/health` keeps its old keys, so clients are unaffected.

## Open decisions

- Startup behaviour on failure: **stop** (recommended, a broken DB should not serve routes) or **warn**. The current plan assumes stop.
- Whether `/health/deep` should also be exposed on the daemon (port 8080). Recommend: no, until a client needs it.
