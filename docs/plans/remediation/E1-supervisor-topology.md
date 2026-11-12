# E1 — Supervisor owns MCP and HTTP (topology change)

Source: plan §E1 · Commit: `E1: supervisor owns MCP+HTTP; singleton MCP lock` · Depends on: A4, C3 · **Blocked on decision — conflicts with CLAUDE.md**

## Conflict with CLAUDE.md (must resolve first)

`CLAUDE.md` § Process topology states:

> "`supervisorctl status` showing MCP/HTTP/harness as **STOPPED** is intentional; do not flip `autostart=true` while the IDE or systemd owns those processes."

It also says the MCP stdio server is **"per-session spawn"** (`.mcp.json`, one process per IDE session).

The plan's E1.1 flips `autostart=true` for `nexus-mcp-server`, and E1.2 adds a **singleton lock** so only one MCP process can run. These two rules cannot both hold: a singleton MCP would break a second IDE session, and supervisor autostart would start an MCP that the IDE also spawns.

Current state (verified): 4 `nexus-mcp-enhanced.py` processes are running (PIDs 2523, 5375, 14764, 83348). The singleton lock would stop three of the four from starting, or kill their IDE connections on restart.

## Corrections to source plan

| Plan says | Reality |
|---|---|
| `autostart=true` for `[program:nexus-http-daemon]` and `[program:nexus-mcp-server]` | Currently `autostart=false` on the MCP program (`supervisord.conf:65–75`). `CLAUDE.md` forbids flipping it for MCP. HTTP daemon is a separate question — see decisions. |
| Singleton lock at `/home/roshan/nexus/run/nexus-mcp.pid` | Hardcoded path. Use repo-relative `run/`; directory exists. |
| E1.3: `from server.nexus_mcp_enhanced import find_skills_tool` | **Invalid.** The file is `nexus-mcp-enhanced.py` (hyphen) — cannot be imported by that name. The function is `find_skills` (line 79), not `find_skills_tool`. Calling MCP tools from the HTTP daemon would import a stdio server into the HTTP process. |
| Dashboard and context-harness programs | Not in plan; leave unchanged. |

## Pseudocode (only the parts that don't conflict)

HTTP daemon under supervisor is the least controversial part:

```ini
; supervisord.conf — [program:nexus-http-daemon]
autostart=true          ; DECISION REQUIRED — CLAUDE.md is silent on HTTP; confirm
autorestart=true
```

Singleton lock applies **only to the HTTP daemon** (it binds :8000 — a real singleton), not to MCP:

```python
# server/nexus_http_daemon.py — at startup
import fcntl, os
from pathlib import Path
PIDFILE = Path(__file__).resolve().parents[1] / "run" / "nexus-http.pid"

def _singleton_lock():
    PIDFILE.parent.mkdir(parents=True, exist_ok=True)
    # O_RDWR|O_CREAT — never truncate before flock, or a losing process
    # erases the live owner's PID.
    fd = os.open(PIDFILE, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise RuntimeError("NEXUS HTTP daemon already running (see run/nexus-http.pid)")
    os.ftruncate(fd, 0)
    os.write(fd, str(os.getpid()).encode()); os.fsync(fd)
    return fd          # keep reference alive for process lifetime
```

For HTTP, E1.3's "don't spawn MCP from HTTP" is correct; implement it by **not importing** the stdio server. If HTTP needs a tool, call the shared function in the module that defines it (e.g., `server.router_cutover.route_for_task`), never `nexus-mcp-enhanced.py`.

## Steps

1. **Decide** the open questions below. Do not edit `supervisord.conf` before deciding.
2. Back up `supervisord.conf`.
3. Apply the HTTP-only change (autostart + singleton lock) in a commit.
4. Verify the daemon restarts cleanly with `supervisorctl restart nexus-http-daemon`.
5. Leave MCP as-is unless the decision says otherwise.

## Tests

New `tests/test_singleton_lock.py`:

```python
import subprocess, sys, os, time
from pathlib import Path

def test_second_lock_fails(tmp_path, monkeypatch):
    # run a helper that takes the lock, then try again in a second process
    ...
```

Keep the lock helper in a module the test can import (not inside the daemon's import-time code, which starts uvicorn).

## Verification (live)

```bash
supervisorctl status nexus-http-daemon
curl -s http://127.0.0.1:8000/health | python3 -m json.tool      # E2
```

## Rollback

Set `autostart=false`, `supervisorctl reread && supervisorctl update`, remove the lock call. The pidfile can stay.

## Open decisions (must answer before implementing)

1. **MCP autostart?** CLAUDE.md says no. Recommend: **keep `autostart=false` for MCP**. Each IDE spawns its own — that is the design.
2. **MCP singleton?** Recommend: **no**. Per-session stdio is intentional. Replace E1.2 with per-session lock keyed on IDE session id, or drop it.
3. **HTTP autostart?** Recommend: **yes** — it is a shared daemon on :8000 with one owner. Confirm the systemd unit `nexus-daemon.service` (CLAUDE.md) is not also starting it; two owners would collide on the port.
4. **Answered live 2026-11-10:** `nexus-daemon.service` (systemd **--user** scope) is `active` and owns **:8000** — PID 288888, `server/nexus_http_daemon.py --port 8000`. Nothing listens on :8080. So E1 is a change of owner, not a new owner: `systemctl --user stop nexus-daemon` must come first, or supervisor and systemd will both bind :8000.
