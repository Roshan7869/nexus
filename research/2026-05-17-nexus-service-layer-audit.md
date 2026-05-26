# Nexus Service-Layer Audit

Date: 2026-05-17

## Core findings

1. `nexus` had multiple routing stacks, but the live connector path usually called `route_task(..., use_embedding=False)`, so the semantic router was effectively bypassed.
2. The `frontend_ui` cluster existed, but its canonical defaults were wrong in practice:
   - `frontend_ui.default_skill` pointed at `design-html`, which was clustered as `architect`.
   - `designer` was also clustered as `architect`.
   - Result: design requests activated the UI cluster but did not surface the right design resources.
3. The repo had a package-shadowing hazard:
   - callers that injected `~/nexus/core` or `~/nexus/server` directly into `sys.path` could break `server.*` imports.
4. Tests and docs were stale relative to the live database:
   - live DB has `1686` active resources, not `1096/1097`.
   - several test assumptions were about a much older system state.

## Fixes applied

- Added canonical bundle registry in `server/service_layer.py`.
- Boosted cluster-specific service bundles inside `server/router.py`.
- Changed the unified HTTP route to use semantic routing by default in `server/unified.py`.
- Changed the OpenCode connector to import via `server.*` and to use embedding-aware routing in `opencode_connector.py`.
- Corrected cluster defaults in `server/cluster_config.py` and `server/auto_trigger.py`.
- Added `scripts/repair_service_layer.py` and applied it to repair canonical cluster assignments in `nexus.db`.
- Made `IDEEvent` construction safer in `core/learner.py`.
- Updated `tests/test_unified.py`; suite now passes.

## Architectural recommendation

Treat Nexus as a 4-layer system:

1. Intent detection
   - classify task and activate 1-2 domains
2. Service bundle activation
   - inject canonical domain bundle before generic retrieval
3. Retrieval and ranking
   - semantic search + keyword search + feedback priors
4. Execution policy
   - `INVOKE`, `REVIEW`, `SUGGEST`, `BLOCK`

This is better than pure hierarchical clustering alone. Clusters narrow the search space; bundles guarantee that the canonical tools for a domain are never silent.

## Next upgrades

1. Move all callers to package-safe imports only; stop adding `~/nexus/core` or `~/nexus/server` directly to `sys.path`.
2. Persist bundle success metrics separately from generic tool metrics.
3. Add alias tables:
   - `website design` -> `frontend_ui`
   - `figma` -> `ds-figma`
   - `audit nexus` -> `analyzer_planner`
4. Add explicit execution modes per resource:
   - `silent_service`
   - `auto_invoke`
   - `review_only`
   - `manual_only`
5. Replace stale fixed thresholds with calibrated per-cluster thresholds from routing logs.
