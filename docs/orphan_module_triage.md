# Orphan Module Triage

**Generated**: 2026-07-01
**Source**: import-graph analysis of `server/` + `core/`
**Method**: A module is "orphan" if no static `from X import` / `import X`
statement references it. Note: dynamic imports (`__import__()`,
`importlib.import_module()`) are NOT detected, so some "orphans" may
actually be loaded at runtime.

## Category A: Genuinely dead (safe to delete)

| Module | Size | Reason |
|--------|------|--------|
| `server/feedback` | 2.2KB | Stub file, no functionality |
| `server/executor_mcp` | 12.9KB | Superseded by `nexus-mcp-enhanced.py` |
| `server/nexus_daemon` | 3.5KB | Old daemon, replaced by `nexus_http_daemon` |
| `server/mcp_v2_server` | 20.9KB | Stale v2 MCP server (replaced by enhanced) |
| `server/mcp_v2_manifest` | 3.3KB | Manifest for the stale v2 server |
| `server/numpy_router` | 10.7KB | Experimental, never adopted |
| `server/learning_faithful` | 9.6KB | Duplicate of `learning.py` |
| `server/efc` | 6.9KB | Empty Feature-Class container, never used |

**Action**: Delete. Saves ~70KB. Risk: very low (no imports).

## Category B: Dynamic-load entrypoints (NOT actually orphan)

These are loaded by `__import__()` or `importlib` from other modules.
Listed as orphan only because static analysis missed the dynamic load.

| Module | Size | Loaded by |
|--------|------|-----------|
| `server/context_harness.fusion` | 5.9KB | `context_harness.server` |
| `server/context_harness.engine` | 12.7KB | `context_harness.server` |
| `server/context_harness.rate_limited_http` | 3.1KB | `arxiv_search` (just added) |
| `server/context_harness.channels.*` | ~80KB total | `context_harness.server` |
| `server/integrations.rate_limited_proxy` | 8.2KB | (just added, optional consumer) |
| `server/resilience.circuit_breaker` | 16.3KB | `ruflo_proxy.py` (confirmed) |
| `server/maintenance.resource_validator` | 8.0KB | (orphan, needs wiring) |
| `server/communication.notifier` | 8.2KB | `learning.py:307` (confirmed) |
| `server/dashboard.server` | 5.8KB | supervisord program (PID 275322) |
| `server/nexus_http_daemon` | 25.5KB | supervisord program (PID 1429) |

**Action**: Leave as-is. They are wired at runtime.

## Category C: Class registry / plugin systems (orphan by design)

These define classes/registries that other modules look up by name.

| Module | Size | Notes |
|--------|------|-------|
| `server/coach.*` (8 files) | ~70KB | Coach system — dynamic registry |
| `server/autonomous_pipeline` | 17.2KB | Pipeline class definitions |
| `server/orchestrator_tools` | 70.7KB | Tool registry |

**Action**: Leave. They are loaded by name lookup.

## Category D: Test/CLI scripts (intentionally not imported)

| Module | Size | Purpose |
|--------|------|---------|
| `server/test_v3` | 17.1KB | Test script |
| `server/scripts.calibrate_theta` | 11.5KB | One-off calibration |
| `server/scripts.eval_faiss` | 5.4KB | FAISS evaluation |
| `server/scripts.fix_avg_reward_db` | 1.7KB | DB repair utility |
| `server/scripts.phase1_data_hygiene` | 19.9KB | Phase 1 cleanup |
| `server/scripts.phase2_tier_classify` | 3.3KB | Phase 2 tool |
| `server/scripts.phase4_build_faiss` | 3.5KB | Phase 4 tool |
| `server/scripts.phase5_precondition_gate` | 2.3KB | Phase 5 tool |
| `server/scripts.regenerate_embeddings` | 3.9KB | Re-embed all chunks |
| `server/scripts.enrich_skills_from_disk` | 14.1KB | Bulk skill enrichment |
| `server/scripts.generate_eval_dataset` | 3.0KB | Eval data builder |

**Action**: Move to `scripts/` directory. Not orphans — they're meant to be
run directly, not imported.

## Category E: Ingest pipeline (planned but not wired)

| Module | Size | Notes |
|--------|------|-------|
| `server/ingest.cli_tool` | 4.1KB | CLI ingest entrypoint |
| `server/ingest.git_repo` | 4.4KB | Git repo ingester |
| `server/ingest.http_doc` | 6.1KB | HTTP doc ingester |
| `server/ingest.linker` | 13.9KB | Cross-resource linker |
| `server/ingest.mcp_server` | 4.0KB | MCP ingest server |
| `server/ingest.obsidian_vault` | 6.2KB | Obsidian vault ingester |
| `server/ingest.okf_ingest` | 5.5KB | OKF format ingester |
| `server/ingest.pipeline` | 7.0KB | Main ingest pipeline |

**Action**: Wire or document. This is the "no automated ingestion
pipeline" the gap report mentioned — but the modules EXIST, they just
aren't connected to the running `watch-inbox.sh` script.

## Category F: Plan execution (development artifacts)

| Module | Size | Notes |
|--------|------|-------|
| `server/nexus_plan_executor` | 16.9KB | Plan execution (NEXUS plan system) |
| `server/plan_tracker` | 9.6KB | Plan progress tracking |
| `server/graphify_nexus_bridge` | 20.0KB | Graph knowledge bridge |
| `server/ide_manager` | 7.2KB | IDE event manager |
| `server/implicit_outcomes` | 8.9KB | Implicit signal extraction |
| `server/research_pipeline` | 11.8KB | Research workflow |
| `server/router_v2.scoring` | 7.7KB | V2 router scoring |
| `server/rl.outcome_log` | 9.6KB | RL outcome log |
| `server/rl.thompson` | 6.0KB | RL Thompson sampling |
| `server/metrics.graded` | 7.6KB | Graded metrics |

**Action**: Document or archive. Some may be partially used via dynamic
load. Don't delete without confirmation.

## Summary

| Category | Modules | Action |
|----------|---------|--------|
| A: Dead | 8 | Delete (saves ~70KB) |
| B: Dynamic | 10+ | Leave (wired at runtime) |
| C: Registry | 10+ | Leave (by design) |
| D: Scripts | 11 | Move to `scripts/` |
| E: Ingest | 8 | Wire or document |
| F: Plans/experiments | 10+ | Triage individually |

**Recommended immediate action**: Delete Category A (8 files, ~70KB) and
move Category D (11 files) to `scripts/`. Total cleanup: ~120KB and
clearer module structure.

**DO NOT** attempt to delete Category B, C, or E without dynamic-load
testing — they may be loaded by `__import__()` or `importlib`.
