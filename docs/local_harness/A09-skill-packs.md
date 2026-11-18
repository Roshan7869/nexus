# A09 — `skills/nexus-*/SKILL.md` packs (CREATE, outside-in validation)

## GitNexus queries
```
query({search_query: "skill resource bundle find_skills"}) · context({name: "bundle_for_cluster"})
```
Used: `third_party/OpenResearch/SKILL.md` (cardinal rules + module index + `orx skill <name>` loader), `.claude/skills/skill-builder/SKILL.md` (house packaging format), `server/service_layer.py:SERVICE_BUNDLES`, B5-install note in archify doc (same `skills add` channel).

## Research-backed context
- **OpenResearch `orx-agent-delegation` + `orx skill` loader (primary):** helper sessions + lazily-loaded procedure modules keep the playbook small (A03's premise). Skills carry *procedures*; the harness carries *state*.
- **Tencent MT-Chimera lesson (weak-to-strong):** sample cheap candidates, fuse with a synthesizer — a NEXUS skill pack taught to an outside agent tests whether our tool descriptions survive naive use (weak) before we trust them in routing (strong).
- **NEXUS RC7:** if an OpenResearch session can drive NEXUS skills via `orx skills add` with no NEXUS maintainer in the room, the contract is real.

## Current state
No exportable packs. `resources`/`bundle_membership` serve only internal `find_skills`.

## Blast radius: NONE (new `skills/` dir; zero repo-code imports)

## Detailed pseudocode (pack layout ×3)
```
skills/nexus-router/SKILL.md      # route-first: task_start → visible_tools only → task_complete
skills/nexus-eval/SKILL.md        # agreement gate: route_ab_compare + 20-query suite + report_outcome
skills/nexus-harness/SKILL.md     # open/report/reconcile + manifest reading (A07)
```
Each SKILL.md (follow skill-builder format):
```markdown
---
name: nexus-router
description: Route any coding task through NEXUS before acting — get the tool working set, use only it, report back.
---
# nexus-router
1. `task_start(query, project)` → read `visible_tools` + `instructions`.
2. Use ONLY listed tools for this task.
3. `task_complete(session_id, outcome, summary)` — outcome ∈ correct|wrong|partial|skip.
Rules: never improvise tools; never skip step 3 (learning dies without it).
```

## Build steps
1. Write 3 packs (one screen each — procedures, not essays).
2. Validate outside-in: `orx skills add <path>` in an OpenResearch session; drive one task per pack; record friction as M-doc amendments.
3. Publish criteria: all 3 tasks complete with no maintainer hints.

## Tests
Manual acceptance (outside-in runs) + `skills/README.md` index; recorded transcripts linked in PR.
