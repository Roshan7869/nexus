# File-by-File Component Audit — NEXUS

Date: 2026-11-10 · Branch `second-brain-v2` · Commit `6a3abaa` · GitNexus index: up to date at this commit.
Scope: 213 Python files under `server/`, `core/`, `providers/`, `scripts/`. Four EGM Goldfish reviewers, one per component group, each rated every file in its group. Importer counts are name-based or AST-based, so dynamic imports can be missed.

Verdict key: **KEEP** correct and needed · **FIX** specific defect · **MERGE** duplicates another file · **DELETE** no live importer and no entry point · **UNCLEAR** needs a decision.

Groups: G1 routing and learning (38) · G2 context harness and ingest (64) · G3 harness, broker and core (42) · G4 transport, ops, coach, scripts (69).

## 1. Net issue areas (worst first)

### A. Broken runtime or false results (fix first)

| # | Issue | Where | Sev |
|---|---|---|---|
| A1 | Routed chat requests raise `AttributeError`: `learner.get_route` and `learner.get_learning_stats` do not exist on `NexusLearner`. `/nexus/beliefs` also fails. | `core/server.py:100`, `:349` (verified) | High |
| A2 | Retry decisions use a random simulated outcome | `server/self_reflector.py:242, :275-290` (verified) | High |
| A3 | Broker records a fake "executed" state with `outcome=None`; timeout sweeper hard-codes `"correct"`; auto-execute defaults on | `broker_runtime.py:555, :643, :701-730, :1078-1086` | High |
| A4 | End-of-session default outcome is `"correct"` with no evidence | `tool_session_broker.py:185`; `harness_mcp_bridge.py:166` | High |
| A5 | Orchestrator marks phases done without running them; verification returns a constant `True`; approval gate not implemented | `server/orchestrator.py:298-336, :380` | High |
| A6 | Single-verdict routing grade from a 56-entry lookup table | `server/router.py:392-414` | High |
| A7 | CLI feedback path cannot import `learning` (bare import, `sys.path` wrong) and never finds rows marked `routed` | `server/feedback.py:18, :35` | High |
| A8 | `ended_at` set on still-active sessions; quality `0.0` becomes `1.0` | `execution_plane.py:243-246, :341` | High |
| A9 | JSON-RPC replies not matched to request ids; stale reply returned to the next call; stderr pipe never drained | `ruflo_proxy.py:61-92, :149` | High |
| A10 | Store reports `"stored"` but stores nothing | `context_harness/gcode_backend.py:71-74` | High (latent) |
| A11 | `sites.py`: insert failure after broker row is persisted leaves an orphan active session | `local_harness/sites.py:85-99` ↔ `session.py:142` | Med |
| A12 | Duplicate or fabricated data written to the live DB and into the learning loop | `scripts/seed_benchmarks.py`, `scripts/seed_routing_log.py` | High |
| A13 | Global learnings injected into project sessions (beliefs leak across projects) | `tool_session_broker.py:366-372`; `project_continuity.py:149-173` | Med |

### B. Security (exec, network, exposure)

| # | Issue | Where | Sev | Live? |
|---|---|---|---|---|
| B1 | **SSRF**: fetch with no private-address check, redirects followed | `channels/target_site.py:97, :136`; `scrapling_fetch.py:101`; `scrapling_spider.py:118`; exposed via `server.py:138, :594` | High | Live once context_harness runs |
| B2 | Unified server binds `0.0.0.0` with no auth; `/ide/launch`, `/nexus/ops/bootstrap` start processes | `unified.py:1171`; `start.sh:19` | High | Latent (not running) |
| B3 | `ruflo_invoke` forwards any tool name to ruflo (includes terminal tools) | `nexus-mcp-enhanced.py:1439-1455` | High | Local MCP |
| B4 | Caller-chosen program and session names reach `supervisorctl` and `tmux`; `cmd` runs through a shell in `tmux new-session` | `process_supervisor.py:183-445`; `ide_manager.py:256-275` | High | Via B2 |
| B5 | `gcode` `os.popen` on an f-string | `gcode_backend.py:48` | High | Dormant (`GCODE_BINARY=None`) |
| B6 | Harvested and ingested text stored with no trust marker, later read into LLM context | `research_ingest.py:85, :118, :160`; `nexus_bridge.py:81`; `engine.py:335-336`; `server.py:842` | Med | Live |
| B7 | Symlink escape in local ingest | `ingest/local_dir.py:37-50` (live); `obsidian_vault.py:84, :99-104` (dead) | High | Live (`local_dir`) |
| B8 | `scrapecraft` reads `OPENAI_API_KEY` from `~/.bashrc` and sends it to localhost; caller URL fetched unchecked; page text goes into the prompt unescaped | `scrapecraft_pattern.py:260-314` | Med | Live |
| B9 | `forge` matches tmux sessions by prefix: project `a` can stop `a-b` | `forge.py:219-247` | Med | Latent |
| B10 | `harness_mcp_bridge` passes client `kwargs_json` straight through | `harness_mcp_bridge.py:257-260` | Med | Live |
| B11 | `NCBI_API_KEY` in URL query; httpx request logs at INFO | `pubmed_search.py:28-41`; `nexus_http_daemon.py:49` | Low | Live |

### C. Architecture drift (the C1 problem, now confirmed in many places)

- **Routers:** v4 (`router.py`), v2/v5 (`router_v2/`), v3 (`tool_attention.py`), context-harness router (`context_harness/router.py`), `content_router.py`, dispatcher planner (`channels/dispatcher.py`), `numpy_router.py`, `cross_type_router.py`. `context_assembly.py:44` routes into `router_v2` directly. `router_cutover.py` picks between three, and `get_router_flag` falls back to `"0"` on invalid input.
- **Reflection:** four implementations (`reflection_loop.py`, `self_reflector.py`, `reflection.py`, `rl_feedback_loop.py`).
- **RRF fusion:** duplicated in `router_v2/__init__.py`, `context_harness/backend_protocol.py` and `fusion.py`.
- **FAISS and embedding writers:** `scripts/rebuild_faiss.py` (temp file then rename, keep), `server/scripts/phase4_build_faiss.py` (in-place, `DELETE` with no backup), `server/scripts/regenerate_embeddings.py` (in-place).
- **Two IDE event collectors:** `core/ide_events.py` and `core/learner.py:529`.
- **Two writers into core tables:** `coach/bridge.py` upserts Coach cache into NEXUS tables.
- **Dashboard duplicated:** `dashboard/server.py` repeats endpoints in `unified.py:887, :967`.
- **Two weight schemes:** `router_v2/scoring.py` (0.40/0.15/0.25/0.20) vs `fusion.py:329`.
- **Two vector backends:** `numpy_router.py` says it replaces FAISS; `tool_attention.py:890` still imports `faiss`; `router_v2` uses `sqlite-vec`.

### D. Hygiene (non-blocking, but breaks the 500-line rule or import paths)

- Over 500 lines: `learning.py` (1,496), `router.py` (1,425), `nexus-mcp-enhanced.py` (1,760), `unified.py` (1,171), `broker_runtime.py` (1,197), `context_harness/server.py` (1,213), `router_v2/__init__.py` (674), `rl_feedback_loop.py` (656), `compression.py` (668), `cache.py` (919), `catalog_lifecycle.py` (841), `bridge.py` (807), `tool_attention_v4.py` (790).
- Bare imports that fail under the package path: `orchestrator.py:102, :120`, `cross_type_router.py:129`, `tool_attention.py:895-896`, `ingest/linker.py:34-35`, `feedback.py`.
- Hard-coded `/home/roshan` paths: `graphify_nexus_bridge.py:30, :38, :54`, `orchestrator.py:72` (Path.cwd, ok), `harness_engine/orchestrator.py:167`, `state_check.py:3`.
- `test_v3.py` sits outside `tests/`.
- `run_migration_007.py` exits 0 when the migration fails.
- `db.py:69-76` silently copies an unwritable DB to `/tmp/nexus.db`; `db.py:230-243` rebuilds `routing_log` and drops `action_type` and `repeat_count_1h`.
- `cluster_faiss.py` is named for FAISS but is numpy-only.

## 2. DELETE candidates (no live importer, no entry point)

**Provisional.** The triage research (`docs/audits/triage-remediation-2026-11-10.md`, §6) rules out "zero importers" as proof of dead code in this codebase: string-based `importlib.import_module`, `spec_from_file_location`, `entry_points.txt` and coverage-driven imports all bypass a grep. A check found `spec_from_file_location` loading in `server/unified.py` and `server/harness_mcp_bridge.py`. Enumerate every entry point per candidate before deleting any file below.

Confirm against the decisions in `docs/plans/remediation/REVIEW.md` before deleting. Each has a stated basis in the group reports.

- Core and routing: `core/router.py`, `server/autonomous_pipeline.py`, `server/learning_faithful.py`, `server/rl/` (3 files + `__init__`), `server/reflection.py` (test-only importer).
- Local harness: `local_harness/harness.py`, `local_harness/playbook.py`, `server/opencode_adapter.py` (test-only chain), `server/nexus_plan_executor.py`.
- Context and ingest: `context_harness/backend_protocol.py`, `context_harness/bundles/seeder.py`, `context_harness/classifier/classification_pipeline.py`, `graphify_nexus_bridge.py`, `ingest/pipeline.py`. Also `ingest/cli_tool.py`, `git_repo.py`, `http_doc.py`, `mcp_server.py`, `obsidian_vault.py` — dead because the loader registry (`ingest/__init__.py:51-61`) has no callers. Remove the registry and these five go with it. Note that the SSRF in `http_doc.py` and the symlink escape in `obsidian_vault.py` then stop being live risks.
- Scripts: `scripts/seed_benchmarks.py` (fabricated data — delete now), `scripts/seed_routing_log.py` (synthetic queries in the learning loop — delete or tag and exclude), `scripts/test-design-system.py`, `server/scripts/fix_avg_reward_db.py`.

**Merge** (duplicate, keep one): `run_migration_007.py` → `run_migrations_007.py`; `state_check.py` → `verify_state.py`; `generate_eval_dataset.py` → `eval_faiss.py`; `phase4_build_faiss.py` and `regenerate_embeddings.py` → `scripts/rebuild_faiss.py`; `cluster_router.py` (keep) vs `router.py:224`; `fts5.py` → `chunk_retrieval.py`; `chunk_index.py` → `bitvec.py`; `reflection_loop.py` absorbs `self_reflector.py`.

**UNCLEAR** (decide by the stated question): `plan_tracker.py` (has a CLI — is it used?), `context_harness/prediction.py` (wire into feedback or delete), `research_pipeline.py` (does `research_service` call it?), `context_harness/cache.py` (which cache does the query path hit?), `ide_event_client.py` (check `~/.claude` hooks), `sse_reconnect.py`, `rate_limited_proxy.py`, `calibrate_theta.py`, `compute_cluster_centroids.py`, `scan_duplicates.py`, `self_audit.py` (check crontab and systemd), and the one-off phase and campaign scripts.

## 3. Priority order

1. **A1, A2, A3, A7** — the core runtime is broken or misleading. Fix these before any further routing work.
2. **B1, B2, B5, B7** — SSRF guard, bind to loopback, close the dormant shell path, symlink containment.
3. **A10, A12** — stop writing fake data; delete the seeders.
4. **B3, B4** — allowlists on the MCP and process-control entry points.
5. **C** — pick one router (C1) and one reflection path; then delete the duplicates above.
6. **D** — split the large files and fix the import paths.

## 4. Limits

- Not all bodies were read. Files over about 300 lines were checked by definitions, grep and targeted reads. The large files that were not read in full are listed in each group report; their verdicts are provisional.
- `bundles/__init__.py` was not read.
- GitNexus was used on a few symbols only: `record_outcome` and `route_task` (CRITICAL risk, 51 and 61 impacted), `_close_routing_outcomes` (HIGH, 7 impacted), `execute_task_run` (LOW, 3 — the index misses the outcome writes), `_cli_exec` (HIGH, 9), `fetch_page` (HIGH, 15). `ruflo_invoke` returned "ambiguous". The index is multi-repo, so `-r nexus` is needed.
- No tests were run during this audit. Coverage claims come from grep only.
- Dynamic imports by string were not found except the ingest registry, which was checked by grep.
