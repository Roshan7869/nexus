# B03 — Flow IRs: `docs/nexus-maps/*.workflow|sequence|lifecycle.json` (CREATE, LLD figures)

## GitNexus queries
```
query({search_query: "router pipeline session lifecycle task sequence"})
context({name: "unified_route_v5"}) · context({name: "LocalHarnessSession"})
```
Used: Archify SKILL.md type router + workflow-v2 layout contract (`renderers/workflow/README.md#layout-contracts`) + lifecycle column rule (main rail 0..4, event/terminal N under N+2; recoverable = `type:failure` + return transition), M03/M11-docs (state machines), `execution_plane.py` contract.

## Research-backed context
- **Archify type router (primary):** `workflow` = processes/gates/tool-calls/runbooks; `sequence` = API call order; `lifecycle` = state machines; `dataflow` = pipelines/lineage. Pick by question, never mix.
- **Cordis lifecycle (Alg5) as content source:** INACTIVE→LOADING→ACTIVE⇄UNLOADING + inertial chaining + per-iteration guard — the lifecycle IR draws exactly this; the diagram is the authored proof the code must match.
- **NEXUS LLD gap:** M-docs specify state machines in prose/pseudocode; prose drifts, validated IR freezes. B03 makes M03/M11/M07 reviewable by picture.

## Current state
No flow IRs. Mermaid in graph doc covers topology only.

## Blast radius: NONE (new docs + generated HTML)

## Detailed pseudocode (three artifacts, same loop each)
```python
flows = {
 "nexus-router-v5.workflow.json":   # L1 intent → short-circuit? → L2 top-2 → L3 fusion → L4 autocut → tools
    {"schema_version": 2, "gates": ["short-circuit (conf>θ+pool≥N)", "L4 autocut 0.5·max"], "tool_calls": ["route_task","unified_route","report_outcome"]},
 "nexus-task.sequence.json":         # IDE → MCP open → broker start → route → tools → report → belief_delta
    {"actors": ["OpenCode","nexus-mcp","broker","v5","learner"], "primary": "open→route→tools→report"},
 "nexus-session.lifecycle.json":     # INACTIVE → LOADING → ACTIVE ⇄ UNLOADING → INACTIVE
    {"rail": "0..4 main states", "recoverable": {"UNLOADING": {"type": "failure", "back": "ACTIVE"}}},
}
for name, spec in flows.items():
    write_candidate(name, spec)                                   # ≤12 nodes, showcase
    validate(type_of(name), name, "--quality showcase --json")    # 0/0 or diagnosed fix (≤2 rounds)
    deliver(...)                                                  # HTML + receipt, frozen
```

## Build steps
1. Router workflow first (highest review value: M10/M11 A/B context).
2. Session lifecycle second (M03 acceptance picture).
3. Task sequence third (M05/M06 onboarding).
4. M-docs link their figure (e.g. M11-doc → workflow HTML route view).

## Tests
Per-artifact `validate` 0/0 + `visual-check` on delivered HTML only (never on failed-candidate paths).
