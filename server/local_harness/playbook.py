"""Durable playbook + ephemeral task brief (cf. OpenResearch SYSTEM_PROMPT.md).

Playbook carries every-turn context only. Task procedures live in skills.
"""
from __future__ import annotations

PLAYBOOK_TMPL = """# NEXUS local harness — {project}
You are a NEXUS-routed agent. Project: **{project}** (id `{project_id}`).
Working scope: **your session only** — tools listed below, nothing else.
Artifacts: `{artifacts}` — durable outputs (reports, figures, CSVs) live here.
Response contract: final answer states outcome + files touched; no tool-discovery chatter.
Routing: need another capability? ask for it by NAME (skill routing); do not improvise tools.
{state_block}""".strip()


def render_playbook(project: str, project_id: str, state: str, artifacts: str) -> str:
    return PLAYBOOK_TMPL.format(project=project, project_id=project_id,
                                state_block=state, artifacts=artifacts)


def build_task_brief(query: str, route: dict, tools: list[str],
                     harness_tools: list[str] | None = None) -> str:
    lines = [f"Task: {query}",
             f"cluster={route.get('cluster')} task_type={route.get('task_type')}",
             f"tools({len(tools)}): {', '.join(tools)}"]
    if harness_tools:
        lines.append(f"harness: {', '.join(harness_tools[:16])}")
    return "\n".join(lines)


def build_context_block(project: str, project_id: str, state: str, artifacts: str,
                        query: str, route: dict, tools: list[str],
                        harness_tools: list[str] | None = None) -> str:
    return (render_playbook(project, project_id, state, artifacts)
            + "\n\n---\n\n"
            + build_task_brief(query, route, tools, harness_tools))
