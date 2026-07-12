# NEXUS Execution Plane Contract v1 (P4)

**Base URL:** `http://127.0.0.1:8080`  
**Contract id:** `nexus.execution_plane.v1`

## Endpoints

### `GET /v1/health`
Returns `version`, `git_sha`, `router_mode`, `db_ok`, `flags`.

### `POST /v1/session/open`
```json
{
  "task": "string",
  "project": "default",
  "budget": 8,
  "ide": "hermes|claude-code|opencode|codex|http|mcp",
  "use_embedding": true
}
```

Response includes:
- `session_id`
- `tools` — ranked skill/resource names (≤ budget)
- `harness_channels`, `harness_tools`, `toolsets`
- `model_hint`, `cluster`, `context_block`
- `instructions`

Side effect: `routing_log` row with `outcome=routed` (non-terminal; no Thompson).

### `POST /v1/session/{session_id}/report`
```json
{
  "outcome": "correct|wrong|partial|skip",
  "notes": "",
  "quality": 1.0,
  "skill_name": "optional-primary"
}
```

Response includes `belief_delta[]` when terminal learning applied.

### `GET /v1/session/{session_id}`
Session status + MCP proxy surface.

### `POST /v1/event` (Gap 4 — IDE feedback loop)
```json
{
  "event_type": "code_accepted|test_passed|test_failed|task_completed|...",
  "ide": "claude-code|opencode|hermes|http",
  "model": "sonnet",
  "provider": "anthropic",
  "task_type": "coding",
  "project": "default",
  "task_description": "optional",
  "metadata": {"skill_name": "optional", "session_id": "optional tool_session id"}
}
```

Alias: `POST /nexus/event`. Writes `ide_events`, may close tool sessions, calls `record_outcome`.

Python helper: `server.ide_event_client.post_event(...)` → defaults to `http://127.0.0.1:8080/v1/event`.

## Client map

| Runtime | Client |
|---------|--------|
| Python / scripts | `clients/python/nexus_client.py` |
| Hermes | `clients/hermes/nexus_session.sh` |
| AgentSpace AgentRouter | `clients/agentspace/NEXUS_AGENTROUTER.md` |
| CodeNomad | `clients/codenomad/sidecar.nexus.json` |
| Claude Code MCP | `start_tool_session` / `task_complete` (existing) |

## Flow

```
IDE/AgentRouter
   │  POST /v1/session/open
   ▼
NEXUS: route_for_task (v5) + tool_session + harness plan
   │  tools + harness_tools
   ▼
IDE executes task
   │  POST /v1/session/{id}/report
   ▼
NEXUS: record_outcome (terminal) → Thompson / edges
```
