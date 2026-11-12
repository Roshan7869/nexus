# C3 — IDE sessions auto-registered and exposed

Source: plan §C3 · Commit: `C3: IDE sessions auto-registered and exposed` · Depends on: — · **Design conflict — decide first**

## Problem (verified)

- `server/ide_manager.py` has `IDEManager` with `register(ide_type, pid, tmux_session=None, notes="", *, project="default")` (line 122), `get`, `list_active` (149), `to_dashboard_dict` (67) ✅.
- No `/nexus/ide/sessions` endpoint exists (grep: none). New.
- `nexus-mcp-enhanced.py` has no `initialize` handler (grep found `find_skills` at line 79 and no `def initialize` / `def main`). The plan's "find `initialize` handler" does not exist as written.

## Conflict with CLAUDE.md (must resolve)

`CLAUDE.md` § Project Structure rules:

> "nothing that must survive an IDE switch may live in MCP process memory (stdio MCP = one process per IDE)." — `server/project_continuity.py` docstring; `docs/UNIFIED_MCP_DESIGN.md`.

`IDEManager` stores sessions **in process memory**. Registering there gives the same IDE a different view per MCP process, and the data is lost on restart. Persist to the DB (existing `project_continuity` tables, or a new `ide_sessions` table) instead.

## Corrections to source plan

| Plan says | Reality |
|---|---|
| Add registration in MCP `initialize` handler | No such handler. Registration belongs at MCP startup (`main()` or module load) — find the entry point first. |
| `manager.register(...)` once per MCP process | Each IDE session spawns its own MCP process (stdio). Four `nexus-mcp-enhanced.py` processes are running now (PIDs 2523, 5375, 14764, 83348), so counting processes ≠ counting IDE sessions. |
| `IDEManager()` as a fresh instance in each endpoint | Each call creates a new empty manager → `/nexus/ide/sessions` would always be empty. Must use a shared/persisted store. |

## Pseudocode (corrected direction — pending decision)

Recommended: persist to DB, read from DB.

```sql
-- db/migrations/023_ide_sessions.sql
CREATE TABLE IF NOT EXISTS ide_sessions (
    id          TEXT PRIMARY KEY,
    ide_type    TEXT NOT NULL,
    pid         INTEGER NOT NULL,
    project     TEXT NOT NULL DEFAULT 'default',
    started_at  TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen   TEXT NOT NULL DEFAULT (datetime('now')),
    alive       INTEGER NOT NULL DEFAULT 1
);
```

```python
# server/ide_sessions.py (NEW)  — heartbeat + list, DB-backed
def register(ide_type: str, pid: int, project: str) -> str: ...   # INSERT, returns id
def heartbeat(session_id: str) -> None: ...                       # UPDATE last_seen
def list_active(max_age_s: int = 120) -> list[dict]: ...          # alive AND recent
```

```python
# server/nexus-mcp-enhanced.py  — at startup, after sanity check (E2)
session_id = ide_sessions.register(os.environ.get("NEXUS_IDE", "unknown"), os.getpid(),
                                   os.environ.get("NEXUS_PROJECT", "default"))
```

```python
# server/unified.py
@app.get("/nexus/ide/sessions")
async def ide_sessions():
    return JSONResponse({"sessions": ide_sessions_mod.list_active()})
```

## Steps

1. **Decide** persistence model (see open decisions). Do not start with in-memory `IDEManager`.
2. Locate MCP startup code: `grep -n "^if __name__\|def main\|mcp.run" server/nexus-mcp-enhanced.py`.
3. Add migration `023_ide_sessions.sql` (back up DB first).
4. Implement `server/ide_sessions.py` (<200 lines).
5. Register at startup; add heartbeat on each tool call (cheap UPDATE).
6. Expose endpoint.

## Tests

New `tests/test_ide_sessions.py` (in-memory DB):

```python
from server import ide_sessions

def test_register_then_list(mock_rw_conn):
    sid = ide_sessions.register("claude-code", 4242, "nexus")
    active = ide_sessions.list_active(max_age_s=3600)
    assert any(s["id"] == sid and s["project"] == "nexus" for s in active)

def test_stale_session_not_listed(mock_rw_conn):
    sid = ide_sessions.register("opencode", 1, "nexus")
    # force last_seen into the past via direct UPDATE in the test DB
    assert all(s["id"] != sid for s in ide_sessions.list_active(max_age_s=0))
```

## Verification (live)

```bash
curl -s http://127.0.0.1:8000/nexus/ide/sessions | python3 -m json.tool
```

Expected: one entry per open IDE with a recent heartbeat.

## Rollback

Remove the register call; the table and endpoint can stay unused.

## Open decisions

- **Persist to DB (recommended)** vs keep in-memory `IDEManager` (conflicts with CLAUDE.md design rule).
- Heartbeat interval and staleness threshold (suggest 120 s).
