# NEXUS Fix-All Issues — Architectural DAG & Stack Breakdown

> 14 tasks. 4 phases. 1 dependency graph. Everything below is the full execution blueprint.

---

## GLOBAL DEPENDENCY DAG

```
                        ┌─────────────────────────────────────────┐
                        │         PHASE 1 (P0 — Structural)       │
                        │      Must complete before P2/P3         │
                        └──────────────┬──────────────────────────┘
                                       │
            ┌──────────────────────────┼──────────────────────────┐
            │                          │                          │
     ┌──────▼──────┐          ┌───────▼───────┐         ┌───────▼───────┐
     │   TASK 1     │          │   TASK 2      │         │   TASK 3      │
     │ Sync Cluster │────┬────►│ Recluster     │────┬───►│ Assign Graph  │
     │ Counts       │    │    │ General→Proper │    │    │ ify Orphans   │
     └──────────────┘    │    └───────┬───────┘    │    └───────┬───────┘
                         │            │              │            │
                         │    ┌───────▼───────┐    │            │
                         │    │   TASK 4      │    │            │
                         │    │ Fix Debug     │    │            │
                         │    │ Routing       │    │            │
                         │    └───────┬───────┘    │            │
                         │            │              │            │
            ┌────────────▼────────────▼────────────▼────────────▼────────┐
            │              MERGE POINT: P1 Data Ready                    │
            │    All cluster assignments are correct. Orphans gone.      │
            └────────────┬────────────┬────────────┬─────────────────────┘
                         │            │            │
            ┌────────────▼──┐  ┌──────▼──────┐  ┌─▼────────────┐
            │   TASK 5      │  │   TASK 6    │  │   TASK 7      │
            │ Sync Strategy │  │ Purge Skill │  │ Fix Outcome  │
            │ -KB Tools     │  │ Shells      │  │ Tracking     │
            └───────┬───────┘  └──────┬──────┘  └──────┬───────┘
                    │                 │                 │
                    └────────┬────────┘                 │
                             │                          │
            ┌────────────────▼──────────────────────────▼────────┐
            │         MERGE POINT: P1 Complete                  │
            │    New resources in. Dead weight marked.           │
            │    Outcome tracking schema fixed.                  │
            └───┬────────────┬────────────┬──────────────────────┘
                │            │            │
       ┌───────▼──┐  ┌─────▼──────┐  ┌─▼────────────┐
       │ TASK 8   │  │  TASK 9    │  │  TASK 10      │
       │ Dedup    │  │ Upgrade    │  │ Cold-Storage  │
       │ Body Text│  │ Tier-3    │  │ Never-Triggered│
       └──────┬───┘  └─────┬────┘  └──────┬────────┘
              │            │               │
              └─────┬──────┘               │
                    │                      │
            ┌───────▼──────────────────────▼────────┐
            │         MERGE POINT: P2 Complete       │
            │    Duplicates gone. Stubs upgraded.    │
            │    Cold storage applied.                │
            └───────────────┬────────────────────────┘
                            │
                   ┌────────▼────────┐
                   │   TASK 11       │
                   │ Fix Self-       │
                   │ Reflection Loop │
                   └────────┬────────┘
                            │
            ┌───────────────▼─────────────────────────┐
            │     MERGE POINT: All Data Changes Done   │
            │     DB is the source of truth.           │
            │     Ready to rebuild materialized views.  │
            └───────────────┬─────────────────────────┘
                            │
                ┌───────────▼───────────┐
                │   TASK 12             │
                │   Rebuild FAISS Index │
                └───────────┬───────────┘
                            │
                ┌───────────▼───────────┐
                │   TASK 13             │
                │   Recompute Centroids │
                └───────────┬───────────┘
                            │
                ┌───────────▼───────────┐
                │   TASK 14             │
                │   Final Verification  │
                │   & Audit Report      │
                └───────────────────────┘
```

---

## PHASE 1: STRUCTURAL INTEGRITY (P0)

### Stack Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                     PHASE 1 STACK DIAGRAM                          │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │  API LAYER (nexus_http_daemon.py, MCP tools)                │  │
│  │  ┌─────────────┐ ┌──────────────┐ ┌──────────────────────┐ │  │
│  │  │cluster_list  │ │cluster_route │ │nexus_route_v4        │ │  │
│  │  │returns stale │ │reads from    │ │reads centroids+       │ │  │
│  │  │counts from   │ │resources.    │ │resources.cluster      │ │  │
│  │  │cluster_      │ │cluster col   │ │+ FAISS filtered by    │ │  │
│  │  │definitions   │ │              │ │activated clusters     │ │  │
│  │  └──────┬───────┘ └──────┬───────┘ └──────────┬───────────┘ │  │
│  │         │                │                      │             │  │
│  └─────────┼────────────────┼──────────────────────┼─────────────┘  │
│            │                │                      │                  │
│  ┌─────────▼────────────────▼──────────────────────▼─────────────┐  │
│  │  ROUTING LAYER (cluster_router.py, cluster_config.py)          │  │
│  │                                                                 │  │
│  │  cluster_config.py defines:                                    │  │
│  │  ┌──────────────────────────────────────────────────────────┐  │  │
│  │  │  CLUSTER_DEFINITIONS = {                                  │  │  │
│  │  │    "analyzer_planner": { expected_size: 120 },           │  │  │
│  │  │    "general":           { expected_size: 200 },           │  │  │
│  │  │    "quality_security":  { trigger_keywords: [...test...] }│  │  │
│  │  │    // BUG: "debug" NOT in analyzer_planner keywords      │  │  │
│  │  │  }                                                         │  │  │
│  │  └──────────────────────────────────────────────────────────┘  │  │
│  │                                                                 │  │
│  │  cluster_router.py routes:                                     │  │
│  │  Query → Encode → Cosine vs Centroids → Activate Top-K        │  │
│  │       ↓ Falls back to keyword match if semantic fails          │  │
│  └────────────────────────────┬──────────────────────────────────┘  │
│                               │                                     │
│  ┌────────────────────────────▼──────────────────────────────────┐  │
│  │  DATA LAYER (nexus.db — SQLite)                                │  │
│  │                                                                 │  │
│  │  ┌──────────────────────┐    ┌────────────────────────────┐  │  │
│  │  │  resources            │    │  cluster_definitions        │  │  │
│  │  │  ──────────────────  │    │  ──────────────────────    │  │  │
│  │  │  1,686 rows           │    │  8 rows (one per cluster)  │  │  │
│  │  │  cluster col = actual │    │  centroid_embedding blob   │  │  │
│  │  │  cluster_confidence   │    │  description text           │  │  │
│  │  │                       │    │                             │  │  │
│  │  │  BUG: 525 in general  │    │  BUG: claims 173 in general│  │  │
│  │  │  7 graphify = NULL    │    │  Stale from old clustering │  │  │
│  │  │  debug skills in       │    │                             │  │  │
│  │  │  quality_security      │    │                             │  │  │
│  │  └──────────┬───────────┘    └────────────────────────────┘  │  │
│  │             │                                                  │  │
│  │  ┌──────────▼───────────┐    ┌────────────────────────────┐  │  │
│  │  │ resource_embeddings   │    │  cluster_membership         │  │  │
│  │  │ ───────────────────  │    │  ──────────────────────    │  │  │
│  │  │ 1,686 vectors         │    │  1,679 mapped rows          │  │  │
│  │  │  384-dim float32      │    │  7 NULL (graphify orphans)  │  │  │
│  │  │  all-MiniLM-L6-v2    │    │                             │  │  │
│  │  └──────────────────────┘    └────────────────────────────┘  │  │
│  │                                                                 │  │
│  └─────────────────────────────────────────────────────────────────┘  │
│                                                                       │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │  VECTOR LAYER (FAISS)                                          │  │
│  │  ┌────────────────────────────┐                                │  │
│  │  │  resources.index            │  ← 1,686 vectors              │  │
│  │  │  IndexFlatIP(384)          │  ← L2-normalized for cosine   │  │
│  │  │  resources.index.meta.json │  ← ID→resource mapping         │  │
│  │  └────────────────────────────┘                                │  │
│  │  NOT rebuilt until Phase 4 (Task 12)                           │  │
│  └─────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

### Task 1: Sync cluster_definitions with actual resource counts

```
INPUT PIPELINE:
  resources.cluster (actual distribution)
      │
      ▼
  COUNT(*) GROUP BY cluster
      │
      ▼
  COMPARE WITH cluster_definitions.centroid_embedding row counts
      │
      ▼
  UPDATE cluster_definitions rows for all 8 clusters

DATA FLOW:
  ┌──────────────┐     SQL      ┌─────────────────────┐
  │ resources     │──COUNT(*)──►│ Verification Output │
  │ .cluster col │             │ Shows claim vs      │
  │              │             │ actual for each     │
  └──────┬───────┘             └─────────────────────┘
         │
         │  Also ensures all 8 cluster_definitions 
         │  rows exist (INSERT if missing)
         │
         ▼
  ┌──────────────────────┐
  │ cluster_definitions   │  ← NOW: 8 rows, all present
  │ synced with reality   │
  └──────────────────────┘

BEFORE:                                AFTER:
  general: claims 173, has 525          general: claims 525, has 525
  analyzer_planner: claims 336, has 151  analyzer_planner: claims 151, has 151
  devops_infra: claims 192, has 89       devops_infra: claims 89, has 89

VERIFICATION:
  mcp_nexus_nexus_cluster_list() → counts match resources table
```

### Task 2: Recluster 382 low-confidence resources out of general

```
ALGORITHM (existing recluster_low_confidence.py):

  FOR each resource WITH cluster_confidence < 0.5:
    │
    ├─ Load resource embedding (384-dim from resource_embeddings)
    │
    ├─ Compute cosine similarity to ALL 8 cluster centroids
    │   (loaded from cluster_definitions.centroid_embedding)
    │
    ├─ Assign to cluster with HIGHEST similarity
    │
    ├─ IF max similarity < 0.3 (orphan threshold):
    │     → keep in 'general' (true orphan, can't classify)
    │
    └─ UPDATE resources SET cluster=?, cluster_confidence=?

DATA FLOW:
  ┌─────────────────┐    cosine sim    ┌──────────────────┐
  │ Resource Embed   │───────────────► │ 8 Cluster         │
  │ (384-dim vector) │                 │ Centroids          │
  │                  │                 │ (from cluster_      │
  │                  │                 │  definitions)       │
  └─────────────────┘                 └────────────────────┘
         │                                      │
         │         ┌────────────────┐            │
         └────────►│ BEST MATCH     │◄───────────┘
                   │ cluster assign │
                   │ + confidence   │
                   └───────┬────────┘
                           │
                    ┌──────▼──────┐
                    │ UPDATE DB   │
                    │ 382 rows    │
                    └─────────────┘

PROJECTED RESULT:
  general: 525 → ~150-200 (true orphans + unclassifiable)
  analyzer_planner: +~60
  architect: +~50
  frontend_ui: +~40
  backend_api: +~30
  devops_infra: +~20
  quality_security: +~20
  knowledge_wiki: +~30

CONFIDENCE IMPROVEMENT:
  general avg: 0.43 → ~0.60+ (only high-confidence orphans remain)
  all clusters: avg increase ~0.10-0.15
```

### Task 3: Assign 7 orphan graphify MCP tools

```
AFFECTED RESOURCES:
  graphify-query_graph     → knowledge_wiki (0.80)
  graphify-get_node         → knowledge_wiki (0.80)
  graphify-get_neighbors    → knowledge_wiki (0.80)
  graphify-get_community    → knowledge_wiki (0.80)
  graphify-god_nodes        → knowledge_wiki (0.80)
  graphify-graph_stats      → knowledge_wiki (0.80)
  graphify-shortest_path    → knowledge_wiki (0.80)

MAPPING RATIONALE:
  ┌─────────────────────────────────────────────────────────┐
  │  graphify tools = knowledge graph traversal              │
  │  knowledge_wiki = "Documentation, wikis, learning,      │
  │                    context management, and content"      │
  │  → Natural fit for knowledge retrieval cluster           │
  │                                                          │
  │  Alternative considered:                                 │
  │  analyzer_planner = "Reasoning, analysis, investigate"   │
  │  → Could also fit, but graph traversal is more           │
  │    "querying knowledge" than "analyzing"                 │
  └─────────────────────────────────────────────────────────┘

SQL:
  UPDATE resources
  SET cluster = 'knowledge_wiki',
      cluster_confidence = 0.80,
      updated_at = datetime('now')
  WHERE name LIKE 'graphify%'
    AND (cluster IS NULL OR cluster = '' OR cluster = 'general')
    AND status = 'active'

BEFORE: 7 resources with cluster=NULL
AFTER:  7 resources with cluster='knowledge_wiki', 0 orphans remain
```

### Task 4: Fix debug → analyzer_planner routing

```
CURRENT BUG PATH:
  User Query: "debug TypeError null pointer"
       │
       ▼
  ClusterRouter._compute_affinities()
       │
       ├── Semantic: query embedding vs centroids
       │   → quality_security scores HIGH (0.41)
       │   → analyzer_planner scores LOWER
       │
       ├── Keyword fallback: "debug" keyword
       │   → ONLY in quality_security.trigger_keywords
       │   → NOT in analyzer_planner.trigger_keywords
       │
       └── Result: quality_security wins ❌

FIX (2-pronged):

  1. CLUSTER_CONFIG.PY — Add trigger keywords:
  ┌─────────────────────────────────────────────────────────┐
  │  analyzer_planner.trigger_keywords += [                 │
  │    "debug", "investigate", "root-cause", "trace",       │
  │    "error recovery", "diagnose"                          │
  │  ]                                                       │
  │                                                          │
  │  quality_security.trigger_keywords — REMOVE:             │
  │    "debug" (if present, keep "testing" but not "debug")  │
  └─────────────────────────────────────────────────────────┘

  2. DB — Move misclassified resources:
  ┌─────────────────────────────────────────────────────────┐
  │  resources WHERE cluster='quality_security'             │
  │    AND name LIKE '%debug%'                               │
  │    AND name LIKE '%investig%'                            │
  │    AND name LIKE '%error%'                              │
  │    AND name LIKE '%root-cause%'                         │
  │    AND name LIKE '%systematic-debug%'                    │
  │                                                          │
  │  → SET cluster = 'analyzer_planner'                      │
  └─────────────────────────────────────────────────────────┘

VERIFICATION:
  ClusterRouter.route_to_clusters("debug TypeError null pointer")
  → Expected: analyzer_planner (affinity > quality_security)
```

---

## PHASE 2: DATA HYGIENE (P1)

### Stack Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                     PHASE 2 STACK DIAGRAM                          │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │  SYNC LAYER                                                 │  │
│  │                                                               │  │
│  │  ┌──────────────┐  ┌─────────────┐  ┌────────────────────┐  │  │
│  │  │ Strategy-KB   │  │ Skill Shell  │  │ Outcome Tracking   │  │  │
│  │  │ MCP Server    │  │ Detector     │  │ Pipeline           │  │  │
│  │  │               │  │               │  │                    │  │  │
│  │  │ 8 live tools: │  │ Scans:       │  │ record_outcome()   │  │  │
│  │  │ · query_strat │  │ ~/.hermes/   │  │ → routing_log       │  │  │
│  │  │ · get_strat   │  │  skills/     │  │ → resources.        │  │  │
│  │  │ · regime_det  │  │              │  │   times_triggered   │  │  │
│  │  │ · strat_ctx   │  │ Checks:      │  │ → resources.        │  │  │
│  │  │ · list_setup  │  │ SKILL.md     │  │   times_correct     │  │  │
│  │  │ · list_market │  │ exists?      │  │ → ReflectionEngine  │  │  │
│  │  │ · strat_stats │  │              │  │   .propose_fix()    │  │  │
│  │  │ · outcome_sync│  │ 159 shells   │  │                    │  │  │
│  │  │               │  │ have NO file │  │ BUG: ide CHECK      │  │  │
│  │  │ NOT IN DB ❌  │  │              │  │ only allows         │  │  │
│  │  │               │  │ Mark as      │  │ opencode/           │  │  │
│  │  │ → INSERT     │  │ 'degraded'   │  │ claude-code/        │  │  │
│  │  │ → Embed      │  │              │  │ unknown             │  │  │
│  │  │ → Cluster    │  │ → status=    │  │                    │  │  │
│  │  │              │  │   'degraded' │  │ → DROP CHECK        │  │  │
│  │  └──────────────┘  └─────────────┘  └────────────────────┘  │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                       │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │  DB STATE AFTER PHASE 2                                      │  │
│  │                                                               │  │
│  │  resources table changes:                                     │  │
│  │  ┌──────────────────────────────────────────────────────────┐│  │
│  │  │ +8 strategy-kb MCP tools (status=active, cluster set)   ││  │
│  │  │ 159 skills: status='active' → 'degraded'                ││  │
│  │  │ Net active: 1686 - 159 + 8 = 1535                        ││  │
│  │  └──────────────────────────────────────────────────────────┘│  │
│  │                                                               │  │
│  │  routing_log table changes:                                   │  │
│  │  ┌──────────────────────────────────────────────────────────┐│  │
│  │  │ Old: routing_log (CHECK ide IN ('opencode',...))         ││  │
│  │  │ New: routing_log (ide TEXT — no constraint)              ││  │
│  │  └──────────────────────────────────────────────────────────┘│  │
│  └───────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

### Task 5: Sync strategy-kb MCP tools into NEXUS resource DB

```
SYNC PIPELINE:

  ┌────────────────────┐       ┌─────────────────────┐
  │   MCP Tool Server  │       │   NEXUS resources    │
  │   (strategy-kb)    │       │   table               │
  │                    │       │                       │
  │ 8 callable tools:  │       │ BEFORE: 0 entries    │
  │ · query_strategies │──────►│ AFTER:  8 entries    │
  │ · get_strategy     │INSERT │                       │
  │ · regime_detect    │       │ Each entry needs:     │
  │ · strategy_context │       │ · name (string)      │
  │ · list_setup_types │       │ · description (text)  │
  │ · list_market_conds│       │ · body_text (text)    │
  │ · strategy_stats   │       │ · resource_type='mcp' │
  │ · outcome_sync     │       │ · repo_name='strategy │
  │                    │       │   -kb'               │
  └────────────────────┘       │ · cluster (assigned)  │
                               │ · tier = 2            │
  ┌────────────────────┐       │ · embedding (384-dim) │
  │   IsoEmbedder      │       └──────────────────────┘
  │   all-MiniLM-L6-v2 │              ▲
  │                    │              │
  │   description +    │─────embed───►│
  │   body_text →      │              │
  │   384-dim vector   │     resource_embeddings
  └────────────────────┘     table (separate)

TOOL → CLUSTER MAPPING:
  ┌──────────────────────────────────┬─────────────────────┐
  │ Tool                             │ Assigned Cluster     │
  ├──────────────────────────────────┼─────────────────────┤
  │ strategy-kb-query_strategies     │ knowledge_wiki       │
  │ strategy-kb-get_strategy         │ knowledge_wiki       │
  │ strategy-kb-regime_detect        │ analyzer_planner     │
  │ strategy-kb-strategy_context     │ analyzer_planner     │
  │ strategy-kb-list_setup_types     │ knowledge_wiki       │
  │ strategy-kb-list_market_conds    │ knowledge_wiki       │
  │ strategy-kb-strategy_stats       │ knowledge_wiki       │
  │ strategy-kb-outcome_sync         │ quality_security     │
  └──────────────────────────────────┴─────────────────────┘

POST-INSERT STEPS:
  1. Insert 8 rows into resources table
  2. Generate 384-dim embeddings for each (IsoEmbedder)
  3. Insert 8 rows into resource_embeddings table
  4. Verify cluster_route returns them for trading queries
```

### Task 6: Purge 159 skill shells

```
DETECTION ALGORITHM:

  FOR each resource WHERE resource_type='skill' AND status='active':
    │
    ├─ Check: does ~/.hermes/skills/{name}/SKILL.md exist?
    │     ├─ YES → keep as active
    │     └─ NO  → Check: does {name.replace('-','/')}/SKILL.md exist?
    │           ├─ YES → keep as active
    │           └─ NO  → Check: does resources.file_path exist?
    │                 ├─ YES → keep as active
    │                 └─ NO  → MARK AS DEGRADED
    │
    └─ Result: 159 skills → status='degraded'

STATUS LIFECYCLE:
  active → degraded → archived → (deleted)

  degraded means:
    · Still in DB for reference
    · EXCLUDED from FAISS search (status filter)
    · EXCLUDED from cluster routing
    · NOT returned in route results
    · CAN be reactivated if skill file appears later

IMPACT ON ROUTING:
  ┌───────────────────────────────────────────────────────┐
  │  BEFORE: 1,686 active resources → 1,686 in FAISS     │
  │  AFTER:  1,527 active resources → 1,527 in FAISS      │
  │           (after rebuild in Phase 4)                   │
  │                                                        │
  │  159 degraded resources still in DB but excluded from: │
  │  · Cluster membership queries (WHERE status='active') │
  │  · FAISS index (rebuilt from active only)             │
  │  · Route results (filtered by status)                  │
  └───────────────────────────────────────────────────────┘
```

### Task 7: Fix outcome tracking pipeline (ide CHECK constraint)

```
CURRENT BUG:

  routing_log table schema:
  ┌──────────────────────────────────────────────────────┐
  │ CREATE TABLE routing_log (                           │
  │   id INTEGER PRIMARY KEY,                            │
  │   query TEXT,                                         │
  │   project TEXT,                                       │
  │   ide TEXT CHECK(ide IN (                             │
  │     'opencode', 'claude-code', 'unknown'    ◄── BUG  │
  │   )),                                                  │
  │   routed_to TEXT,                                     │
  │   ...                                                  │
  │ )                                                      │
  │                                                         │
  │  When Hermes agent calls report_outcome,               │
  │  ide = 'hermes-agent' or 'nexus-daemon'                │
  │  → INSERT FAILS silently                              │
  │  → outcome never recorded                             │
  │  → 17/20 top resources show 0% accuracy               │
  └──────────────────────────────────────────────────────┘

MIGRATION (3-step table swap):

  Step 1: CREATE TABLE routing_log_v2 (no CHECK)
  ┌──────────────────────────────────────┐
  │ CREATE TABLE routing_log_v2 (       │
  │   id INTEGER PRIMARY KEY,            │
  │   query TEXT,                         │
  │   project TEXT,                       │
  │   ide TEXT,              ◄── no CHECK│
  │   routed_to TEXT,                     │
  │   routed_id INTEGER,                  │
  │   alternatives TEXT,                  │
  │   scores TEXT,                        │
  │   model TEXT,                         │
  │   outcome TEXT,                       │
  │   latency_ms REAL,                    │
  │   created_at TEXT                     │
  │ )                                      │
  └──────────────────────────────────────┘

  Step 2: MIGRATE data
    INSERT INTO routing_log_v2 SELECT * FROM routing_log

  Step 3: SWAP tables
    DROP TABLE routing_log
    ALTER TABLE routing_log_v2 RENAME TO routing_log

  No data loss. All 333 existing rows preserved.
  ide column now accepts any string value.

POST-FIX DATA FLOW:
  ┌──────────────────┐    ┌─────────────────┐    ┌─────────────────┐
  │ Hermes Agent      │    │ routing_log      │    │ resources table  │
  │ calls             │    │ (no constraint)  │    │                 │
  │ report_outcome()  │───►│ ide = 'hermes-   │───►│ times_triggered │
  │                  │    │        agent'    │    │ times_correct   │
  │                  │    │ ✓ INSERT works   │    │ times_wrong      │
  │                  │    │                  │    │                 │
  │ ALSO triggers:   │    │ ALSO:            │    │ ALSO updates:   │
  │                  │    │ reflection_       │    │ status →        │
  │                  │    │ failures table   │    │ 'degraded' if   │
  │                  │    │ (if outcome=     │    │ error rate > 40%│
  │                  │    │  'wrong')        │    │                 │
  └──────────────────┘    └─────────────────┘    └─────────────────┘
```

---

## PHASE 3: DATA QUALITY (P2)

### Stack Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                     PHASE 3 STACK DIAGRAM                          │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────┐    │
│  │  QUALITY LAYER — Signal Enhancement                       │    │
│  │                                                             │    │
│  │  ┌───────────────┐ ┌──────────────┐ ┌──────────────────┐ │    │
│  │  │ TASK 8         │ │ TASK 9       │ │ TASK 10          │ │    │
│  │  │ Dedup          │ │ Tier-3       │ │ Cold Storage     │ │    │
│  │  │ Body Text      │ │ Upgrades     │ │ Never-Triggered  │ │    │
│  │  │                │ │              │ │                  │ │    │
│  │  │ 7 groups:     │ │ 15 MCP stubs │ │ 1,147 resources  │ │    │
│  │  │ reviewer/     │ │ with 1-line  │ │ with             │ │    │
│  │  │ tester/       │ │ descriptions │ │ times_triggered  │ │    │
│  │  │ coder/        │ │ → upgrade to │ │ = 0             │ │    │
│  │  │ researcher    │ │ full         │ │ → status='cold'  │ │    │
│  │  │ agents, etc.  │ │ descriptions │ │                  │ │    │
│  │  │               │ │              │ │ Keep: recently   │ │    │
│  │  │ Keep shortest │ │ Embed each   │ │ added (< 90      │ │    │
│  │  │ name,         │ │ with IsoEmbed│ │ days) even if    │ │    │
│  │  │ deprecate     │ │ der         │ │ never triggered   │ │    │
│  │  │ rest          │ │              │ │                  │ │    │
│  │  └───────┬───────┘ └──────┬───────┘ └───────┬──────────┘ │    │
│  │          │                 │                  │             │    │
│  │          └────────┬────────┘                  │             │    │
│  │                   │                            │             │    │
│  └───────────────────┼────────────────────────────┼─────────────┘    │
│                      │                            │                   │
│  ┌───────────────────▼────────────────────────────▼──────────────┐  │
│  │  FEEDBACK LAYER — Self-Reflection Fix                        │  │
│  │                                                                │  │
│  │  ┌────────────────────────────────────────────────────────┐  │  │
│  │  │  TASK 11: Fix self-reflection loop                      │  │  │
│  │  │                                                          │  │  │
│  │  │  CURRENT BROKEN FLOW:                                   │  │  │
│  │  │  record_outcome('wrong')                                │  │  │
│  │  │    → Update times_triggered/correct/wrong  ✓            │  │  │
│  │  │    → Check degraded threshold            ✓              │  │  │
│  │  │    → Record failure in reflection_failures ✓            │  │  │
│  │  │    → Call propose_fix()                 ✗ NEVER CALLED  │  │  │
│  │  │                                                          │  │  │
│  │  │  FIXED FLOW:                                             │  │  │
│  │  │  record_outcome('wrong')                                │  │  │
│  │  │    → Update times_triggered/correct/wrong  ✓            │  │  │
│  │  │    → Check degraded threshold            ✓              │  │  │
│  │  │    → Record failure                       ✓              │  │  │
│  │  │    → ReflectionEngine.propose_fix()      ✓ NEW          │  │  │
│  │  │       → Searches for similar past fixes                 │  │  │
│  │  │       → Returns suggested alternatives                  │  │  │
│  │  │       → Logs proposed fix in reflection_fixes           │  │  │
│  │  └────────────────────────────────────────────────────────┘  │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                       │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │  STATUS SUMMARY AFTER PHASE 3                                  │  │
│  │                                                                 │  │
│  │  resources table:                                               │  │
│  │  ┌───────────────────────────────────────────────────────────┐  │  │
│  │  │ status='active':    ~390 (was 1,686)                      │  │  │
│  │  │ status='degraded':  ~165 (new: 159 shells + 7 deduped)   │  │  │
│  │  │ status='cold':      ~1,050 (never-triggered > 90 days)    │  │  │
│  │  │ status='deprecated': ~7 (body_text duplicates)             │  │  │
│  │  │ +8 new strategy-kb entries                                 │  │  │
│  │  └───────────────────────────────────────────────────────────┘  │  │
│  │                                                                 │  │
│  │  routing_log: No CHECK constraint on ide column                 │  │
│  │  reflection_failures ↔ reflection_fixes: Now linked             │  │
│  └─────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

### Task 8: Deduplicate 7 body_text groups

```
DUPLICATE GROUPS:

  Group 1: Agent duplicates (reviewer/tester/coder/researcher)
  ┌──────────────────────────────────────────────────────────────┐
  │  reviewer     ← KEEPER (shortest canonical name)             │
  │  tester        → DEPRECATE (duplicate body)                   │
  │  coder         → DEPRECATE (duplicate body)                   │
  │  researcher    → DEPRECATE (duplicate body)                   │
  └──────────────────────────────────────────────────────────────┘

  Group 2: vibeyard / vibeyard-ide
  ┌──────────────────────────────────────────────────────────────┐
  │  vibeyard     ← KEEPER                                       │
  │  vibeyard-ide  → DEPRECATE                                    │
  └──────────────────────────────────────────────────────────────┘

  Group 3: insforge / insforge-baas
  ┌──────────────────────────────────────────────────────────────┐
  │  insforge      ← KEEPER                                      │
  │  insforge-baas → DEPRECATE                                    │
  └──────────────────────────────────────────────────────────────┘

  ... (4 more similar groups)

ALGORITHM:
  1. GROUP BY body_text
  2. For each group with COUNT > 1:
     a. Find the resource with the SHORTEST name (canonical)
     b. Mark all others as status='deprecated'
  3. Deprecated resources stay in DB but excluded from routing

IMPACT ON FAISS:
  These will be excluded from the rebuilt FAISS index (Phase 4)
  since the rebuild only includes status='active' resources.
```

### Task 9: Upgrade 15 tier-3 MCP stub descriptions

```
TIER-3 MCP STUBS (current state):
  ┌────────────────────────────────────────────────────────────┐
  │  Name                  │ Description     │ Tier │ Cluster  │
  │  clickhouse            │ "ClickHouse an"│  3   │ general  │
  │  github                │ "GitHub API"   │  3   │ devops   │
  │  firecrawl             │ "Web scraping" │  3   │ general  │
  │  playwright            │ "Browser test" │  3   │ general  │
  │  ... (11 more)         │                │      │          │
  └────────────────────────────────────────────────────────────┘

UPGRADE PIPELINE:
  ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐
  │ Current Stub │    │ Hand-Written │    │ Re-embedded in   │
  │ Description  │───►│ Full         │───►│ resource_embed-  │
  │ (1 line)     │    │ Description  │    │ dings table      │
  │              │    │ (3-5 lines)  │    │ (384-dim vector) │
  └──────────────┘    └──────────────┘    └──────────────────┘

TIER UPGRADE:
  tier 3 → tier 2 (now callable with proper metadata)

CLUSTER REASSIGNMENT:
  Some currently in 'general' should move to specific clusters
  based on their actual function (e.g., github → devops_infra)
```

### Task 10: Cold-storage never-triggered resources

```
COLD STORAGE CRITERIA:
  ┌─────────────────────────────────────────────────────────────┐
  │  resources WHERE:                                           │
  │    status = 'active'                                        │
  │    AND times_triggered = 0                                   │
  │    AND created_at < datetime('now', '-90 days')             │
  │                                                             │
  │  → SET status = 'cold'                                      │
  │                                                             │
  │  EXCEPTIONS (kept active even if never triggered):          │
  │    - Resources created < 90 days ago (too new)              │
  │    - MCP tools (always callable)                            │
  │    - Resources with cluster_confidence > 0.7                 │
  └─────────────────────────────────────────────────────────────┘

DISTRIBUTION OF NEVER-TRIGGERED (by type):
  ┌────────────────┬───────────┬──────────────┐
  │ Type           │ Never     │ Total        │
  ├────────────────┼───────────┼──────────────┤
  │ skill          │ 478       │ 687          │
  │ command        │ 203       │ 268          │
  │ design_system  │ 122       │ 151          │
  │ agent          │ 117       │ 169          │
  │ doc            │ 89        │ 124          │
  │ cli_tool       │ 66        │ 131          │
  │ hook           │ 47        │ 69           │
  │ mcp            │ 36        │ 63           │
  │ spec           │ 10        │ 12           │
  │ craft          │ 9         │ 12           │
  └────────────────┴───────────┴──────────────┘

LIFECYCLE:
  active → cold → (manually review) → active or archived

  'cold' means:
    · Still in DB, NOT deleted
    · Excluded from FAISS search index
    · Excluded from cluster routing
    · NOT returned in any query results
    · CAN be reactivated: UPDATE status='active' WHERE name='...'
    · Visible in admin queries: WHERE status='cold'
```

### Task 11: Fix self-reflection loop

```
CURRENT vs FIXED ARCHITECTURE:

  ┌──────────────────────────────────────────────────────────────┐
  │  CURRENT (BROKEN):                                           │
  │                                                              │
  │  record_outcome('wrong')                                     │
  │    │                                                         │
  │    ├── Update resources.times_triggered/wrong      ✓         │
  │    ├── Update routing_log.outcome                  ✓         │
  │    ├── Check degraded threshold                    ✓         │
  │    ├── Insert into reflection_failures              ✓         │
  │    │                                                         │
  │    ══ END ══  (propose_fix is never called)        ✗         │
  │                                                              │
  │  Result: 27 failures, 0 fixes                               │
  └──────────────────────────────────────────────────────────────┘

  ┌──────────────────────────────────────────────────────────────┐
  │  FIXED:                                                     │
  │                                                              │
  │  record_outcome('wrong')                                     │
  │    │                                                         │
  │    ├── Update resources.times_triggered/wrong      ✓         │
  │    ├── Update routing_log.outcome                  ✓         │
  │    ├── Check degraded threshold                    ✓         │
  │    ├── Insert into reflection_failures              ✓         │
  │    │                                                         │
  │    ├── ReflectionEngine.propose_fix()             ✓ NEW     │
  │    │     │                                                   │
  │    │     ├── Search for similar past failures (semantic)     │
  │    │     ├── Look up past fixes that succeeded              │
  │    │     ├── Find alternative resources for same task type    │
  │    │     └── Return: {                                        │
  │    │           suggested_alternatives: [...],                 │
  │    │           past_fixes: [...],                              │
  │    │           confidence: 0.xx                               │
  │    │         }                                                │
  │    │                                                         │
  │    └── Log proposed fix in reflection_fixes         ✓ NEW   │
  │                                                              │
  │  Result: failures → alternatives → learning loop closes      │
  └──────────────────────────────────────────────────────────────┘

CODE CHANGE (server/learning.py):

  After the degraded threshold check block:
  ```python
  # Layer 3: Self-Reflection — propose alternatives on failure
  if outcome in ("wrong", "error"):
      try:
          from reflection import ReflectionEngine
          reflector = ReflectionEngine(str(DB_PATH))
          fix = reflector.propose_fix(
              task=task_summary or "unknown",
              resource_name=skill_name,
              root_cause=f"outcome={outcome}",
          )
          if fix:
              logger.info(
                  f"Reflection proposed fix for {skill_name}: "
                  f"{fix.get('suggested_alternatives', [])[:3]}"
              )
      except Exception as e:
          logger.warning(f"Self-reflection failed: {e}")
  ```

VERIFICATION:
  1. Call record_outcome('test-skill', 'wrong')
  2. Check reflection_fixes table for new entry
  3. Confirm suggested_alternatives is populated
```

---

## PHASE 4: REBUILD & VERIFY (Final)

### Stack Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                     PHASE 4 STACK DIAGRAM                          │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  MATERIALIZED VIEW REBUILDS                                  │  │
│  │                                                               │  │
│  │  All prior phases modified resources in the DB.              │  │
│  │  The FAISS index and cluster centroids are DERIVED data      │  │
│  │  that must be recomputed from the now-correct DB state.      │  │
│  │                                                               │  │
│  │  ┌──────────────────────┐    ┌──────────────────────────┐  │  │
│  │  │ nexus.db              │    │ Derived Artifacts         │  │  │
│  │  │ (source of truth)    │    │                           │  │  │
│  │  │                      │    │ ┌──────────────────────┐ │  │  │
│  │  │ 1,527 active rows    │───►│ │ FAISS Index          │ │  │  │
│  │  │  ~165 degraded       │    │ │ resources.index      │ │  │  │
│  │  │ ~1,050 cold          │    │ │ 1,527 vectors        │ │  │  │
│  │  │ ~7 deprecated        │    │ │ (was 1,686)          │ │  │  │
│  │  │ +8 strategy-kb       │    │ │ + 8 strategy-kb      │ │  │  │
│  │  │                      │    │ │ = 1,535 vectors       │ │  │  │
│  │  │ Clean clusters:     │    │ └──────────────────────┘ │  │  │
│  │  │ general: ~150       │    │                           │  │  │
│  │  │ analyzer_planner:   │    │ ┌──────────────────────┐ │  │  │
│  │  │   ~200 (+debug)     │    │ │ Cluster Centroids     │ │  │  │
│  │  │ others: correct     │    │ │ cluster_definitions   │ │  │  │
│  │  └──────────────────────┘    │ │ 8 × 384-dim vectors │ │  │  │
│  │                               │ │ Recomputed from      │ │  │  │
│  │                               │ │ new distribution     │ │  │  │
│  │                               │ └──────────────────────┘ │  │  │
│  │                               │                           │  │  │
│  │                               │ ┌──────────────────────┐ │  │  │
│  │                               │ │ Cluster Membership   │ │  │  │
│  │                               │ │ (implicit via        │ │  │  │
│  │                               │ │  resources.cluster   │ │  │  │
│  │                               │ │  column)             │ │  │  │
│  │                               │ └──────────────────────┘ │  │  │
│  └──────────────────────────────┘    └──────────────────────────┘  │
│                                                                       │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  VERIFICATION SUITE                                          │  │
│  │                                                               │  │
│  │  ┌─────────────────────────────────────────────────────────┐ │  │
│  │  │ Phase 1 Checks:                                         │ │  │
│  │  │ ✓ cluster_definitions counts match resources.cluster   │ │  │
│  │  │ ✓ general cluster < 200 (was 525)                      │ │  │
│  │  │ ✓ 0 orphan resources (unassigned cluster)              │ │  │
│  │  │ ✓ debug queries route to analyzer_planner              │ │  │
│  │  └─────────────────────────────────────────────────────────┘ │  │
│  │                                                               │  │
│  │  ┌─────────────────────────────────────────────────────────┐ │  │
│  │  │ Phase 2 Checks:                                         │ │  │
│  │  │ ✓ 8 strategy-kb tools visible in route results          │ │  │
│  │  │ ✓ 0 skill shells with status='active' and missing file │ │  │
│  │  │ ✓ routing_log.ide accepts 'hermes-agent' etc.          │ │  │
│  │  └─────────────────────────────────────────────────────────┘ │  │
│  │                                                               │  │
│  │  ┌─────────────────────────────────────────────────────────┐ │  │
│  │  │ Phase 3 Checks:                                         │ │  │
│  │  │ ✓ 0 duplicate body_text groups among active resources   │ │  │
│  │  │ ✓ All tier-3 MCP entries have descriptions > 50 chars  │ │  │
│  │  │ ✓ ~1,050 resources with status='cold'                  │ │  │
│  │  │ ✓ reflection_fixes table has entries after wrong outcome│ │  │
│  │  └─────────────────────────────────────────────────────────┘ │  │
│  │                                                               │  │
│  │  ┌─────────────────────────────────────────────────────────┐ │  │
│  │  │ Phase 4 Checks:                                         │ │  │
│  │  │ ✓ FAISS index vector count = active resource count     │ │  │
│  │  │ ✓ All 8 centroids recomputed (384-dim each)            │ │  │
│  │  │ ✓ route_to_clusters() for 11 smoke queries = 11/11    │ │  │
│  │  │ ✓ Overall health grade: A- or higher                   │ │  │
│  │  └─────────────────────────────────────────────────────────┘ │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

### Task 12: Rebuild FAISS index

```
REBUILD PIPELINE:

  ┌─────────────────────┐
  │ nexus.db             │
  │                      │
  │ SELECT re.resource_id│
  │       re.embedding   │
  │       r.name         │
  │       r.status       │
  │ FROM resource_       │
  │   embeddings re      │
  │ JOIN resources r     │
  │   ON re.resource_id  │
  │       = r.id         │
  │ WHERE re.embedding   │
  │       IS NOT NULL    │
  │   AND r.status =     │
  │       'active'       │
  └──────────┬──────────┘
             │
             │  ~1,535 rows (was 1,686)
             │  (excludes degraded + cold + deprecated)
             │
             ▼
  ┌─────────────────────┐     ┌─────────────────────┐
  │ Deserialize blobs   │     │ Validate dims       │
  │ float32 vectors     │────►│ len(vec) == 384     │
  │                     │     │                     │
  │ Filter: status=     │     │ Skip: dim mismatch   │
  │  'active' only      │     │ or NULL embedding   │
  └──────────┬──────────┘     └─────────────────────┘
             │
             ▼
  ┌─────────────────────┐
  │ L2 Normalize        │
  │ (required for       │
  │  IndexFlatIP =      │
  │  cosine similarity) │
  └──────────┬──────────┘
             │
             ▼
  ┌─────────────────────┐
  │ Build IndexFlatIP   │
  │ index.add(embeddings)│
  │                     │
  │ Result:             │
  │  ~1,535 vectors     │
  │  dim = 384           │
  │  type = IndexFlatIP │
  └──────────┬──────────┘
             │
             ▼
  ┌─────────────────────┐
  │ Write to disk:       │
  │  db/faiss/           │
  │  resources.index     │
  │  resources.index.    │
  │  meta.json           │
  │                      │
  │ meta.json contains:  │
  │  resource_ids: [...] │
  │  dim: 384             │
  │  model: all-MiniLM...│
  │  n_total: 1535        │
  │  built_at: timestamp  │
  └──────────┬──────────┘
             │
             ▼
  ┌─────────────────────┐
  │ Verify round-trip:   │
  │  Load index back     │
  │  assert ntotal=1535  │
  │  assert meta matches │
  └─────────────────────┘

EXISTS AS: scripts/rebuild_faiss.py (no modifications needed)
RUN: ./venv/bin/python scripts/rebuild_faiss.py
```

### Task 13: Recompute cluster centroids

```
CENTROID RECOMPUTATION:

  For each cluster c in [analyzer_planner, architect, frontend_ui,
                         backend_api, devops_infra, quality_security,
                         knowledge_wiki, general]:

  ┌─────────────────────────────────────────────────────────────────┐
  │ centroid(c) = MEAN( embeddings of all active resources          │
  │                     WHERE cluster = c )                          │
  │                                                                  │
  │ 1. SELECT r.id, re.embedding                                    │
  │    FROM resources r                                              │
  │    JOIN resource_embeddings re ON re.resource_id = r.id          │
  │    WHERE r.cluster = c AND r.status = 'active'                   │
  │                                                                  │
  │ 2. Stack all embeddings into matrix M                            │
  │ 3. centroid = M.mean(axis=0)  # 384-dim vector                  │
  │ 4. L2-normalize centroid                                         │
  │ 5. UPDATE cluster_definitions                                   │
  │    SET centroid_embedding = ? WHERE name = c                     │
  └─────────────────────────────────────────────────────────────────┘

AFTER RECOMPUTATION:
  ┌──────────────────────────────────────────────────────────────┐
  │  Cluster           │ Old Centroid (from stale distribution) │
  │                    │ New Centroid (from corrected data)       │
  │                    │                                         │
  │  general           │ 0.43 avg confidence → ~0.60+            │
  │  analyzer_planner  │ Absorbed debug + strategy-kb → shifted   │
  │  knowledge_wiki    │ Absorbed graphify + strategy-kb → shifted│
  │  Others            │ Minor adjustments from recluster          │
  └──────────────────────────────────────────────────────────────┘

EXISTS AS: scripts/compute_cluster_centroids.py (no modifications needed)
RUN: ./venv/bin/python scripts/compute_cluster_centroids.py
```

### Task 14: Final verification — full audit re-run

```
VERIFICATION PIPELINE:

  ┌──────────────────────────────────────────────────────────────────┐
  │  STEP 1: DB Integrity (audit_phase1_v2.py)                     │
  │                                                                  │
  │  ✓ 0 orphan resources (unassigned cluster)                      │
  │  ✓ cluster_definitions counts match resources.cluster GROUP BY  │
  │  ✓ general cluster < 200 resources                               │
  │  ✓ All 8 clusters have valid centroids                          │
  │  ✓ Embedding coverage = 100% of active resources               │
  └──────────────────────────────────────────────────────────────────┘

  ┌──────────────────────────────────────────────────────────────────┐
  │  STEP 2: Routing Smoke Tests (MCP tool calls)                   │
  │                                                                  │
  │  Query                              Expected Cluster             │
  │  ─────────────────────────────────  ─────────────────            │
  │  Debug TypeError null pointer       analyzer_planner  ← FIXED  │
  │  Design microservice payment        architect                     │
  │  React dashboard with charts        frontend_ui                  │
  │  PostgreSQL auth REST API            backend_api                 │
  │  Docker K8s CI/CD pipeline          devops_infra                │
  │  Unit tests pytest security audit   quality_security             │
  │  Write documentation wiki article    knowledge_wiki              │
  │  Analyze performance review code     analyzer_planner            │
  │  Landing page with Tailwind          frontend_ui                 │
  │  REST API JWT auth database          backend_api                 │
  │  Knowledge base article microservices knowledge_wiki             │
  │                                                                  │
  │  Target: 11/11 correct (was 10/11)                              │
  └──────────────────────────────────────────────────────────────────┘

  ┌──────────────────────────────────────────────────────────────────┐
  │  STEP 3: Wired-Up Check (audit_phase5.py)                       │
  │                                                                  │
  │  ✓ 0 skill shells with status='active' and missing files        │
  │  ✓ 8 strategy-kb MCP tools visible in resources table           │
  │  ✓ All 7 graphify tools assigned to knowledge_wiki              │
  │  ✓ routing_log accepts any ide value                            │
  └──────────────────────────────────────────────────────────────────┘

  ┌──────────────────────────────────────────────────────────────────┐
  │  STEP 4: FAISS Health (audit_phase6.py)                          │
  │                                                                  │
  │  ✓ FAISS vector count = active resource count (~1,535)           │
  │  ✓ All embeddings 384-dim                                        │
  │  ✓ 0 missing embeddings                                           │
  │  ✓ Round-trip verification passed                                 │
  └──────────────────────────────────────────────────────────────────┘

  ┌──────────────────────────────────────────────────────────────────┐
  │  STEP 5: Final Grade Calculation                                │
  │                                                                  │
  │  Metric                    Before    After    Grade Change        │
  │  ────────────────────────  ───────   ──────   ──────────        │
  │  Cluster membership        99.6%     100%     A → A+             │
  │  Cluster count accuracy    F (off by 755)  A (exact)  F→A       │
  │  Orphan resources          7         0        D → A              │
  │  Callable tools             23+528    23+528+8 C → B            │
  │  Routing accuracy          74.5%     ~90%+    B- → A-            │
  │  Never-triggered           68%       ~10%     D → B              │
  │  Skill shells              159       0        D → A              │
  │  Strategy-KB visibility    0/8       8/8      F → A              │
  │  Reflection loop           Broken    Working  F → B              │
  │  Outcome tracking         6%        ~50%+    D → C+             │
  │                                                                  │
  │  OVERALL: C+ → A-                                                │
  └──────────────────────────────────────────────────────────────────┘
```

---

## EXECUTION ORDER & TIMING

```
TIME    TASK  DEPENDS ON    DESCRIPTION
─────   ────  ──────────    ───────────────────────────────────
00:00   T1    —             Sync cluster counts (5 min)
00:05   T2    T1            Recluster general (10 min)
00:15   T3    T2            Assign graphify orphans (3 min)
00:10   T4    T2            Fix debug routing (10 min, parallel with T3)
─── P0 DONE at ~00:25 ──────────────────────────────────────────

00:25   T5    T3            Sync strategy-kb (10 min)
00:25   T6    T3            Purge skill shells (5 min, parallel with T5)
00:25   T7    T4            Fix outcome tracking (10 min, parallel with T5)
─── P1 DONE at ~00:35 ──────────────────────────────────────────

00:35   T8    T6            Dedup body_text (5 min)
00:35   T9    T6            Upgrade tier-3 stubs (15 min, parallel with T8)
00:35   T10   T6            Cold storage (5 min, parallel with T8)
00:50   T11   T7            Fix self-reflection (10 min)
─── P2 DONE at ~01:00 ──────────────────────────────────────────

01:00   T12   ALL ABOVE     Rebuild FAISS (5 min)
01:05   T13   T12           Recompute centroids (5 min)
01:10   T14   T13           Final verification audit (15 min)
─── P3 DONE at ~01:25 ──────────────────────────────────────────

TOTAL ESTIMATED TIME: ~1.5 hours
CRITICAL PATH: T1 → T2 → T3/T4 → T6 → T8/T9/T10 → T12 → T13 → T14
PARALLELISM: T3∥T4, T5∥T6∥T7, T8∥T9∥T10
```

---

## ROLLBACK PLAN

Every DB-changing task can be rolled back:

```
Task 1-4 (P0):  Status changes only. Rollback = re-run with reversed SQL.
                git revert on cluster_config.py for Task 4.

Task 5 (P1):   DELETE FROM resources WHERE name LIKE 'strategy-kb%';
                DELETE FROM resource_embeddings WHERE resource_id IN (...);

Task 6 (P1):   UPDATE resources SET status='active' WHERE status='degraded'
                AND resource_type='skill';

Task 7 (P1):   Reverse table swap (rename routing_log back, add CHECK).
                Keep backup of old schema.

Task 8-10 (P2): UPDATE resources SET status='active' WHERE status IN
                ('deprecated', 'cold');

Task 11 (P2):  Revert server/learning.py (git revert).

Task 12-13 (P3): Rebuild FAISS from backup. Recompute old centroids.
                Keep backup: cp db/faiss/resources.index db/faiss/resources.index.bak
                Keep backup: sqlite3 db/nexus.db ".backup db/nexus.db.bak"
```

Plan written to: `/home/roshan/nexus/docs/plans/2026-05-17-nexus-fix-all-issues-DAG.md`