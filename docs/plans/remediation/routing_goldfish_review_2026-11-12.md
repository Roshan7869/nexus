# Routing Goldfish review — 2026-11-12

Target: [A1 single-router plan](A1-single-router.md), full file. Three fresh-context EGM reviewers independently read the design critic, implementation readiness, and codebase details personas and their assigned standards. They checked the current router source and benchmark receipt. This consolidation was authored by the parent agent after checking the cited source. It is a read-only evaluation; no routing implementation was changed.

## Decision

A1 is not implementation ready. The router is operational, but the proposed tests and release gate cannot establish routing correctness. The 20-query A/B receipt reports 3/20 top-one agreement against a 95% target. A separate 105-case golden run dated 2026-11-10 reports 33/105 passed (0.3143); the plan's 0.600 baseline came from a five-case seed run dated 2026-08-17. Neither historical run is a fresh acceptance test.

## Blockers

1. **The proposed equality test is vacuous.** `A1-single-router.md:80` reads `result["results"]`, while `route_for_task` returns ranked items under `skills` (`server/router_cutover.py:147`). Both sides can be empty and equal. Require HTTP 200, nonempty `skills`, identical ordered names, and the expected router mode for the same fixed task. Cover the public MCP and broker paths too.
2. **A production endpoint bypasses the cutover router.** `server/nexus_http_daemon.py:633-675` implements `/find_skills` through `router.route_task` and writes its own log. A1 changes only `/nexus/search`. Classify all routing callers and give every public surface an explicit disposition, including `/find_skills`, `/nexus/search`, MCP `route_v3`, broker fallback, and chat classification.
3. **The quality gate is unresolved.** `evals/results/ab_benchmark_receipt.json` records `gate_pass: false` (15% versus 95% top-one agreement). `evals/results/20261110_221931_swarm-fixes-head.jsonl` reports a 0.3143 golden pass rate on 105 cases; A1's 0.600 floor refers to `evals/results/20260817_010908_seed.jsonl`, which evaluated only five cases. Re-run a representative current golden set and define an outcome-quality acceptance rule. Agreement with v4 is a divergence measure, not proof that either version is right.
4. **The search contract is undecided.** Current `/nexus/search` returns `{query, method, results}` (`server/unified.py:366-381`); A1 proposes the route shape `{task, skills, ...}`. Its `route_for_task` call would also write a routed session by default (`server/router_cutover.py:251,286`). Decide whether search remains a read-only search, becomes a versioned route alias, or receives an explicit client migration. The environment flag rollback does not restore the old response shape.

## Major findings

- **Project scope:** `_shape_v5` accepts `project` but calls `unified_route_v5(task, ...)` without it (`server/router_cutover.py:126-130`); v5 L1 hardcodes `project="default"` (`server/router_v2/v5.py:203-211`). A nondefault project test is required before promising project-aware ranking.
- **Latency and resilience:** The A/B receipt averages 8,749.8 ms for v5. A1 has no warm/cold latency or concurrency gate. `route_for_task` has no bounded fallback on v5 errors; the HTTP handler converts exceptions to 500 (`server/nexus_http_daemon.py:435-460`). Define timeouts, failure thresholds, and a rollback drill across HTTP and MCP owners.
- **Consumer contract:** v5's adapter fixes `model="sonnet"` and omits `task_type` (`server/router_cutover.py:147-160`); the broker consumes `task_type` for templates (`server/broker_runtime.py:580`). Specify the complete routing result contract before changing callers.
- **Stale plan steps:** `ROUTER_MODES` already exists (`server/router_cutover.py:49`). A1's suggested replacement changes the observable v4 mode name. The `tool_session_broker.py:289` call is an exception fallback, not the primary route. Classify each call site as production routing, classification, diagnostic, or fallback before editing.
- **Test setup:** `isolated_test_db` in `tests/conftest.py:345` uses a temporary SQLite file; A1's instruction to use an unspecified in-memory fixture does not define how v5 receives its database and resource data.

## Next decision

Set the release criterion for v5 against task outcomes on the 105-case golden set (including acceptable latency), decide the `/nexus/search` compatibility contract, and then revise A1 with non-vacuous cross-surface tests. The existing 95% v4 agreement gate must either be met or explicitly replaced with a justified outcome-quality gate before declaring cutover complete.

Verification during this review: `pytest tests/test_router_cutover_phase7.py -q` passed 8 tests. Those tests check dispatch and A/B arithmetic; they do not validate ranking relevance or all public entry points.
