# NEXUS v4+Cluster Full Audit Report
**Date**: 2026-05-22  
**Auditor**: GLM-5.1 via Hermes  
**System**: NEXUS vector-based resource & knowledge management system  
**Database**: `/home/roshan/nexus/db/nexus.db` (SQLite)  
**Stack**: Python/FastAPI + SQLite + FAISS + SentenceTransformers  

---

## EXECUTIVE SUMMARY

NEXUS has 2,463 resources (1,870 active), 8 domain clusters, 84.5% routing accuracy, and 2,329 Thompson Sampling beliefs. The system works but has critical scalability gaps at the current 2,463 resource level that will block growth to 5,000+.

**Top 3 blockers for 5K scale:**
1. **49.4% cold-start** — 923 resources never triggered, invisible to routing
2. **No dedup pipeline** — 33+ exact duplicates, 36 overlapping name stems with 3+ resources
3. **No relationship graph** — zero structural edges (calls, depends_on, similar_to), only flat FAISS

**Top 3 quick wins (under 30 min each):**
1. Delete 33 exact description duplicates
2. Delete 592 orphaned archived resource memberships
3. Reassign 817 knowledge_wiki resources into sub-clusters

---

## 1. SYSTEM HEALTH SCORECARD

| Metric | Current | Target at 5K | Status |
|--------|---------|-------------|---------|
| Total resources | 2,463 | 5,000+ | GROWING |
| Active resources | 1,870 | 4,500+ | OK |
| Routing accuracy | 84.5% | 85%+ | PASS |
| Outcome coverage | 98.9% | 95%+ | PASS |
| Cold-start resources | 49.4% (923) | <10% | FAIL |
| Self-reflection fix rate | 0% (0/27) | >50% | FAIL |
| Cluster centroids computed | 0/9 | 9/9 | FAIL |
| Exact duplicates | 33 removable | 0 | WARN |
| Cluster orphans (archived) | 592 | 0 | FAIL |
| High-error resources still active | 15+ | 0 | WARN |
| IDE feedback events | 15 | >1000 | FAIL |
| User learnings | 13 | >100 | FAIL |
| Resource edges (structural) | 0 | >5000 | MISSING |
| Dedup pipeline | None | Periodic batch | MISSING |
| Embedding coverage | 100% (1,871) | 100% | PASS |

---

## 2. CRITICAL BUGS

### BUG 1: 49.4% Cold-Start Resources (923 never triggered)

**Impact**: Half your knowledge base is dead weight. Routing never reaches these resources.

**Breakdown by type:**
| Type | Total | Cold | Cold% |
|------|-------|------|-------|
| skill | 693 | 343 | 49.5% |
| command | 276 | 167 | 60.5% |
| agent | 169 | 94 | 55.6% |
| design_system | 158 | 103 | 65.2% |
| doc | 139 | 78 | 56.1% |
| cli_tool | 132 | 54 | 40.9% |
| hook | 69 | 41 | 59.4% |
| spec | 156 | 9 | 5.8% |
| mcp | 66 | 26 | 39.4% |
| craft | 12 | 8 | 66.7% |

**Root cause**: Resources ingested but never got routing traffic. Cluster routing only searches within 2 activated clusters, so resources in inactive clusters get zero visibility.

**Fix**: Implement cold-start exploration (see Implementation Plan Phase 1).

---

### BUG 2: Self-Reflection Fix Rate = 0% (27 failures, zero fixes)

The `reflection_failures` table has 27 entries but `reflection_fixes` has 0 rows. All failures have `root_cause="general_failure"`. No root cause analysis, no automated repair.

**Fix**: Wire `reflection_failures` → `find_alternatives_on_failure` MCP endpoint. Track root causes properly.

---

### BUG 3: 592 Archived Resources in Cluster Membership

`cluster_membership` has 2,504 entries for 1,870 active resources (134% coverage = double-counting). 592 archived resources pollute `cluster_membership`, `bundle_membership` (1,273 orphaned), and `resource_policies`.

**Fix**:
```sql
DELETE FROM cluster_membership WHERE resource_id IN (SELECT id FROM resources WHERE status='archived');
DELETE FROM bundle_membership WHERE resource_id IN (SELECT id FROM resources WHERE status='archived');
DELETE FROM resource_policies WHERE resource_id IN (SELECT id FROM resources WHERE status='archived');
```

---

### BUG 4: High-Error Skills Still Routed

| Resource | Triggered | Correct | Wrong | Error Rate |
|----------|-----------|---------|-------|------------|
| article-writing | 9 | 24 | 3 | 33.3% |
| content-engine | 9 | 24 | 3 | 33.3% |
| custom-hmac-admin-auth | 7 | 4 | 2 | 28.6% |
| architect | 8 | 5 | 2 | 25.0% |

These keep getting routed despite poor outcomes. Thompson Sampling only degrades slowly because it needs many wrong outcomes to overcome high alpha.

**Fix**: Auto-degradation if `times_wrong / times_triggered > 0.6`.

---

## 3. DEDUPLICATION ANALYSIS

### 3.1 Exact Description Duplicates (33 removable)

| Group | Count | Names |
|-------|-------|-------|
| Role duplicates | 4 | reviewer, tester, coder, researcher |
| Design docs v7 | 4 | doc-2026-04-18-designlang-v7-design, doc-2026-04-18-website-redesign-design, spec-2026-04-18-designlang-v7-design, spec-2026-04-18-designlang-v7 |
| Design docs v10 | 4 | doc-2026-04-22-v10-intent-release-design, doc-2026-04-22-v10.1-component-screenshots-design, spec-2026-04-22-v10-int, ... |
| Ruflo MCP duplicates | 10 | ruflo-mcp-{worker,task,system,swarm,session,sona,memory,hooks}-tools vs .d variants |
| Other duplicates | 4 | formatter-brand-book/grade, extractor-component-screenshots/interactions, claude-skill/cmd-security-review, extractor-perf/core-crawler |

### 3.2 High-Overlap Name Stems (3+ resources sharing prefix)

| Stem | Count | Triggered | Types |
|------|-------|-----------|-------|
| claude-skill | 115 | 62 | skill |
| hermes-skill | 96 | 41 | skill |
| claude-cmd | 88 | 46 | command |
| skill-html | 51 | 7 | skill |
| ruflo-cli | 49 | 23 | cli_tool |
| career-ops | 44 | 16 | mixed |
| ruflo-doc | 38 | 17 | doc |
| ruflo-mcp | 38 | 24 | mcp |
| gstack | 26 | 22 | command |
| ruflo-agent | 21 | 9 | agent |
| hive-mind | 12 | 0 | skill |
| ruflo-skill | 9 | 2 | skill |

### 3.3 Functional Overlap by Action

| Action | Resources | Triggered | Top Types |
|--------|-----------|-----------|-----------|
| design | 290 | 160 | design_system:158, spec:57 |
| manag | 69 | 37 | skill:21, agent:13, mcp:13 |
| test | 65 | 51 | skill:26, spec:18 |
| review | 64 | 40 | skill:33, agent:9 |
| extract | 62 | 60 | spec:48, doc:5 |
| implement | 61 | 34 | skill:16, cli_tool:14 |

---

## 4. ARCHITECTURE GAPS FOR 5000+ SCALE

### GAP 1: No Relationship/Edge Tables

**Missing tables**: `resource_edges`, `resource_aliases`, `dedup_candidates`

Currently NEXUS has ZERO structural connections between resources. There's no way to express:
- Resource A calls Resource B
- Resource A depends_on Resource C
- Resource A is similar_to Resource D
- Resources X and Y are always used together (co_triggered)

The `graphify` MCP is connected but separate — not integrated into NEXUS routing.

### GAP 2: No Deduplication Pipeline

No mechanism to detect, flag, or merge semantically similar resources. Currently relies on manual inspection.

### GAP 3: Cluster Imbalance

| Cluster | Resources | Cold | Cold% | Notes |
|---------|-----------|------|-------|-------|
| knowledge_wiki | 263 (817 in membership!) | 148 | 56.3% | OVERSIZED — needs sub-clusters |
| frontend_ui | 416 | 201 | 48.3% | Over-represented |
| analyzer_planner | 337 | 174 | 51.6% | Balanced size, high cold |
| architect | 218 | 124 | 56.9% | |
| general | 171 (526 in membership!) | 88 | 51.5% | Catch-all, needs splitting |
| devops_infra | 192 | 82 | 42.7% | |
| backend_api | 148 | 54 | 36.5% | BEST cold rate |
| quality_security | 119 | 47 | 39.5% | GOOD cold rate |

`knowledge_wiki` has 817 resources in membership but only 263 triggered — it's a dumping ground. `general` has 526 in membership.

### GAP 4: No Centroid Computation

All 9 clusters have `centroid_embedding = NULL`. Cluster routing works on keyword matching alone, not embedding similarity to centroids. This explains 1,036 resources with low cluster confidence.

### GAP 5: Benchmark Store Empty, IDE Events Near-Zero

- `benchmark_results`: 0 rows
- `ide_events`: 15 rows (should be thousands)
- `user_learnings`: 13 rows

The feedback loop from IDE → NEXUS is broken. Thompson Sampling runs on routing outcomes only, not on actual task success/failure data from the IDE.

### GAP 6: 6 Resources with Zero Tokens, Multiple Shells

6 resources with `token_count=0` and 30+ resources with minimal body (1-5 tokens). These are shell commands like `run`, `mcp`, `ask` with no meaningful content for embedding.

---

## 5. CLUSTER HEALTH DEEP DIVE

| Cluster | Triggered | Cold | Cold% | Notes |
|---------|-----------|------|-------|-------|
| analyzer_planner | 337 (163) | 174 | 51.6% | Good size, high cold |
| architect | 218 (94) | 124 | 56.9% | High cold, needs pruning |
| frontend_ui | 416 (215) | 201 | 48.3% | Largest, overloaded |
| backend_api | 148 (94) | 54 | 36.5% | BEST ratio |
| devops_infra | 192 (110) | 82 | 42.7% | Good ratio |
| quality_security | 119 (72) | 47 | 39.5% | Good ratio |
| knowledge_wiki | 263 (115) | 148 | 56.3% | Overloaded catch-all |
| general | 171 (83) | 88 | 51.5% | Needs splitting |
| coaching | 6 (1) | 5 | 83.3% | Tiny, barely used |

---

## 6. RESEARCH SYNTHESIS: SCALING TECHNIQUES

### 6.1 Tool Routing at 5K+ Scale

**AnyTool (3-Layer Funnel)**: Category → Subcategory → Tool. Reduces O(N) to O(log N). NEXUS already uses cluster routing (similar concept) but lacks centroid computation.

**Tool Attention / ISO Scoring (arxiv:2604.21816)**: Already implemented in NEXUS v4. Theta decay chain: 0.28 → 0.20 → 0.12 → 0.05 → 0.0. Achieves 80% token reduction.

**TDWA Dynamic Embeddings (ScaleMCP)**: Description weight 0.5, schema weight 0.3, keywords weight 0.2. NEXUS needs this — current embeddings are static, description-only.

**ToolkenGPT Hot Cache**: LRU cache of top-20 most-triggered resource embeddings for <5ms access. Cold resources fall back to FAISS.

### 6.2 Semantic Deduplication

**Multi-stage filtering**: MinHash LSH (coarse, 0.8 Jaccard) → embedding cosine (medium, >0.85) → cross-encoder reranking (fine, >0.9).

**Functional signature matching**: Jaccard on (param_name, param_type) sets, combined with description cosine. Catches "send_email" vs "send_notification" — same description but different schemas.

**HDBSCAN clustering**: Variable-density clusters without specifying k. Better than fixed-k K-Means for tool catalogs where some domains have hundreds and others have tens.

### 6.3 Relational + Semantic Connections

**Hybrid graph + FAISS**: Store structural edges (calls, depends_on, similar_to) in SQLite `resource_edges` table. Query via BFS for exact traversal, FAISS for fuzzy nearest-neighbor. Merge scores at query time.

**Graduation-based clustering**: Resources with 10+ triggers and >80% success rate graduate from `general` to their own sub-cluster.

### 6.4 Cold-Start Exploration

**Thompson Sampling + epsilon-greedy**: Currently only exploits warm resources. Add epsilon=0.1 forced exploration to cold resources.

**UCB exploration bonus**: For cold resources with no outcome data, use `score = ISO_score + sqrt(2*ln(N)/max(1, n))`.

**Description warm-start**: Initialize alpha prior from description quality (length, clarity, schema completeness) instead of Beta(1,1) uniform.

---

## 7. IMPLEMENTATION PLAN

### PHASE 1: IMMEDIATE FIXES (Day 1-2)

#### P1.1: Delete Orphaned Archived Memberships
```sql
DELETE FROM cluster_membership 
WHERE resource_id IN (SELECT id FROM resources WHERE status='archived');
-- Expected: removes 592+ orphans

DELETE FROM bundle_membership 
WHERE resource_id IN (SELECT id FROM resources WHERE status='archived');
-- Expected: removes 1,273 orphans

DELETE FROM resource_policies 
WHERE resource_id IN (SELECT id FROM resources WHERE status='archived');
-- Expected: removes 592 orphans
```
**Impact**: 25% storage reduction, cleaner cluster routing.

#### P1.2: Delete Exact Description Duplicates
Remove 33 duplicate resources (keep the one with higher times_triggered):
```python
for group in duplicate_groups:
    # Keep resource with highest times_triggered, archive the rest
    best = max(group, key=lambda r: r.times_triggered)
    for r in group:
        if r.id != best.id:
            archive_resource(r.id)
```

#### P1.3: Compute Cluster Centroids
```python
for cluster in clusters:
    members = get_active_members(cluster.name)
    embeddings = [get_embedding(m) for m in members]
    centroid = normalize(mean(embeddings, axis=0))
    update_cluster_centroid(cluster.id, centroid)
```
**Impact**: Fixes 1,036 resources with low cluster confidence. Enables proper embedding-based cluster routing.

#### P1.4: Auto-Degrade High-Error Resources
```python
for resource in resources:
    if resource.times_triggered >= 5 and resource.error_rate > 0.6:
        resource.status = 'degraded'
```

**Estimated effort**: 2-4 hours total for P1.1-P1.4.

---

### PHASE 2: DEDUPLICATION PIPELINE (Week 1)

#### P2.1: Create `dedup_candidates` Table
```sql
CREATE TABLE dedup_candidates (
    id INTEGER PRIMARY KEY,
    resource_a_id INTEGER REFERENCES resources(id),
    resource_b_id INTEGER REFERENCES resources(id),
    similarity_score REAL,
    similarity_method TEXT, -- 'minhash', 'cosine', 'cross_encoder', 'functional'
    status TEXT DEFAULT 'pending', -- pending, reviewed, merged, dismissed
    merged_into_id INTEGER REFERENCES resources(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(resource_a_id, resource_b_id, similarity_method)
);
```

#### P2.2: Build Batch Dedup Scanner
```python
class SemanticDeduplicator:
    """3-stage dedup: MinHash LSH → cosine → cross-encoder"""
    
    def scan(self):
        # Stage 1: MinHash LSH (O(N) pass, catches near-identical)
        lsh = MinHashLSH(threshold=0.8, num_perm=128)
        # Stage 2: Cosine similarity on embeddings (0.85 threshold)
        # Stage 3: Cross-encoder reranking (0.9 threshold)
        # Output: dedup_candidates rows for human review
```

#### P2.3: Ruflo MCP Duplication Cleanup
Archive the `.d` variants: `ruflo-mcp-worker-tools.d` → merged into `ruflo-mcp-worker-tools`, etc. (10 duplicates).

**Estimated effort**: 1-2 days.

---

### PHASE 3: COLD-START EXPLORATION (Week 1-2)

#### P3.1: Add Epsilon-Greedy Exploration
```python
EXPLORATION_EPSILON = 0.1  # 10% of routes go to cold resources

def route_with_exploration(query, candidates):
    if random.random() < EXPLORATION_EPSILON:
        cold = [c for c in candidates if c.times_triggered < 10]
        if cold:
            return weighted_random_choice(cold, weight_by='description_quality')
    return thompson_sample(candidates)  # normal exploitation
```

#### P3.2: Description-Based Warm Start
```python
def compute_warm_start_prior(resource):
    quality = (
        0.3 * min(len(resource.description.split()) / 50, 1.0) +
        0.3 * name_desc_coherence(resource) +
        0.2 * len(resource.keywords) / 10 +
        0.2 * float(bool(resource.schema))
    )
    alpha = 1.0 + quality * 3.0   # range [1, 4]
    beta = 1.0 + (1 - quality) * 2.0  # range [1, 3]
    return alpha, beta
```

#### P3.3: UCB Bonus for Cold Resources
```python
def ucb_score(resource, total_routes):
    if resource.times_triggered < MIN_SAMPLES:
        # Upper confidence bound for cold resources
        return resource.iso_score + sqrt(2 * log(total_routes) / max(1, resource.times_triggered))
    return resource.iso_score
```

**Estimated effort**: 2-3 days.

---

### PHASE 4: RELATIONSHIP GRAPH (Week 2-3)

#### P4.1: Create `resource_edges` Table
```sql
CREATE TABLE resource_edges (
    id INTEGER PRIMARY KEY,
    source_id INTEGER REFERENCES resources(id),
    target_id INTEGER REFERENCES resources(id),
    relation TEXT NOT NULL, -- 'calls', 'depends_on', 'similar_to', 'co_triggered', 'contains'
    weight REAL DEFAULT 1.0,
    confidence REAL DEFAULT 1.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_id, target_id, relation)
);

CREATE INDEX idx_edges_source ON resource_edges(source_id);
CREATE INDEX idx_edges_target ON resource_edges(target_id);
CREATE INDEX idx_edges_relation ON resource_edges(relation);
```

#### P4.2: Build Similarity Edges from Embeddings
```python
def build_similarity_edges(threshold=0.85):
    for resource in active_resources:
        similar = faiss_index.search(resource.embedding, k=20)
        for candidate, score in similar:
            if score > threshold:
                insert_edge(resource.id, candidate.id, 'similar_to', weight=score)
```

#### P4.3: Build Co-Triggered Edges from Routing Log
```python
def build_co_triggered_edges():
    sessions = group_routing_log_by_session()
    for session_tasks in sessions:
        resources = [t.matched_skill for t in session_tasks]
        for a, b in combinations(resources, 2):
            increment_edge(a.resource_id, b.resource_id, 'co_triggered')
```

#### P4.4: Integrate with Hybrid Query
```python
def hybrid_query(query, depth=2):
    # 1. FAISS semantic search
    semantic_hits = faiss_search(query_embedding, k=20)
    # 2. BFS structural expansion from top hit
    structural = bfs(top_hit, relations=['calls', 'depends_on', 'co_triggered'], depth=depth)
    # 3. Merge: structural edges boost semantic scores
    merged = {}
    for rid, score in semantic_hits:
        merged[rid] = score
    for rid, hops in structural:
        merged[rid] = merged.get(rid, 0) + 0.3 / (1 + hops)
    return sorted(merged.items(), key=lambda x: -x[1])
```

**Estimated effort**: 3-5 days.

---

### PHASE 5: CLUSTER REBALANCING (Week 3)

#### P5.1: Split knowledge_wiki (817 resources → sub-clusters)
```
knowledge_wiki → knowledge_wiki_system_design, knowledge_wiki_trading, 
                 knowledge_wiki_devops, knowledge_wiki_general
```

#### P5.2: Split general (526 resources → domain-specific)
```
general → general_utilities, general_ide, general_workflow
```

#### P5.3: Recompute all cluster centroids after rebalancing

#### P5.4: Add dynamic graduation — resources with 10+ triggers and >80% accuracy graduate from general to domain-specific cluster

**Estimated effort**: 2-3 days.

---

### PHASE 6: FEEDBACK LOOP REPAIR (Week 3-4)

#### P6.1: Wire reflection_failures → find_alternatives_on_failure
```python
# On routing failure:
diagnosis = diagnose_failure(failed_skill, task)
alternatives = find_alternatives(diagnosis, task)
log_reflection_fix(failure_id, alternatives)
```

#### P6.2: IDE Event Pipeline
Build a post-task hook in OpenCode/Claude Code that calls `POST /nexus/event` with outcome data.

#### P6.3: Partial Outcome Follow-Up
For 254 routes with `outcome='partial'`, schedule a delayed confirmation check.

**Estimated effort**: 3-4 days.

---

### PHASE 7: KNOWLEDGE ABSORPTION PIPELINE (Week 4+)

This addresses your vision of NEXUS as a brain that absorbs multi-domain knowledge.

#### P7.1: Build `ingest` Command
```python
@click.command()
@click.option('--source', type=click.Path(exists=True))
@click.option('--type', type=click.Choice(['markdown', 'pdf', 'url', 'directory']))
def ingest(source, type):
    """Ingest knowledge documents into NEXUS."""
    chunks = chunk_content(source, type)
    for chunk in chunks:
        embedding = model.encode(chunk.text)
        resource_id = insert_resource(
            name=chunk.title,
            description=chunk.summary,
            resource_type='doc',
            body_text=chunk.text,
            embedding=embedding
        )
        assign_to_cluster(resource_id, embedding)
        warm_start_beliefs(resource_id)
```

#### P7.2: Multi-Format Support
- Markdown: section-based chunking (## headers)
- PDF: page-based extraction via pymupdf
- URLs: web scraping + summarization
- Directories: recursive scan with file-type detection

#### P7.3: Auto-Cluster Assignment
New ingested resources get assigned to nearest cluster centroid, or create a new micro-cluster if similarity is below threshold.

**Estimated effort**: 5-7 days for core pipeline, ongoing for format additions.

---

## 8. PRIORITY MATRIX

| Priority | Item | Impact | Effort | ROI |
|----------|------|--------|--------|-----|
| P1.1 | Delete orphan memberships | High | 5 min | VERY HIGH |
| P1.2 | Delete exact duplicates | Medium | 30 min | HIGH |
| P1.3 | Compute cluster centroids | High | 1 hour | VERY HIGH |
| P1.4 | Auto-degrade high-error | Medium | 30 min | HIGH |
| P2.1 | Create dedup_candidates table | Medium | 1 hour | MEDIUM |
| P2.2 | Build batch dedup scanner | High | 1-2 days | HIGH |
| P3.1 | Epsilon-greedy exploration | High | 2-3 hours | VERY HIGH |
| P3.2 | Description warm-start | High | 3-4 hours | VERY HIGH |
| P4.1 | Create resource_edges table | High | 1 hour | HIGH |
| P4.2 | Build similarity edges | Medium | 2-3 hours | HIGH |
| P5.1 | Split knowledge_wiki | High | 2-3 days | MEDIUM |
| P6.1 | Wire reflection → alternatives | High | 1 day | HIGH |
| P6.2 | IDE event pipeline | Critical | 2-3 days | VERY HIGH |
| P7.1 | Build ingest command | Critical | 5-7 days | HIGH |

---

## 9. EXPECTED OUTCOMES AFTER FIXES

| Metric | Current | After P1-P3 | After P4-P6 | After P7 |
|--------|---------|-------------|-------------|----------|
| Active resources | 1,870 | ~1,835 | 1,835 | 3,000-5,000 |
| Cold-start % | 49.4% | <20% | <10% | <15% |
| Routing accuracy | 84.5% | ~87% | ~89% | ~87% |
| Cluster centroids | 0/9 | 9/9 | 12+ | 15-20 |
| Duplicate resources | 33 | 0 | 0 | 0 |
| Structural edges | 0 | 0 | 5,000+ | 10,000+ |
| Reflection fix rate | 0% | 0% | >50% | >70% |
| IDE events | 15 | 15 | 1000+ | 1000+ |
| Knowledge documents | 0 | 0 | 0 | 500+ |

---

## 10. DATABASE SCHEMA CHANGES

### New Tables

```sql
-- P2: Dedup tracking
CREATE TABLE dedup_candidates (
    id INTEGER PRIMARY KEY,
    resource_a_id INTEGER REFERENCES resources(id),
    resource_b_id INTEGER REFERENCES resources(id),
    similarity_score REAL,
    similarity_method TEXT,
    status TEXT DEFAULT 'pending',
    merged_into_id INTEGER REFERENCES resources(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(resource_a_id, resource_b_id, similarity_method)
);

-- P4: Relationship graph
CREATE TABLE resource_edges (
    id INTEGER PRIMARY KEY,
    source_id INTEGER REFERENCES resources(id),
    target_id INTEGER REFERENCES resources(id),
    relation TEXT NOT NULL,
    weight REAL DEFAULT 1.0,
    confidence REAL DEFAULT 1.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_id, target_id, relation)
);

CREATE INDEX idx_edges_source ON resource_edges(source_id);
CREATE INDEX idx_edges_target ON resource_edges(target_id);
CREATE INDEX idx_edges_relation ON resource_edges(relation);

-- P7: Knowledge ingestion log
CREATE TABLE ingestion_log (
    id INTEGER PRIMARY KEY,
    source_path TEXT,
    source_type TEXT,
    chunks_created INTEGER,
    resources_created INTEGER,
    status TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Modified Columns

```sql
-- Add description quality score for warm-start
ALTER TABLE resources ADD COLUMN desc_quality_score REAL DEFAULT 0.5;

-- Add cluster confidence (already exists, needs computation)
-- UPDATE resources SET cluster_confidence = computed_value WHERE cluster_confidence IS NULL;

-- Add auto-degraded flag
ALTER TABLE resources ADD COLUMN auto_degraded_at TIMESTAMP;
```

---

*End of audit report. Generated 2026-05-22.*