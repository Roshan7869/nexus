# A03 — `server/local_harness/playbook.py` (CREATE, durable playbook + task brief)

## GitNexus queries
```
context({name: "ToolSessionBroker"}) · query({search_query: "context block build session prompt"})
```
Used: `third_party/OpenResearch/SYSTEM_PROMPT.md:1-98` (full playbook: `{token}` substitution, durable-only content, per-harness channel, task procedures live in skills), `read tool_session_broker.py:124-132` (`_build_context_block` call site), M03/M07-docs.

## Research-backed context
- **OpenResearch SYSTEM_PROMPT.md:1-14 (primary):** playbook *"carries only durable context needed every turn: identity, project facts, project state, the chat response contract, shared execution policy, and skill routing. Task-specific procedures live in the native skills."* Rendered per session with `{token}` substitution (project facts, state, compute default, artifacts path); leading comment stripped at render.
- **OpenResearch SKILL.md modules list:** `orx skill <name>` lazy-loads procedures — the reason the playbook stays small. NEXUS mirror: `find_skills` + bundles ARE the procedure store; playbook must reference, never inline.
- **Tencent Hunyuan-DiT lesson** (dual encoders + caption-refine): playbook = stable prior, task brief = refined observation. Keep the two on separate refresh cadences (playbook per project, brief per session).

## Current state
`_build_context_block(project, query, route, tools)` mixes durable + ephemeral text in one blob; grows with every feature; no template, no tests.

## Blast radius: NONE (new file; broker adopts it in one guarded call-site change)

## Detailed pseudocode
```python
PLAYBOOK_TMPL = """# NEXUS local harness — {project}
You are a NEXUS-routed agent. Project: **{project}** (id `{project_id}`).
Working scope: **your session only** — tools listed below, nothing else.
Artifacts: `{artifacts}` — reports/figures/CSVs live here (cf. orx-reports rule).
Response contract: final answer states outcome + files touched; no tool discovery chatter.
Routing: need another capability? ask for it by NAME (skill routing); do not improvise tools.
{state_block}""".strip()
# NOTE: no task procedures here — they live in skills (find_skills), loaded per task.

def render_playbook(project: str, project_id: str, state: str, artifacts: str) -> str:
    return (PLAYBOOK_TMPL.format(project=project, project_id=project_id,
                                 state_block=state, artifacts=artifacts))

def build_task_brief(query: str, route: dict, tools: list[str], harness_tools: list[str]) -> str:
    # ephemeral half: task + cluster/task_type + visible tools + harness tools + budget
    lines = [f"Task: {query}", f"cluster={route.get('cluster')} task_type={route.get('task_type')}",
             f"tools({len(tools)}): {', '.join(tools)}"]
    if harness_tools: lines.append(f"harness: {', '.join(harness_tools[:16])}")
    return "\n".join(lines)

def build_context_block(project, project_id, state, artifacts, query, route, tools, harness_tools) -> str:
    return render_playbook(...) + "\n\n---\n\n" + build_task_brief(...)
```

## Build steps
1. Write module; snapshot the exact current `_build_context_block` output for 3 fixtures BEFORE switching (golden test).
2. Broker call-site: use new builder behind `NEXUS_PLAYBOOK_V2=1` (default 0 → legacy path, byte-identical).
3. Promote default after 3-fixture parity + 20-query A/B neutral.

## Tests
`test_playbook_has_no_procedures` (assert no "how to" verbs blocklist), `test_golden_parity_v1`, `test_brief_lists_tools`.
