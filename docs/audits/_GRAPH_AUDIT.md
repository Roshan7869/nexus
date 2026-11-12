# NEXUS — GitNexus Graph Audit Evidence Pack
Generated from live GitNexus index (repo `nexus`, ladybugdb backend, schemaVersion 5)

Index metadata: files=476, nodes=8944, edges=15688, communities=380, processes=300
Indexed commit: 2dfb62d  |  HEAD at audit: c8782ad (STALE by 1 commit)

-------------------- PART 1 — NODE INVENTORY --------------------

| node label | count |
| --- | --- |
| Variable | 1712 |
| Method | 1354 |
| Function | 1201 |
| Property | 986 |
| File | 476 |
| Class | 397 |
| Community | 323 |
| Process | 300 |
| Tool | 98 |
| Route | 66 |
| Module | 0 |
| Interface | 0 |

NOTE: Module=0 and Interface=0 — these abstractions are ABSENT from the graph.

-------------------- PART 2 — EDGE INVENTORY --------------------

| etype | c |
| --- | --- |
| DEFINES | 3680 |
| CALLS | 2953 |
| CONTAINS | 2447 |
| MEMBER_OF | 2020 |
| STEP_IN_PROCESS | 1463 |
| HAS_METHOD | 1374 |
| HAS_PROPERTY | 596 |
| IMPORTS | 507 |
| ENTRY_POINT_OF | 439 |
| HANDLES_TOOL | 98 |
| HANDLES_ROUTE | 66 |
| METHOD_OVERRIDES | 23 |
| EXTENDS | 20 |
| ACCESSES | 2 |

-------------------- PART 3 — ROUTES (HTTP surface) --------------------

| id | m | f |
| --- | --- | --- |
| Route:POST /v1/chat/completions | POST | core/server.py |
| Route:GET /v1/models | GET | core/server.py |
| Route:GET /health | GET | core/server.py |
| Route:GET /metrics/{provider}/{model} | GET | core/server.py |
| Route:GET /metrics/routing | GET | core/server.py |
| Route:GET /config | GET | core/server.py |
| Route:POST /nexus/event | POST | core/server.py |
| Route:GET /nexus/events | GET | core/server.py |
| Route:GET /nexus/learner/stats | GET | core/server.py |
| Route:GET /nexus/beliefs | GET | core/server.py |
| Route:POST /report_outcome | POST | server/nexus_http_daemon.py |
| Route:DELETE /ide/{session_id} | DELETE | server/nexus_http_daemon.py |
| Route:POST /ide/{session_id}/outcome | POST | server/nexus_http_daemon.py |
| Route:GET /ide/{session_id}/harness | GET | server/nexus_http_daemon.py |
| Route:POST /ide/launch-multi | POST | server/nexus_http_daemon.py |
| Route:GET /nexus/harness/dashboard | GET | server/nexus_http_daemon.py |
| Route:GET /ide/list | GET | server/nexus_http_daemon.py |
| Route:POST /ide/launch | POST | server/nexus_http_daemon.py |
| Route:POST /ide/register | POST | server/nexus_http_daemon.py |
| Route:GET /health_check | GET | server/nexus_http_daemon.py |
| Route:GET /health_orchestrate | GET | server/nexus_http_daemon.py |
| Route:GET /cluster_affinities | GET | server/nexus_http_daemon.py |
| Route:GET /learning_stats | GET | server/nexus_http_daemon.py |
| Route:POST /harness/research | POST | server/nexus_http_daemon.py |
| Route:POST /harness/plan | POST | server/nexus_http_daemon.py |
| Route:GET /v1/health | GET | server/nexus_http_daemon.py |
| Route:POST /find_skills | POST | server/nexus_http_daemon.py |
| Route:GET /cluster_list | GET | server/nexus_http_daemon.py |
| Route:POST /cluster_activate | POST | server/nexus_http_daemon.py |
| Route:POST /cluster_route | POST | server/nexus_http_daemon.py |
| Route:POST /debug/route_v4 | POST | server/nexus_http_daemon.py |
| Route:POST /route | POST | server/nexus_http_daemon.py |
| Route:GET /stats | GET | server/nexus_http_daemon.py |
| Route:GET /v1/session/{session_id} | GET | server/nexus_http_daemon.py |
| Route:POST /v1/event | POST | server/nexus_http_daemon.py |
| Route:POST /v1/session/{session_id}/report | POST | server/nexus_http_daemon.py |
| Route:POST /v1/session/open | POST | server/nexus_http_daemon.py |
| Route:GET /v1/ops/harness | GET | server/nexus_http_daemon.py |
| Route:GET /v1/ops/coverage | GET | server/nexus_http_daemon.py |
| Route:GET /v1/ops | GET | server/nexus_http_daemon.py |
| Route:POST /nexus/ops/bootstrap | POST | server/unified.py |
| Route:POST /mcp/{tool_name} | POST | server/unified.py |
| Route:GET /approaches | GET | server/unified.py |
| Route:GET /nexus/health | GET | server/unified.py |
| Route:GET /nexus/learning-stats | GET | server/unified.py |
| Route:GET /nexus/reflection | GET | server/unified.py |
| Route:POST /nexus/report-outcome | POST | server/unified.py |
| Route:POST /nexus/learnings/distill | POST | server/unified.py |
| Route:POST /nexus/kg/rebuild | POST | server/unified.py |
| Route:POST /nexus/router/ab | POST | server/unified.py |
| Route:POST /nexus/research/synthesize | POST | server/unified.py |
| Route:GET /nexus/research/sources | GET | server/unified.py |
| Route:GET /nexus/ops/processes | GET | server/unified.py |
| Route:POST /nexus/route | POST | server/unified.py |
| Route:GET /nexus/ops/status | GET | server/unified.py |
| Route:GET /nexus/router/status | GET | server/unified.py |
| Route:GET /nexus/kg/status | GET | server/unified.py |
| Route:GET /nexus/feedback/coverage | GET | server/unified.py |
| Route:GET /nexus/learnings | GET | server/unified.py |
| Route:POST /nexus/learnings | POST | server/unified.py |
| Route:POST /nexus/task-runs/{task_run_id}/review | POST | server/unified.py |
| Route:GET /nexus/task-runs/{task_run_id} | GET | server/unified.py |
| Route:POST /nexus/autopilot/run | POST | server/unified.py |
| Route:GET /nexus/resources | GET | server/unified.py |
| Route:GET /nexus/stats | GET | server/unified.py |
| Route:POST /nexus/search | POST | server/unified.py |

-------------------- PART 4 — TOOLS (MCP surface) --------------------

| name | f |
| --- | --- |
| harness_arxiv | server/context_harness/server.py |
| harness_smart_research | server/context_harness/server.py |
| harness_research_plan | server/context_harness/server.py |
| research_sites_list | server/context_harness/server.py |
| harness_study_project | server/context_harness/server.py |
| harness_target_site | server/context_harness/server.py |
| harness_lookup | server/context_harness/server.py |
| harness_pubmed | server/context_harness/server.py |
| harness_crossref | server/context_harness/server.py |
| harness_openalex | server/context_harness/server.py |
| harness_paperswithcode | server/context_harness/server.py |
| harness_github_search | server/context_harness/server.py |
| harness_scholar_s2 | server/context_harness/server.py |
| context_harness | server/context_harness/server.py |
| harness_research | server/context_harness/server.py |
| harness_pattern_extract | server/context_harness/server.py |
| harness_spider_results | server/context_harness/server.py |
| harness_spider_status | server/context_harness/server.py |
| harness_spider_start | server/context_harness/server.py |
| harness_sherlock | server/context_harness/server.py |
| harness_fetch_batch | server/context_harness/server.py |
| harness_fetch | server/context_harness/server.py |
| context_doctor | server/context_harness/server.py |
| context_store | server/context_harness/server.py |
| context_search | server/context_harness/server.py |
| _context_store | server/harness_mcp_bridge.py |
| _smart_research | server/harness_mcp_bridge.py |
| _tool | server/harness_mcp_bridge.py |
| harness_plan | server/harness_mcp_bridge.py |
| harness_research_orchestrator | server/harness_mcp_bridge.py |
| browser_harness_status_tool | server/harness_mcp_bridge.py |
| harness_toolsets | server/harness_mcp_bridge.py |
| sync_mcp_tools | server/nexus-mcp-enhanced.py |
| nexus_health | server/nexus-mcp-enhanced.py |
| cluster_list | server/nexus-mcp-enhanced.py |
| cluster_activate | server/nexus-mcp-enhanced.py |
| cluster_route | server/nexus-mcp-enhanced.py |
| update_state | server/nexus-mcp-enhanced.py |
| check_preconditions | server/nexus-mcp-enhanced.py |
| universal_route | server/nexus-mcp-enhanced.py |
| tool_attention_route | server/nexus-mcp-enhanced.py |
| nexus_health_orchestrate | server/nexus-mcp-enhanced.py |
| ide_event | server/nexus-mcp-enhanced.py |
| kg_rebuild | server/nexus-mcp-enhanced.py |
| kg_status | server/nexus-mcp-enhanced.py |
| feedback_coverage | server/nexus-mcp-enhanced.py |
| distill_project_learnings | server/nexus-mcp-enhanced.py |
| search_learnings | server/nexus-mcp-enhanced.py |
| add_learning_note | server/nexus-mcp-enhanced.py |
| review_task | server/nexus-mcp-enhanced.py |
| nexus_health_check | server/nexus-mcp-enhanced.py |
| nexus_route_to_cluster | server/nexus-mcp-enhanced.py |
| close_outcomes | server/nexus-mcp-enhanced.py |
| learning_stats | server/nexus-mcp-enhanced.py |
| report_outcome | server/nexus-mcp-enhanced.py |
| find_alternatives_on_failure | server/nexus-mcp-enhanced.py |
| get_cluster_affinities | server/nexus-mcp-enhanced.py |
| ruflo_invoke | server/nexus-mcp-enhanced.py |
| ruflo_list_tools | server/nexus-mcp-enhanced.py |
| ruflo_proxy_status | server/nexus-mcp-enhanced.py |
| coach_query | server/nexus-mcp-enhanced.py |
| coach_antipatterns | server/nexus-mcp-enhanced.py |
| coach_converse | server/nexus-mcp-enhanced.py |
| coach_skill_finder | server/nexus-mcp-enhanced.py |
| coach_analyze_sessions | server/nexus-mcp-enhanced.py |
| forge_stop | server/nexus-mcp-enhanced.py |
| research_sources | server/nexus-mcp-enhanced.py |
| research_synthesis | server/nexus-mcp-enhanced.py |
| ops_bootstrap | server/nexus-mcp-enhanced.py |
| ops_dashboard | server/nexus-mcp-enhanced.py |
| process_status | server/nexus-mcp-enhanced.py |
| router_ab_suite | server/nexus-mcp-enhanced.py |
| router_status | server/nexus-mcp-enhanced.py |
| route_ab_compare | server/nexus-mcp-enhanced.py |
| get_skill | server/nexus-mcp-enhanced.py |
| forge_status | server/nexus-mcp-enhanced.py |
| forge_spawn | server/nexus-mcp-enhanced.py |
| forge_plan | server/nexus-mcp-enhanced.py |
| repo_map | server/nexus-mcp-enhanced.py |
| project_note | server/nexus-mcp-enhanced.py |
| project_resume | server/nexus-mcp-enhanced.py |
| project_attach | server/nexus-mcp-enhanced.py |
| find_skills | server/nexus-mcp-enhanced.py |
| wiki_brain_query | server/nexus-mcp-enhanced.py |
| audit_routing | server/nexus-mcp-enhanced.py |
| list_resources | server/nexus-mcp-enhanced.py |
| design_plan | server/nexus-mcp-enhanced.py |
| execute_phase | server/nexus-mcp-enhanced.py |
| verify_phase | server/nexus-mcp-enhanced.py |
| audit_plan | server/nexus-mcp-enhanced.py |
| task_start | server/nexus-mcp-enhanced.py |
| task_complete | server/nexus-mcp-enhanced.py |
| nexus_session_open | server/nexus-mcp-enhanced.py |
| nexus_session_report | server/nexus-mcp-enhanced.py |
| harness_dashboard | server/nexus-mcp-enhanced.py |
| task_status | server/nexus-mcp-enhanced.py |
| autopilot_run | server/nexus-mcp-enhanced.py |
| get_task_run | server/nexus-mcp-enhanced.py |

-------------------- PART 5 — COMMUNITIES (functional areas) --------------------

| id | label | coh | syms |
| --- | --- | --- | --- |
| comm_109 | Server | 0.7241379310344828 | 26 |
| comm_123 | Coach | 0.8735632183908046 | 23 |
| comm_305 | Server | 0.9777777777777777 | 23 |
| comm_196 | Tests | 0.9523809523809523 | 21 |
| comm_245 | Server | 0.8125 | 19 |
| comm_113 | Server | 0.6296296296296297 | 18 |
| comm_114 | Server | 0.9142857142857143 | 16 |
| comm_288 | Server | 0.8888888888888888 | 16 |
| comm_56 | Router_v2 | 0.7804878048780488 | 15 |
| comm_107 | Server | 0.76 | 15 |
| comm_237 | Harness_engine | 0.9130434782608695 | 15 |
| comm_299 | Server | 0.918918918918919 | 14 |
| comm_45 | Router_v2 | 0.896551724137931 | 13 |
| comm_47 | Scripts | 0.7058823529411765 | 13 |
| comm_115 | Server | 0.6666666666666666 | 13 |
| comm_182 | Channels | 0.7777777777777778 | 13 |
| comm_267 | Server | 0.8108108108108109 | 13 |
| comm_301 | Server | 0.7659574468085106 | 13 |
| comm_314 | Server | 1 | 13 |
| comm_316 | Server | 0.96 | 13 |
| comm_63 | Scripts | 0.926829268292683 | 12 |
| comm_64 | Scripts | 1 | 12 |
| comm_104 | Scripts | 0.7906976744186046 | 12 |
| comm_203 | Tests | 0.631578947368421 | 12 |
| comm_345 | Router_v2 | 0.96 | 12 |
| comm_8 | Server | 0.9333333333333333 | 11 |
| comm_43 | Server | 0.96 | 11 |
| comm_73 | Scripts | 0.9090909090909091 | 11 |
| comm_122 | Tests | 0.918918918918919 | 11 |
| comm_152 | Channels | 0.6 | 11 |
| comm_229 | Tests | 0.8 | 11 |
| comm_260 | Integrations | 1 | 11 |
| comm_279 | Server | 0.9230769230769231 | 11 |
| comm_282 | Server | 0.7692307692307693 | 11 |
| comm_34 | Cluster_34 | 1 | 10 |
| comm_121 | Coach | 0.7272727272727273 | 10 |
| comm_125 | Coach | 0.56 | 10 |
| comm_126 | Server | 0.782608695652174 | 10 |
| comm_156 | Tests | 0.8148148148148148 | 10 |
| comm_170 | Context_harness | 0.5555555555555556 | 10 |
| comm_181 | Server | 0.7096774193548387 | 10 |
| comm_219 | Server | 0.5142857142857142 | 10 |
| comm_235 | Harness_engine | 0.7407407407407407 | 10 |
| comm_280 | Server | 0.896551724137931 | 10 |
| comm_287 | Server | 0.6666666666666666 | 10 |
| comm_289 | Server | 0.8571428571428571 | 10 |
| comm_337 | Server | 0.5294117647058824 | 10 |
| comm_25 | Server | 0.48484848484848486 | 9 |
| comm_79 | Ingest | 0.75 | 9 |
| comm_96 | Server | 0.782608695652174 | 9 |
| comm_128 | Comm | 1 | 9 |
| comm_134 | Server | 0.7272727272727273 | 9 |
| comm_139 | Tests | 0.9411764705882353 | 9 |
| comm_154 | Context_harness | 0.47058823529411764 | 9 |
| comm_194 | Context_harness | 0.6956521739130435 | 9 |
| comm_205 | Context_harness | 0.9090909090909091 | 9 |
| comm_223 | Server | 0.75 | 9 |
| comm_226 | Server | 0.631578947368421 | 9 |
| comm_233 | Server | 1 | 9 |
| comm_10 | Tests | 0.8333333333333334 | 8 |

-------------------- PART 6 — PROCESSES / FLOWS --------------------

Total processes: 300. Types:
| ptype | c |
| --- | --- |
| cross_community | 268 |
| intra_community | 32 |

Step-count distribution:
| steps | c |
| --- | --- |
| 3 | 3 |
| 4 | 130 |
| 5 | 95 |
| 6 | 54 |
| 7 | 11 |
| 8 | 5 |
| 9 | 2 |

### Representative flow chains (entry -> terminal, step order)

**proc_205_chat_completions**
   1. chat_completions  (core/server.py)
   2. classify_task  (core/learner.py)
   3. encode  (server/iso_embedder.py)
   4. load_model  (server/iso_embedder.py)

**proc_107_unified_route_v5**
   1. unified_route_v5  (server/router_v2/v5.py)
   2. _l1_intent  (server/router_v2/v5.py)
   3. route_task  (server/router.py)
   4. classify_task  (server/router.py)
   5. close  (core/learner.py)

**proc_186_chat_completions**
   1. chat_completions  (server/unified.py)
   2. thompson_sample  (core/learner.py)
   3. get_belief  (core/learner.py)
   4. _belief_key  (core/learner.py)

**proc_0_harness_study_projec**
   1. harness_study_project  (server/context_harness/server.py)
   2. _harvest_all  (server/context_harness/server.py)
   3. harvest_site  (server/context_harness/channels/target_site.py)
   4. fetch_batch  (server/context_harness/channels/target_site.py)
   5. _one  (server/context_harness/channels/target_site.py)
   6. fetch_page  (server/context_harness/channels/target_site.py)
   7. _extract_content  (server/context_harness/channels/target_site.py)
   8. _extract_with_heuristics  (server/context_harness/channels/target_site.py)
   9. _html_to_text  (server/context_harness/channels/target_site.py)

**proc_163_ide_event**
   1. ide_event  (server/nexus-mcp-enhanced.py)
   2. handle_ide_event  (server/rl_feedback_loop.py)
   3. _record_beliefs_from_event  (server/rl_feedback_loop.py)
   4. outcome_to_event  (server/event_bridge.py)
   5. create_event  (core/ide_events.py)


-------------------- PART 7 — GAP PROBES (zero-definition primitives) --------------------

Direct filePath substring probes against the index — ALL returned ZERO nodes:
   - 'fiber' -> 0 nodes, 0 flows
   - 'dispose' -> 0 nodes, 0 flows
   - 'coeffect' -> 0 nodes, 0 flows
   - 'isolate' -> 0 nodes, 0 flows
   - 'intercept' -> 0 nodes, 0 flows
   - 'hot-swap' -> 0 nodes, 0 flows
   - 'mount' -> 0 nodes, 0 flows
   - 'unmount' -> 0 nodes, 0 flows
   - 'reactiv' -> 0 nodes, 0 flows

Present but SEMANTICALLY UNRELATED (noise-aligned, must not be mistaken for the concept):
   - 'lifecycle' -> server/catalog_lifecycle.py, scripts/run_catalog_lifecycle.py (catalog, not plugins)
   - 'registry'  -> providers/registry.py (provider registry, not a DI/service container)

CONCLUSION OF GRAPH AUDIT:
NEXUS has a rich static call graph (2953 CALLS, 3680 DEFINES) and 300 traced execution
flows, but ZERO nodes or edges exist for: dependency injection / service container,
plugin lifecycle, effect tracking / reversible disposal, middleware interception,
and hot-swap. The dynamic-composition layer is structurally absent.
