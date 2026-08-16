# NEXUS Reality Check & Realistic Path to Vision (2026-08-17)

Method: mattpocock `research` skill (primary sources) + `grilling` stress-test, on
top of the vision-vs-reality audit (6 works / 3 partial / 4 dormant / 2 fiction).
Sources: LiteLLM, OpenRouter, semantic-router, Letta, mem0, continue.dev launch
histories; LiteLLM proxy/OpenRouter docs; Aider repo-map; Claude Code memory/hooks;
AGENTS.md standard; OpenHands/Copilot/Sweep execution models.

## The actual reality (uncomfortable, evidence-backed)

1. **NEXUS already shipped its real v1 and doesn't know it.** Every successful
   comparable (LiteLLM = two functions; mem0 = add()/search(); semantic-router =
   utterances+threshold) launched as a tiny callable core. NEXUS's equivalent —
   `route(task) → ranked skills` + `report(outcome) → belief update` — is BUILT,
   LIVE, and used daily by two IDEs across three projects. That IS the product.
   The README just sells a different one (a gateway) that was never plugged in.
2. **The gap between vision and reality is not effort-shaped, it's scope-shaped.**
   The dead surface (gateway unserved, dashboard, autopilot theater, ambient
   injection) represents the "architecture-first" half. Every studied winner did
   the opposite: thin core → real users → grow.
3. **The laptop is not the constraint; the architecture is.** The proven mechanisms
   for NEXUS's four gaps are all CPU-cheap: repo-map (tree-sitter + PageRank, no
   embeddings), AGENTS.md markdown memory, subprocess exit-code verification,
   single-process YAML gateway.

## What the vision becomes (reframed)

> **NEXUS: the routing + memory layer that every coding IDE shares.**
> It ranks the right skills for a task, learns from outcomes, carries project
> context across IDE switches via open standards (MCP + AGENTS.md), and never
> claims success it didn't verify.

Dropped from the vision: gateway-first identity, ambient everything, autonomy
theater, benchmark blending, dashboards beyond one page, multi-cloud.

## The realistic path — 4 weeks to honest v1 (solo dev, evenings)

**Week 1 (60% done)** — Measurement + convergence. [current roadmap]
Finish evals (curate goldens, holdout, CI), runbook 2.3, API key, smoke green.

**Week 2 — Context injection, REPLACED with the proven cheap mechanism.**
Original plan: harness fan-out → TOIN → inject. Evidence says start simpler:
- **Aider-style repo-map**: tree-sitter symbols + reference ranking → ~1k-token
  map of the project, injected on every route. Deterministic, no embeddings,
  minutes on 2 cores. (aider.chat/docs/repomap.html)
- **AGENTS.md as the project-memory interchange** (60k+ projects, adopted by
  Codex/Cursor/Copilot/Gemini/Zed/OpenCode): NEXUS reads AGENTS.md at session
  start and appends durable notes to a git-committed memory file (index + topics,
  Claude-Code-style). DB stays the index; markdown becomes the portable format.
  This IMPLEMENTS unified-MCP L1/L2 on the open standard instead of a proprietary
  surface. Harness fan-out stays behind the same `assemble()` seam for
  knowledge-base queries (architecture candidate #1).
- The existing context-harness becomes the deep module behind the seam; repo-map
  is its first adapter. Measured by the eval harness (judge subset).

**Week 3 — Learning honesty + per-project brains.**
- U2 BeliefStore extraction + per-project beliefs with global fallback
  (architecture candidate #3 — the forcing function arrives with it).
- Autopilot: either delete, or reduce to the verified loop used by Claude Code
  hooks / Copilot agent: run the project's verify command in a subprocess; gate
  completion on exit code; quote the code in the trace. Never trust a model's
  success claim. No Docker needed — subprocess is enough on 2 cores.

**Week 4 — Serve + ship.**
- Gateway: LiteLLM-proxy shape — one process, YAML alias table, ordered fallbacks;
  no Redis/DB/workers (docs.litellm.ai/docs/proxy/quick_start). It's already
  implemented; the work is deletion, not construction.
- README rewritten to the 6 true capabilities + the reframed vision.
- Ship-shape: `pip install`-able / one MCP config block + a demo GIF. Postable.

## Permanent kill list (delete on sight, do not refactor)

- Autonomy theater (execute_task_run's outcome=None stamping) — week 3 decision.
- OptiLLM approaches surface, benchmark blending, Redis/p99<50ms Phase-4
  performance work, plugin API, Prometheus, backup CLI — none earned users yet.
- The second start-server path once the cutover lands (one `nexus serve`).

## Definition of "implemented into reality"

1. Switch IDE mid-task → `project_resume` (AGENTS.md + memory + beliefs) returns
   the working state. 2. A routed suggestion is wrong → user corrects → next
   routing measurably changes (already true; keep true per-project). 3. Context
   block contains a repo-map + project notes the IDE agent actually uses. 4. Any
   "executed" trace quotes a real exit code. 5. A stranger can install it from
   one config block and see 1-4 in a 60-second GIF.
