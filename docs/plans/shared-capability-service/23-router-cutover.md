# server/router_cutover.py

Action: **Modify**. Status: Proposed execution instructions, design only.

## Purpose

Keep one compatibility entry for legacy ranking callers while adopting the shared catalog and advisory recommender. This is the authoritative adapter used by the daemon, unified HTTP surface, MCP compatibility functions and broker; it does not own provider processes.

## Interfaces and contract

Preserve route_for_task(task, project, max_results, flag, open_session, ide). Add an optional keyword-only seed for controlled evaluation. Explicit legacy flags keep their diagnostic meaning. Central shared_capability_mode governs legacy, shadow or serve behavior; do not silently reinterpret USE_UNIFIED_ROUTER values.

Resolve injected shared-service handles; never import the HTTP app to get them. The capability recommender must not call route_for_task, so this adapter introduces no recursion. New capability API requests call the recommender directly.

For compatibility responses keep task, project, skills, router_mode, method, latency and session correlation fields. Attach canonical_id, capability_version and catalog_revision to every returned skill. Translate only kind=skill into the skills array; expose executable tool recommendations separately. Preserve task_type and model selection via the existing classification contract with write_feedback=False; do not invent task_type=general or hardcode sonnet merely to fill missing fields. If the classifier cannot provide a required result, expose classification_unavailable and let each consumer use its documented fallback.

The supplied project must reach context creation, relevance scoring and policy evaluation. An echoed project field alone does not prove project-aware ranking.

## Algorithmic pseudocode

The following is design pseudocode.

```text
ROUTE_FOR_TASK(task,project,max_results,flag,open_session,ide,seed):
  mode = central_config.shared_capability_mode()
  if explicit diagnostic flag supplied:
    run corresponding legacy diagnostic adapter with no quality updates
  else if mode=legacy:
    run current ranking contract
  else if mode=shadow:
    run current ranking; run shared recommendation as isolated comparison
    record comparison, not success; return current ranking
  else:
    ctx = trusted caller context(principal,project,harness,versioned niche)
    recommendation = shared_recommender.recommend(ctx,task,max_results,seed)
    result = convert_to_legacy_shape(recommendation)
    classification = existing_pure_classifier(task,project)
    attach explicit classification result or unavailable reason
  if open_session:
    create one nonterminal correlation record with unique request key
    attach record ID
  return result
```

Do not call an old ranking path with feedback enabled and then open a second routing record. New capability invocations and rewards remain owned by capability_events. A legacy route query never earns a capability reward.

## File integration instructions

Daemon /route and /find_skills, unified /nexus/route and the compatibility broker must use this adapter for ranking. Their full catalog search/get/invoke functions use the new capability API. /nexus/search retains its read-only catalog conversion. Chat task classification remains a separate classifier call. MCP route_v3 is explicitly diagnostic and labelled as such; reflection ranking callers receive a documented legacy/internal disposition.

Update plans 10 and 14 with this dependency when implementing. This plan resolves their phrase “compatibility adapter”: it means route_for_task in this file, not another ranking wrapper in each endpoint.

## Acceptance checks

- All production recommendation endpoints use the same canonical ranking service in serve mode.
- Two nondefault project fixtures prove context reaches ranking.
- Fixed seed and identical context produce identical nonempty canonical recommendations across HTTP, MCP and broker.
- A returned task_type is the classifier's actual value; unavailable classification is observable.
- open_session=False writes no routing/session/reward rows, including legacy diagnostics.
- open_session=True creates one correlated nonterminal record; it never double-counts feedback.
- Mode rollback restores legacy response behavior while preserving new event/version tables.

Run upstream impact on route_for_task, _shape_v4, _shape_v5, compare_routers and run_ab_suite. Verify all callers before edits. Existing A/B comparison remains a divergence diagnostic; the new outcome-quality gate is defined in plan 16.

## Dependencies

Requires plans 02–05 and 21. Implement before the endpoint changes in 10–14. Extend test plans 18 and 20 to cover the compatibility contract. Keep the current flag-dispatch tests and add assertions for the new central mode.
