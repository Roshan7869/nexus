# AgentSpace AgentRouter ↔ NEXUS execution plane (P4)

AgentSpace **owns** multi-runtime execution (Claude / Codex / OpenCode / OpenClaw / Hermes).  
NEXUS **owns** resource retrieval + learning.

## Integration hook (before `agent-router run`)

```http
POST http://127.0.0.1:8080/v1/session/open
Content-Type: application/json

{
  "task": "<user task>",
  "project": "<workspace id>",
  "budget": 8,
  "ide": "http"
}
```

Response fields for AgentRouter:

| Field | Use |
|-------|-----|
| `session_id` | Store on task record; report later |
| `tools` | Skill/tool allow-list for the harness |
| `harness_channels` | e.g. `["research","memory"]` |
| `harness_tools` | MCP tools to attach if research/browse |
| `model_hint` | Optional model suggestion |
| `context_block` | Inject into agent system/context |

## After harness completes

```http
POST http://127.0.0.1:8080/v1/session/{session_id}/report
Content-Type: application/json

{
  "outcome": "correct",
  "notes": "tests passed / PR merged",
  "quality": 1.0,
  "skill_name": "optional-primary-skill"
}
```

`belief_delta` in the response is the learning signal for ops dashboards.

## Pseudocode for AgentSpace daemon

```ts
const open = await fetch(`${NEXUS_URL}/v1/session/open`, {
  method: "POST",
  body: JSON.stringify({ task, project, budget: 8, ide: "http" }),
});
const session = await open.json();
// pass session.tools + session.harness_tools into AgentRouter env / MCP filter
const result = await agentRouter.run({ harness, prompt: task, tools: session.tools });
await fetch(`${NEXUS_URL}/v1/session/${session.session_id}/report`, {
  method: "POST",
  body: JSON.stringify({
    outcome: result.ok ? "correct" : "wrong",
    notes: result.summary,
  }),
});
```

## Health gate before task queue

```http
GET /v1/health  →  router_mode == unified_v5 && db_ok
```
