<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **nexus** (11120 symbols, 18515 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> Index stale? Run `node .gitnexus/run.cjs analyze` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? `npx gitnexus analyze` (npm 11 crash → `npm i -g gitnexus`; #1939).

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows. For regression review, compare against the default branch: `detect_changes({scope: "compare", base_ref: "main"})`.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `query({search_query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `context({name: "symbolName"})`.
- For security review, `explain({target: "fileOrSymbol"})` lists taint findings (source→sink flows; needs `analyze --pdg`).

## Never Do

- NEVER edit a function, class, or method without first running `impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `rename` which understands the call graph.
- NEVER commit changes without running `detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/nexus/context` | Codebase overview, check index freshness |
| `gitnexus://repo/nexus/clusters` | All functional areas |
| `gitnexus://repo/nexus/processes` | All execution flows |
| `gitnexus://repo/nexus/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->

## Skill routing (NEXUS)

Match the task to a skill first, then follow that skill's steps. Skills live in `skills/<name>/SKILL.md`.

| Intent | Skill |
|--------|-------|
| Any coding task routed through NEXUS | `nexus-router` — `task_start` → use only the listed tools → `task_complete` |
| Open, report, or reconcile an IDE-local harness session (sites bound per `task_type`) | `nexus-harness` — `local_harness_open(task_type=…)` → `local_harness_report` (returns `sites_released`) → `local_harness_reconcile` |
| Change routing weights or logic | `nexus-eval` — A/B gate (`router_ab_suite`, `route_ab_compare`) must pass before proposing |
| Over-engineering review of a diff | `ponytail-review` |
| Whole-repo over-engineering audit | `ponytail-audit` |
| Harvest `ponytail:` deferral comments | `ponytail-debt` |

Rules:
- Never improvise tools outside a skill's listed set.
- Never skip the closing step (`task_complete` / `local_harness_report`); learning depends on it.
- No default-weight change without a passing A/B receipt.
- Session start: follow the NEXUS Session Protocol above (`project_attach` → `project_resume`).
