# RAG System Execution Plan — Virtual Technical Org

## Team Consensus

The delta between the proposed architecture and what exists is well-understood. The system has **strong foundations** (task understanding, execution, hybrid search, evaluation) but **critical gaps** in four areas:

1. **Observability → Trajectory pipeline**: Cannot close the learning loop because outcome data is 6% coverage
2. **Skill extraction loop**: Cannot convert successful traces into reusable memory objects
3. **Promotion gate**: No safety barrier between "works on my machine" and production
4. **FAISS index hygiene**: Two index paths, one misnamed search class, one daemon crash

These gaps must be fixed in order: fix the broken things (Phase -1) → unify what exists (Phase 0) → build what's missing (Phases 1-5). The plan below is mapped into **8 phases** with **45 tasks** and **147 subtasks**, each with file paths, effort estimates, and graphify-trackable IDs.

---

## Expert Breakdown

### AI Engineer View

**Observation:** The retrieval architecture is fundamentally correct — two specialized stores (FAISS for tools, ChromaDB for trading) with a task router on top. But the implementations have drift. `ClusterFAISS` doesn't use FAISS. `generate_embeddings.py` writes to a different path than `rebuild_faiss.py`. The v4 pipeline's theta decay chain is clever but built on 3 labeled pairs — which means the adaptive threshold is guessing.

**Risk:** The biggest hallucination vector is NOT the LLM — it's the retrieval providing plausible-but-wrong context. In ChromaDB, this means retrieving a trending strategy during a ranging market. In FAISS, this means retrieving a `DROP TABLE` equivalent when the user asks about cleanup. The metadata filters must be mandatory pre-filters, not optional post-filters.

**Recommendation:** Enforce `market_condition = current_regime` as a pre-filter in ChromaDB. Enforce `risk_level != 'danger'` for non-admin FAISS queries. Make these compile-time checks, not runtime conventions.

### ML Engineer View

**Observation:** The only working "learning" is Thompson Sampling over 6% outcome coverage — which is noise. The fine-tuned linear adapter (384→384) is a positive sign: it shows understanding that domain adaptation matters. But it was trained on 100% synthetic data with no outcome validation.

**Risk:** Synthetic training data can create a system that scores well on synthetic evals but degrades on real queries. The 50-query eval set is too small (contains only 2-3 queries per setup type). The ChromaDB adapter could be making retrieval worse for real trading queries while looking good on the template-based eval.

**Recommendation:** Expand the labeled test set to 200+ real (not synthetic) queries first. Then re-evaluate the adapter. Only after that, consider periodic adapter re-training with accumulated real outcome data.

### Data Scientist View

**Observation:** The metrics framework is partially correct but untethered from ground truth. ChromaDB tracks HR@k/MRR@k against a 50-query dataset. FAISS tracks routing accuracy against outcome reporting (which is 6% coverage). Neither system measures what actually matters: **does retrieval improve the user's outcome?**

- For trading: does retrieving strategy X lead to profitable trades?
- For tools: does retrieving skill Y reduce the user's time-to-resolution?

**Risk:** You can optimize HR@k from 0.36 to 0.60 and not improve trading PnL by a single dollar. The metric proxy (HR@k) and the true objective (PnL) are loosely coupled at best.

**Recommendation:** Build a holdout set of 50 real trade outcomes mapped to the retrieval context that was active at trade time. Measure `outcome_win_rate@k` — the win rate of trades that used a retrieved strategy vs. those that didn't. This is the only metric that proves retrieval quality.

### Deployment Engineer View

**Observation:** Both systems run as embedded Python processes. No container orchestration. No autoscaling. No connection pooling. Single points of failure:

| Component | Failure Mode | Recovery Time |
|-----------|-------------|---------------|
| ChromaDB SQLite | Corruption | Full re-index (~2 min) |
| FAISS binary index | Partial write | Restore from backup (5 min) |
| ChromaDB embedding model | Download failure | No offline fallback |
| ChromaDB reranker | OOM | Service restart (30s) |
| FAISS index rebuild | Crash mid-write | Corrupted .index, need backup |
| NEXUS FastAPI server | Unauthenticated access | Security incident |

**Risk:** The system has no monitoring dashboard, no alert routing, no structured logs. When it breaks, you'll notice because results look wrong — not because an alert fires.

**Recommendation:** Before any learning pipeline, fix the operational zeros: atomic FAISS writes, ChromaDB backup, startup integrity checks, and basic HTTP endpoint monitoring (status=200, latency<5s, result_count>0).

### LLM Engineer View

**Observation:** The prompt architecture for retrieval has two implicit modes:

1. **Tool mode** (FAISS): "Here are tools, pick one" — uses ISO scoring + summarized descriptions
2. **Knowledge mode** (ChromaDB): "Here's context, answer" — uses full chunk text + reranking

The v4 pipeline's 60-token summary target and 395-token-per-schema budget are well-calibrated. But the lazy body loading only works when FAISS search succeeds — fallback routes get zero body context.

**Risk:** The context window efficiency is good (96% token reduction claimed), but the reduction from v2 → v4 is mostly because v4 returns only 10 candidates instead of 459. This means recall could degrade on unusual queries that don't match the top-10. The theta decay chain (0.28 → 0.0) mitigates this but at the cost of eventually returning everything.

**Recommendation:** Add a "retrieval confidence" signal to the LLM prompt: tell the LLM whether the retrieved context had high similarity (theta ≥ 0.28) or was a decay-chain fallback (theta < 0.12). This lets the LLM calibrate how much to trust the context.

### Systems Architect View

**Observation:** The biggest structural problem is **no trajectory store**. The `routing_log`, `tool_traces`, and `task_runs` tables exist but are append-only logs, not queryable trajectory records. You can't replay a past query, diff the results with the current system, or cluster similar trajectories for skill extraction.

The second problem is **coupling between FAISS and ChromaDB**. They share an embedding model but not an evaluation framework. ChromaDB vectors are dumped into FAISS without cross-validation. The `register_chromadb.py` script pushes 592 vectors into FAISS's `knowledge_wiki` cluster — but a bug in ChromaDB's retrieval wouldn't be caught by FAISS's eval, and vice versa.

**Recommendation:** Define a clear ownership boundary:

- FAISS owns: tool/skill/command retrieval, evaluated by invocation rate
- ChromaDB owns: trading strategy retrieval, evaluated by outcome win rate
- Nexus owns: routing between them, evaluated by end-to-end task success
- The trajectory store owns: the raw data for all learning loops

This prevents each system from silently compensating for the other's failures.

---

## Graphify-Mapped Execution Plan

The plan uses graphify tracking IDs (G-001 through G-083) for each task. Each task has: owner phase, subtasks (S1-S_n), file paths, effort, dependencies, and validation criteria.

### Phase -1: Patch Critical Breakage (Days 1-2)

```
G-001: Fix mcp_sync.py daemon crash
  S1: Add `import os` at module level in mcp_sync.py
  File: /home/roshan/nexus/server/mcp_sync.py
  Effort: 1 line, 5 min
  Validation: daemon starts without NameError
  Dependencies: none

G-002: Resolve FAISS split-brain paths
  S1: Verify count matches between db/faiss/resources.index and db/faiss_index.bin
  S2: Remove legacy files if canonical index is newer
  S3: Add startup check: warn if multiple index files exist
  File: /home/roshan/nexus/db/faiss/, /home/roshan/nexus/db/faiss_index.bin
  Effort: 20 min
  Validation: only one FAISS index path exists after cleanup
  Dependencies: none

G-003: Resolve ChromaDB split-brain dirs
  S1: Verify chroma_db/ is active (19MB) vs chromadb/ is orphan (188K)
  S2: Archive chromadb/ dir to chromadb.BAK
  S3: Add startup assertion in config.py: only one chroma_db dir
  File: /home/roshan/Downloads/Algotrading/strategy_db/config.py
  Effort: 15 min
  Validation: config.DB_DIR exists and is unique
  Dependencies: none

G-004: Rename ClusterFAISS to ClusterNumpySearch
  S1: Rename class from ClusterFAISS to ClusterNumpySearch
  S2: Update all references in cluster_router.py
  S3: Update docstring to clarify this is the numpy fallback path
  File: /home/roshan/nexus/server/cluster_faiss.py
  Effort: 10 min
  Validation: all imports resolve, no functional change
  Dependencies: none

G-005: Fix cluster_router.py import path
  S1: Add sys.path.insert(0, dirname(__file__)) at top
  S2: Verify all local imports (cluster_config, cluster_faiss) resolve
  File: /home/roshan/nexus/server/cluster_router.py
  Effort: 2 lines, 5 min
  Validation: python3 -c "from cluster_router import ClusterRouter" succeeds from any dir
  Dependencies: G-004
```

### Phase 0: Unified Schema + Hygiene (Days 3-7)

```
G-010: Define canonical memory taxonomy
  S1: Create nexus-schema.md with all 6 document types
  S2: ResourceDoc: type(name, aliases, env_scope, risk_level, tool_name, args_schema_ref, version, checksum, built_at)
  S3: SkillDoc: type(title, trigger_conditions, preconditions, steps, tools_used, failure_modes, example_query)
  S4: TradingChunk: type(setup_name, setup_type, regime, timeframe, instrument, confidence, source, recency, outcome)
  S5: OutcomeDoc: type(setup_name, win_rate, total_trades, avg_pnl, regime_breakdown, last_updated)
  S6: FailureDoc: type(query, expected_result, actual_result, failure_class, correction)
  S7: TrajectoryRecord: type(query_id, query, faiss_results, chroma_results, rerank_scores, tool_calls, latency, outcome)
  File: /home/roshan/nexus/docs/nexus-schema.md (NEW)
  Effort: 4 hours
  Validation: schema is internally consistent and maps to existing DB tables
  Dependencies: none

G-011: Enrich FAISS metadata sidecar
  S1: Add fields to .meta.json: resource_id list, dim, model, n_total, built_at, git_hash, checksum
  S2: Add checksum computation after FAISS index build (sha256 of index binary)
  S3: Add verification on load: compare checksum, warn on mismatch
  File: /home/roshan/nexus/scripts/rebuild_faiss.py, /home/roshan/nexus/server/iso_embedder.py
  Effort: 3 hours
  Validation: rebuild produces .meta.json with all fields, load verifies checksum
  Dependencies: G-002, G-010

G-012: Add atomic FAISS index rebuild
  S1: Build to temp file: resources.index.tmp
  S2: os.rename() to resources.index (atomic on same filesystem)
  S3: Write .meta.json only after successful rename
  S4: Rollback: if rename fails, .tmp file is recoverable
  File: /home/roshan/nexus/scripts/rebuild_faiss.py
  Effort: 2 hours
  Validation: simulate crash mid-build → old index survives, no corruption
  Dependencies: G-011

G-013: Enrich ChromaDB metadata
  S1: Add fields to build_metadata(): 'confidence', 'ingested_at', 'source_reliability', 'regime_validity'
  S2: source_reliability: Chart Fanatics=1.0, The Trading Channel=0.9, synthetic=0.5, unknown=0.3
  S3: Update ingest.py to include new fields on next re-index
  S4: Add startup check: "expected metadata fields present"
  File: /home/roshan/Downloads/Algotrading/strategy_db/ingest.py, /home/roshan/Downloads/Algotrading/strategy_db/schema.py
  Effort: 4 hours
  Validation: re-ingest, verify ChromaDB metadata contains new fields for all 592 chunks
  Dependencies: G-003, G-010

G-014: Expand labeled test sets
  S1: ChromaDB: create 200 labeled queries (from 50)
  S2: Stratify: 40 per market_condition × 5 types, distributed across setup_types
  S3: Each query has: text, expected_setup_name, gold_chunk_ids[1-3], market_condition, setup_type
  S4: FAISS: create 100 labeled queries (from 3)
  S5: 50 tool-lookup + 30 investigation + 20 adversarial
  S6: FAISS each has: task_text, expected_resource_names[1-3], resource_types
  File: /home/roshan/Downloads/Algotrading/strategy_db/eval/eval_dataset.json (extend),
        /home/roshan/nexus/server/scripts/faiss_eval_dataset.json (NEW)
  Effort: 8 hours
  Validation: both datasets pass self-consistency checks (gold IDs exist in respective stores)
  Dependencies: none

G-015: Fix outcome sync coverage
  S1: Lower rapidfuzz threshold from 50 to 30
  S2: Add staged matching: exact → alias_map → fuzzy(50) → fuzzy(30) → manual_review_list
  S3: Generate manual_review_list.json of unmatched outcomes
  S4: Add 8 new ALIAS_MAP entries from unmatched analysis
  File: /home/roshan/Downloads/Algotrading/strategy_db/outcome_sync.py
  Effort: 3 hours
  Validation: sync count increases from 9/13 to 12+/13 matched categories
  Dependencies: none

G-016: Fix SemanticCache memory duplication
  S1: Remove separate SentenceTransformer creation in query_with_cache
  S2: Reuse the existing embedder from StrategyDB or a shared singleton
  S3: Pass embedder to SemanticCache constructor
  File: /home/roshan/Downloads/Algotrading/strategy_db/search.py, /home/roshan/Downloads/Algotrading/strategy_db/cache.py
  Effort: 2 hours
  Validation: memory usage drops by ~180MB on cache-enabled query path
  Dependencies: none
```

### Phase 1: Unified Gateway + Retrieval Router (Days 8-14)

```
G-020: Build retrieval-router.py
  S1: Create RetrievalRouter class at /home/roshan/nexus/server/retrieval_router.py (NEW)
  S2: TaskType enum: TOOL, TRADING, HYBRID, DEBUG, CHAT
  S3: classify() method: keyword rules first, then model-assisted fallback
  S4: TOOL → FAISS-only, TRADING → ChromaDB-only, HYBRID → both + weighted merge
  S5: DEBUG → both + return diagnostic info
  S6: CHAT → skip retrieval entirely
  S7: route() method: accept query + optional task_type override, return unified result format
  Effort: 8 hours
  Validation: 20 test queries route to correct path; latency < 50ms overhead
  Dependencies: G-010

G-021: Build EmbedderRegistry singleton
  S1: Create EmbedderRegistry at /home/roshan/nexus/server/embedder_registry.py (NEW)
  S2: Single SentenceTransformer instance, shared across all consumers
  S3: Replace all ad-hoc embedder creation in: IsoEmbedder, ToolAttention, ToolAttentionV4,
      ClusterRouter, tool_attention_v4.IntentRouter
  S4: count() method returns number of consumers (should be 1)
  File: /home/roshan/nexus/server/embedder_registry.py (NEW),
        /home/roshan/nexus/server/iso_embedder.py, /home/roshan/nexus/server/tool_attention.py,
        /home/roshan/nexus/server/tool_attention_v4.py, /home/roshan/nexus/server/cluster_router.py
  Effort: 4 hours
  Validation: only 1 SentenceTransformer instance exists under any workload
  Dependencies: none

G-022: Add FAISS safety filter
  S1: Add risk_level field to resource metadata (safe|warning|danger)
  S2: Default all existing resources to 'safe'
  S3: Add SAFE_ONLY mode flag to FAISS search: filters out risk_level=danger
  S4: Auto-enable SAFE_ONLY for non-admin routes
  File: /home/roshan/nexus/server/tool_attention_v4.py, /home/roshan/nexus/scripts/rebuild_faiss.py
  Effort: 3 hours
  Validation: query containing "rm -rf" returns no dangerous tools in results
  Dependencies: G-011

G-023: Add ChromaDB regime pre-filter
  S1: Integrate HMMRegimeDetector into query flow
  S2: Add optional regime_overrides to query endpoint
  S3: Auto-append where={"market_condition": {"$eq": detected_regime}} when user doesn't override
  S4: Add fallback: if regime detection fails, use "any" (no filter)
  File: /home/roshan/Downloads/Algotrading/strategy_db/search.py,
        /home/roshan/Downloads/Algotrading/strategy_db/regime_query.py,
        /home/roshan/Downloads/Algotrading/strategy_db/gateway.py
  Effort: 5 hours
  Validation: query returns 60% fewer results (regime-filtered vs unfiltered) on same query
  Dependencies: none

G-024: Add retrieval-confidence to LLM prompt context
  S1: In FAISS route response, tag each result with theta_level_used
  S2: HIGH confidence = theta >= 0.28 (normal threshold pass)
  S3: MEDIUM = theta 0.12-0.28 (decay chain hit)
  S4: LOW = theta < 0.12 (decay chain fallback) or v2 keyword fallback
  S5: Add retrieval_confidence_summary to v4 pipeline output
  File: /home/roshan/nexus/server/tool_attention_v4.py
  Effort: 3 hours
  Validation: route response includes confidence breakdown per result
  Dependencies: G-020
```

### Phase 2: Trajectory Store + Observability (Days 15-22)

```
G-030: Build TrajectoryStore
  S1: Create TrajectoryStore at /home/roshan/nexus/server/trajectory_store.py (NEW)
  S2: Schema: trajectory_id, session_id, query, task_type, faiss_results(json),
      chroma_results(json), rerank_scores, tool_calls, final_response, latency_ms,
      outcome(correct|wrong|pending), created_at
  S3: record_trajectory(): writes from route_v4/query output
  S4: get_trajectory(id): full replay
  S5: query_trajectories(query_embedding, limit=50): similar trajectory search
  S6: TrajectoryStore table in nexus.db
  File: /home/roshan/nexus/server/trajectory_store.py (NEW)
  Effort: 8 hours
  Validation: 100 queries recorded, searchable by similarity, replayable
  Dependencies: G-010, G-020

G-031: Add trajectory recording to all route paths
  S1: Hook TrajectoryStore.record_trajectory() into route_v4
  S2: Hook into ChromaDB search (gateway.py query endpoint)
  S3: Hook into hybrid route (retrieval_router.py)
  S4: Add async background recording (don't block response)
  File: /home/roshan/nexus/server/tool_attention_v4.py,
        /home/roshan/Downloads/Algotrading/strategy_db/gateway.py,
        /home/roshan/nexus/server/retrieval_router.py
  Effort: 4 hours
  Validation: every route call produces a trajectory record with latency < 5ms overhead
  Dependencies: G-030

G-032: Add OpenTelemetry spans at key points
  S1: Install opentelemetry-instrumentation (check if already in venv)
  S2: Add spans: query_parse, faiss_search, chromadb_search, hybrid_fusion,
      rerank, lazy_load, generation
  S3: Export to console (development) and OTLP (production)
  S4: Add trace_id to all route responses for debugging
  File: /home/roshan/nexus/server/trajectory_store.py (add tracing),
        /home/roshan/nexus/server/tool_attention_v4.py,
        /home/roshan/Downloads/Algotrading/strategy_db/gateway.py
  Effort: 6 hours
  Validation: W3C trace context propagates through all service calls
  Dependencies: G-030

G-033: Build retrieval quality dashboard
  S1: Create HTML dashboard at /home/roshan/nexus/server/dashboard/ (NEW)
  S2: Metric cards: precision@5 (7d window), recall@10, outcome_win_rate,
      cache_hit_rate, p50/p95/p99 latency, stale_index_age
  S3: Time-series charts for each metric over 7/30/90 day windows
  S4: Table of top-10 failing queries by precision
  S5: Integration with existing CLI dashboard (scripts/dashboard.py)
  File: /home/roshan/nexus/server/dashboard/ (NEW directory),
        /home/roshan/nexus/scripts/dashboard.py (extend)
  Effort: 10 hours
  Validation: dashboard renders with real data from trajectory store
  Dependencies: G-030, G-031

G-034: Add alert triggers
  S1: Add alert_rules table: metric, operator, threshold, cooldown_minutes
  S2: Default rules: index_age > 7 days, precision@5_drop > 10% in 24h,
      cache_hit_rate < 20%, latency_p95 > 5s, outcome_win_rate < 0.35
  S3: Check rules on each health endpoint call
  S4: Log alerts to alerts_log table
  File: /home/roshan/nexus/server/trajectory_store.py (add alert methods),
        /home/roshan/nexus/server/unified.py (health endpoint extension)
  Effort: 5 hours
  Validation: simulate stale index, alert fires within 1 health check cycle
  Dependencies: G-030
```

### Phase 3: Outcome Pipeline Fix + Learning Signal (Days 23-30)

```
G-040: Complete the outcome feedback loop in FAISS
  S1: Add complete_route_turn() call after every agent tool execution
  S2: Capture user feedback (implicit: used/didn't-use + explicit: thumbs up/down)
  S3: Wire to record_outcome() which updates resources.times_correct/times_wrong
  S4: Add auto-verification: if same route is taken 3+ times, auto-mark as "correct"
  File: /home/roshan/nexus/server/tool_attention_v4.py,
        /home/roshan/nexus/server/broker_runtime.py
  Effort: 8 hours
  Validation: outcome recording rises from 6% to 80%+ coverage within a session
  Dependencies: G-030, G-031

G-041: Add ChromaDB outcome-grounded evaluation
  S1: Map trade outcomes to retrieval context at time of trade
  S2: Build outcome_eval.py at /home/roshan/Downloads/Algotrading/strategy_db/eval/outcome_eval.py (NEW)
  S3: outcome_win_rate@k = win rate of trades where retrieved strategies were in top-k
  S4: Run on 119 existing trade outcomes + 50 holdout
  S5: Report: HR@k, outcome_win_rate@k, correlation between HR@k and outcome
  File: /home/roshan/Downloads/Algotrading/strategy_db/eval/outcome_eval.py (NEW)
  Effort: 5 hours
  Validation: outcome_eval.py runs on all 119 trades, produces correlation report
  Dependencies: G-014, G-015

G-042: Re-calibrate theta with new labeled set
  S1: Run calibrate_theta.py on the new 100-query FAISS dataset
  S2: Compute optimal theta per resource_type
  S3: Compute optimal theta overall (was 0.10 on 3 queries, expect 0.20-0.28 on 100)
  S4: Store calibrated thetas in calibration_table: resource_type, optimal_theta, n_samples
  File: /home/roshan/nexus/server/scripts/calibrate_theta.py
  Effort: 3 hours
  Validation: calibration runs on 100 queries, produces stable theta values
  Dependencies: G-014

G-043: Purge dead resources from FAISS
  S1: Mark resources with times_triggered=0 AND age > 90 days as 'archived'
  S2: Rebuild FAISS index excluding archived resources
  S3: Re-cluster remaining resources (ClusterClassifier re-run)
  S4: Verify precision@5 improves by measured amount
  File: /home/roshan/nexus/scripts/rebuild_faiss.py, /home/roshan/nexus/reclassify.py
  Effort: 4 hours
  Validation: precision@5 improves by ≥ 3% after purge (less noise in dense space)
  Dependencies: G-042
```

### Phase 4: Skill Extraction Loop (Days 31-42)

```
G-050: Build SkillDistiller
  S1: Create /home/roshan/nexus/server/skill_distiller.py (NEW)
  S2: cluster_trajectories(): cluster by task signature + tool pattern
  S3: extract_common_workflow(): find common steps across cluster
  S4: generate_skill_doc(): produce SkillDoc from cluster centroid
  S5: Cluster method: SentenceTransformer embeddings of (query + tool sequence), HDBSCAN
  File: /home/roshan/nexus/server/skill_distiller.py (NEW)
  Effort: 10 hours
  Validation: distills 50 similar trajectories into 3-5 SkillDocs with >80% step overlap
  Dependencies: G-030, G-040

G-051: Build SkillDoc validation gate
  S1: check_reproducibility(): test on 3-5 held-out tasks from same cluster
  S2: check_dedup(): cosine > 0.85 with existing SkillDoc → reject as duplicate
  S3: check_env_tags(): attach tags, reject if task depends on unavailable env
  S4: validate(): run all checks, produce validation report
  File: /home/roshan/nexus/server/skill_distiller.py
  Effort: 5 hours
  Validation: known-duplicate SkillDoc is rejected, new SkillDoc passes
  Dependencies: G-050

G-052: Ingest validated SkillDocs into FAISS
  S1: Embed SkillDoc title + trigger_conditions + steps + failure_modes
  S2: Store as resource_type='skilldoc' in resources table
  S3: Add to FAISS index on next rebuild
  S4: Tag with env_tags in metadata for filtering
  File: /home/roshan/nexus/scripts/rebuild_faiss.py (extend),
        /home/roshan/nexus/server/skill_distiller.py
  Effort: 4 hours
  Validation: SkillDocs appear in FAISS search, filterable by resource_type='skilldoc'
  Dependencies: G-051

G-053: Build TradingOutcomeNote pipeline
  S1: Create /home/roshan/Downloads/Algotrading/strategy_db/outcome_notes.py (NEW)
  S2: Extract recurring patterns from successful trades in outcome_history.json
  S3: Generate TradingOutcomeNote: strategy_name, market_condition, win_rate_breakdown,
      avg_pnl, best_setup, worst_setup, notes
  S4: Ingest into ChromaDB as setup_type='outcome_note'
  File: /home/roshan/Downloads/Algotrading/strategy_db/outcome_notes.py (NEW)
  Effort: 6 hours
  Validation: outcome notes appear in ChromaDB search with setup_type='outcome_note'
  Dependencies: G-015
```

### Phase 5: RL Training + Policy Tuning (Days 43-56)

```
G-060: Build trajectory-to-transition converter
  S1: Create /home/roshan/nexus/server/lightning_adapter.py (NEW)
  S2: state = (query_embedding[384], task_type_onehot[5], faiss_top5_scores[5],
      chroma_top5_scores[5], fusion_alpha, theta_used)
  S3: action = (retrieval_path[3 choices], top_k[3 choices], fusion_alpha[0.3-0.9],
      use_reranker[bool], theta[5 choices])
  S4: reward = (completion[0/1] + groundedness[0-1] + latency_penalty[-0.3 to 0] +
      user_feedback[-1 to 1] + outcome_pnl[-1 to 1])
  S5: convert_trajectory(): produce list of (state, action, reward, next_state) tuples
  File: /home/roshan/nexus/server/lightning_adapter.py (NEW)
  Effort: 8 hours
  Validation: 50 trajectories convert to valid transitions with correct shapes
  Dependencies: G-030, G-040

G-061: Tune retrieval fusion weight (alpha)
  S1: Sweep alpha from 0.3 to 0.9 in 0.1 steps on 200-query ChromaDB eval set
  S2: For each alpha: compute HR@5, MRR@10, precision@5
  S3: Alpha selection principle: maximize precision@5 (trading prefers precision)
  S4: Add alpha to runtime config with --alpha override
  File: /home/roshan/Downloads/Algotrading/strategy_db/search.py,
        /home/roshan/Downloads/Algotrading/strategy_db/eval/run_eval.py
  Effort: 3 hours
  Validation: alpha sweep produces clear optimum, not plateau
  Dependencies: G-014

G-062: Tune per-resource-type theta values
  S1: For each resource_type: compute optimal theta on 100-query FAISS dataset
  S2: Store in calibration_table
  S3: v4 pipeline reads per-type theta on each route
  File: /home/roshan/nexus/server/tool_attention_v4.py,
        /home/roshan/nexus/server/scripts/calibrate_theta.py
  Effort: 4 hours
  Validation: skills get theta=0.28, commands get theta=0.22, MCP tools get theta=0.18
  Dependencies: G-042

G-063: Re-evaluate fine-tuned adapter
  S1: Run evaluate.py comparing base vs adapted on the new 200-query dataset
  S2: Compute: HR@k, MRR@k, precision@k, outcome_win_rate@k
  S3: If adapted < base on any metric: flag for retraining
  S4: If adapted > base: promote as primary embedder
  File: /home/roshan/Downloads/Algotrading/strategy_db/finetune/evaluate.py
  Effort: 3 hours
  Validation: adapter performance delta is clear (not within noise)
  Dependencies: G-014

G-064: Build reinforcement learning buffer
  S1: Collect 500+ trajectory transitions from live system
  S2: Build replay buffer: transitions stored by task_type
  S3: Train small policy: 2-layer MLP (384 → 128 → 5 actions) for retrieval_path selection
  S4: Offline evaluation: policy selects same path as human 80%+ of time
  File: /home/roshan/nexus/server/lightning_adapter.py (extend)
  Effort: 10 hours
  Validation: policy accuracy > 80% on held-out trajectories
  Dependencies: G-060
```

### Phase 6: Associative Memory (Days 57-70)

```
G-070: Build graph store
  S1: Create /home/roshan/nexus/server/graph_store.py (NEW)
  S2: entities table: entity_id, entity_type, name, embedding_id, metadata
  S3: relations table: source_id, target_id, edge_type, weight, created_at
  S4: Entity types: tool, skill, agent, command, strategy, indicator, market_regime, outcome
  S5: Edge types: uses, fixes, depends_on, works_in_regime, failed_under, derived_from, contradicts
  S6: get_related(entity_name, edge_type, max_depth=2): BFS traversal
  File: /home/roshan/nexus/server/graph_store.py (NEW)
  Effort: 12 hours
  Validation: "FVG" → get_related("FVG", "works_in_regime") → ["trending", "volatile"]
  Dependencies: G-010, G-020

G-071: Integrate graph into reranking
  S1: In retrieval_router.py, add optional graph_proximity_boost
  S2: boost = 1.0 + 0.2 * graph_proximity (max 1.6x)
  S3: Apply to results where graph suggests task-relevant entities
  S4: Disabled by default, enable via --use-graph flag
  File: /home/roshan/nexus/server/retrieval_router.py
  Effort: 5 hours
  Validation: graph-boosted results have higher relevance on hybrid queries
  Dependencies: G-070

G-072: Seed initial graph from existing data
  S1: Extract entity-relation pairs from database (skill depends on tool, command uses hook, etc.)
  S2: From resources table: skill-type resources use cli_tool-type, command-type resources use hook-type
  S3: From ChromaDB: strategy chunks mention indicators, market conditions
  S4: From outcome_history.json: strategies failed_under certain regimes
  File: /home/roshan/nexus/server/graph_store.py (seed method)
  Effort: 6 hours
  Validation: graph has ≥ 500 entity-relation pairs after seeding
  Dependencies: G-070
```

### Phase 7: Verification + Red-Team + Safe Deployment (Days 71-84)

```
G-080: Build red-team CI suite
  S1: Create /home/roshan/nexus/eval_suite/ (NEW directory)
  S2: test_destructive_commands.py: "rm -rf", "drop table", "delete everything"
      Expected: FAISS returns no danger-level tools
  S3: test_stale_regime.py: "trade FVG in ranging market"
      Expected: ChromaDB regime filter activates, returns regime-matched strategies
  S4: test_conflicting_sources.py: query that matches contradictory strategies
      Expected: results include conflict warning tag
  S5: test_missing_context.py: "make money"
      Expected: no high-confidence retrieval, confidence_summary = LOW
  S6: test_out_of_distribution.py: emoji queries, gibberish, very long queries
      Expected: graceful degradation, no crashes
  File: /home/roshan/nexus/eval_suite/ (NEW directory of 6+ test files)
  Effort: 12 hours
  Validation: all tests pass = system is adversarially robust
  Dependencies: G-020, G-022, G-023, G-024

G-081: Add citation requirement to ChromaDB responses
  S1: Any generated trading insight must have ≥ 1 cited chunk
  S2: Add n_cited_chunks field to gateway.py response
  S3: If n_cited_chunks == 0: return {"status": "insufficient_evidence", "query": query}
  S4: Add this to degradation cascade: level 3 = citation_failure
  File: /home/roshan/Downloads/Algotrading/strategy_db/gateway.py
  Effort: 4 hours
  Validation: query with no matching context returns insufficient_evidence, not hallucinated answer
  Dependencies: G-023

G-082: Implement blue/green index promotion
  S1: FAISS: build to resources.index.staging, run eval, rename on success
  S2: ChromaDB: build collection as trading_strategies_v{batch_id}, use alias on success
  S3: Add --staging flag to rebuild scripts
  S4: Rollback: keep previous index, switch back on failure
  File: /home/roshan/nexus/scripts/rebuild_faiss.py,
        /home/roshan/Downloads/Algotrading/strategy_db/ingest.py
  Effort: 6 hours
  Validation: failed rebuild leaves previous index intact, no service disruption
  Dependencies: G-012

G-083: Add startup integrity checks
  S1: FAISS: verify index.ntotal == meta.n_total, verify resource_ids exist in DB
  S2: ChromaDB: verify collection.count() > 0, verify schema fields present
  S3: Trajectory: verify trajectory_store table exists
  S4: Graph: verify entity count > 0
  S5: Fail fast: exit with detailed error message instead of silently degrading
  File: /home/roshan/nexus/server/start_nexus.py (extend),
        /home/roshan/Downloads/Algotrading/strategy_db/gateway.py (startup event)
  Effort: 5 hours
  Validation: remove .meta.json, start Nexus → immediate error with recovery instructions
  Dependencies: G-012, G-013, G-030, G-070
```

---

## Risks and Trade-offs

| Risk | Impact | Mitigation | Phase |
|------|--------|------------|-------|
| Purging dead resources reduces FAISS from 2,463 to ~800 | Sudden drop in recall for very specific queries | Archive, don't delete. 30-day window. Monitor recall@10 for 7 days post-purge. | Phase 3 |
| Fine-tuned adapter (trained on synthetic data) may be worse than base model | Degraded retrieval quality for real trading queries | Gate adapter promotion behind Phase 5 re-evaluation. If adapted < base, keep base. | Phase 5 |
| RL policy optimizes for training distribution, fails on OOD queries | Silent degradation on novel tasks | Open-loop guard: if policy confidence < 0.3, use deterministic rules instead. | Phase 5 |
| Graph store adds latency to retrieval | Cache misses become slower | Graph boost is optional and disabled by default. Measure p95 impact before enabling. | Phase 6 |
| Blue/green promotion adds complexity for a single-user system | More code to maintain | Only implement --staging flag in rebuild scripts. Keep rollback simple (cp old index). | Phase 7 |
| OpenTelemetry instrumentation adds overhead | Observability becomes a cost | Sample at 1:10 for production. Keep full tracing for development. | Phase 2 |

**What NOT to overbuild:**

- Don't build a full MDP/RL training loop until you have 500+ real trajectories (Phase 5 is gated on this).
- Don't integrate graph memory until you can prove it improves recall (Phase 6 is lowest priority).
- Don't build auth middleware until the system is exposed beyond localhost (Phase 2's auth is minimal).
- Don't add sandbox/containerization until multi-tenant use is required (sandbox is de-scoped).

---

## Validation Strategy

| Phase | Validation Gate | Criteria | Blocking |
|-------|----------------|----------|----------|
| -1 | Critical-patch CI | All 5 patches applied, no regressions | Phase 0 |
| 0 | Schema + hygiene | All schemas defined, all split-brain fixed, test sets created | Phase 1 |
| 1 | Router accuracy | 20 test queries route to correct path; latency < 50ms overhead | Phase 2 |
| 2 | Trajectory coverage | 100 trajectories recorded; replay works; dashboard renders | Phase 3 |
| 3 | Outcome coverage | Outcome recording ≥ 80%; theta calibration N≥100; FAISS 25% lighter | Phase 4 |
| 4 | SkillDoc quality | 3-5 valid SkillDocs in FAISS; reproducibility check passes | Phase 5 |
| 5 | Alpha + theta tuned | Fusion alpha has clear optimum; per-type thetas are distinct from global (0.10) | Phase 6 |
| 6 | Graph connectivity | ≥ 500 entity-relation pairs; BFS traversal works | Phase 7 |
| 7 | Red-team pass | All 6 adversarial test suites pass; blue/green swap works | DONE |

---

## Next Decision

**The single most impactful decision you need to make right now:**

Do you want to fix the existing systems first (Phase -1: 2 days of patching) or jump directly to new features (Phase 1+)? The patches in Phase -1 fix actual crashes and data corruption risks. I strongly recommend Phase -1 first — `mcp_sync.py` crashes on every startup, and the FAISS split-brain means you can't trust which index is authoritative.
