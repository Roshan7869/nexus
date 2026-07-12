# NEXUS Process Topology (Gap 2 / RC2)

**Contract id:** `gap2.v1` · **Model:** split ownership (not single-parent)

## Why supervisor shows STOPPED

`supervisorctl status` listing `nexus-mcp-server`, `nexus-http-daemon`, and
`nexus-context-harness` as **STOPPED** is **intentional**. Those programs have
`autostart=false` so supervisord does not fight their real owners.

| Component | Primary owner | How it runs | Supervisor |
|-----------|---------------|-------------|------------|
| **HTTP API** `:8080` | **systemd** `nexus-daemon.service` | Long-lived execution plane | `autostart=false` (STOPPED OK) |
| **MCP tools** (stdio) | **IDE / host** via `.mcp.json` | Per-session spawn of `nexus-mcp-enhanced.py` | `autostart=false` (STOPPED OK) |
| **Harness tools** | **MCP bridge** in-process | `harness_mcp_bridge.register_harness_tools()` on main MCP | `autostart=false` (STOPPED OK) |
| **Dashboard** `:8765` | **supervisord** | Ops UI | `autostart=true` (must RUNNING) |
| **supervisord** | self | Parent for dashboard (+ optional cutover) | must be up |

## Do not “fix” STOPPED by flipping autostart

Starting MCP under supervisor **while** Claude Code / OpenCode also spawn
stdio MCP creates dual parents, log races, and broken sessions.

Starting HTTP under supervisor **while** `nexus-daemon.service` owns `:8080`
creates port conflicts.

Harness as a second stdio server duplicates tools already merged into NEXUS MCP.

## Gate definition

Ops process gate (`process_supervisor.get_process_status().gate_pass`) requires:

1. `supervisord` up (for dashboard + control plane)
2. `nexus-http-daemon` process/port **running** (via systemd)
3. `nexus-dashboard` **running** (via supervisor)

**Not** required for gate:

- MCP stdio process (session-scoped)
- Separate context-harness process (bridged)
- LLM gateway `:8000` (`nexus-unified`)

## Cutover (future single-tree — optional)

If you want supervisord as sole parent (design plan ideal):

1. Out of session: stop IDE MCP, `systemctl --user stop nexus-daemon`
2. Set `autostart=true` for HTTP (and optionally MCP if using socket MCP, not stdio)
3. Start only under supervisor; point clients at the new transport
4. **Stdio MCP cannot be sole-parented by supervisord** while IDEs own the pipe — use HTTP + tool sessions instead, or IDE-spawned MCP only

## Code map

| Artifact | Role |
|----------|------|
| `server/process_supervisor.py` | Topology + probes + gate |
| `supervisord.conf` | Program definitions; MCP/HTTP/harness autostart=false |
| `~/.config/systemd/user/nexus-daemon.service` | HTTP owner |
| `.mcp.json` | IDE MCP spawn config |
| `server/harness_mcp_bridge.py` | Harness tools on main MCP |
| `docs/execution_plane_contract.md` | HTTP session open/report |

## Verify

```bash
# Live ownership + gate
python3 -c "from server.process_supervisor import get_process_status; import json; print(json.dumps(get_process_status(), indent=2, default=str))"

# Supervisor (STOPPED for MCP/HTTP/harness is OK)
venv/bin/supervisorctl -c supervisord.conf status

# HTTP owner
systemctl --user status nexus-daemon.service

# Execution plane
curl -s http://127.0.0.1:8080/v1/health | python3 -m json.tool
```
