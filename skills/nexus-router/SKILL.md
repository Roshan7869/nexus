---
name: nexus-router
description: Discover and invoke canonical tools, skills, and actions through NEXUS with advisory recommendations.
---

# nexus-router

Shared Capability Workflow:
1. `task_start(query, project)` → open session, receive session ID, advisory `visible_tools`, and discovery handles.
2. Recommendations are suggestions; you are NOT restricted to them.
3. If another capability is needed at any time, search the catalog via `capability_search(query)`.
4. Inspect descriptors via `capability_get(capability_id)` and read complete instructions via `skill_read(capability_id, path="SKILL.md")`.
5. Invoke shared tools via `capability_invoke(capability_id, method, args, session_id)`.
6. For native harness actions, request envelopes and execute through the owning adapter.
7. `task_complete(session_id, outcome, summary)` — close the session and record verified outcome (correct | wrong | partial | skip).

Rules:
- Never assume absence from recommendations means a capability is unavailable; always search when needed.
- Recommendations are advisory; authorization is enforced centrally by policy.
- Always close sessions to maintain belief updates and release resources.
