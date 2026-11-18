# B01 — `docs/nexus-maps/nexus-runtime.architecture.json` (CREATE, validated runtime map)

## GitNexus queries
```
query({search_query: "architecture map runtime topology"}) · context({name: "get_process_status"})
```
Used: `third_party/Archify/archify/SKILL.md:1-60` (fast authoring path: 1 schema + 1 example → candidate FIRST → validate → deliver; ≤12 primary nodes; showcase profile; freeze on pass), `archify/schemas/architecture.schema.json` + `common.schema.json`, `docs/nexus_docs_code_graph.json` hub degrees (node shortlist source), `docs/process_topology.md` (ownership facts).

## Research-backed context
- **Archify SKILL.md authoring path (primary law):** *"Artifact first: the next tool action must write the candidate… Start with one clear main path, short side branches, sparse labels, and at most 12 primary nodes… A passing final validation freezes the candidate: never edit it afterward."* Their invoke prompt adds: *"8–12 core components, one primary path… supporting detail in cards instead of adding more edges."*
- **Archify PRODUCT.md #1 truth-before-spectacle + #5 portable proof:** every edge from graph-verified evidence; deliverable = self-contained HTML + receipt, no hosted runtime.
- **NEXUS RC2** (process ownership not load-bearing): the map's highest-value job is making split ownership legible (systemd vs supervisord vs IDE) — the exact confusion `process_topology.md` exists to dispel.

## Current state
Only hand Mermaid in `nexus_docs_code_graph.md` (unvalidated, unpresentable). No `docs/nexus-maps/`.

## Blast radius: NONE (new docs + generated HTML; no code)

## Detailed pseudocode (authoring procedure, executed by agent)
```python
# inputs: hub degrees from nexus_docs_code_graph.json (TOP_CODE_IN) + process_topology.md ownership table
nodes = [  # 10 primaries, one path first
  "OpenCode (IDE, stdio owner)", "nexus-mcp-enhanced.py", "tool_session_broker.py",
  "execution_plane.py (:8080)", "router_v2/v5.py", "core/learner.py (Thompson)",
  "context_harness L0-L3", "db/nexus.db (SQLite SoT)", "harness bridge (27 tools)",
  "dashboard :8765 (supervisord)"]
primary_path = ["OpenCode", "nexus-mcp", "broker", "v5", "tools", "report→learner"]
# side branches: dashboard observes; harness feeds sessions; SQLite backs all
# cards (not edges): env passthrough (.mcp.json), ownership table, flag defaults
candidate = {"type": "architecture", "meta": {"quality_profile": "showcase"}, "nodes": ..., "edges": primary_first}
# loop (their 2-round rule):
run("validate architecture candidate.json --quality showcase --json")  # → 0 errors/0 warnings or diagnosed fix
run("deliver architecture candidate.json nexus-runtime.architecture.html --quality showcase --json")
# freeze: commit JSON + HTML + receipt; later edits = nexus-runtime.v2 (never mutate)
```

## Build steps
1. Read `schemas/architecture.schema.json` + `common.schema.json` + ONE example (shape only, not facts).
2. Write candidate with graph-hub nodes; validate; fix only diagnosed subjects; deliver.
3. Commit triple (JSON + HTML + receipt) + loop #23 (`archify_adaptation.md` ↔ maps) to graph JSON.

## Tests
`validate` showcase 0/0 (machine gate); `check output.html`; reviewer reads primary path in <60s (human gate).
