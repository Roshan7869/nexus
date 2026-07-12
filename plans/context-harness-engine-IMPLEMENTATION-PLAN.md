# NEXUS Context Harness Engine — Master Implementation Plan

**Generated**: 2026-06-28  
**Methodology**: First Principles Deconstruction → Auto-Resource Discovery → 6 Parallel Researcher Agents (DSA System Design + Multidimensional Analysis + Harness Context Guides)  
**Status**: Ready for Approval  

---

## Plan Overview

| Phase | Name | Subtasks | Dep Level | Est Tokens | Parallel |
|-------|------|----------|-----------|------------|----------|
| P0 | Project Classifier + Bundle Seeder | 6 (S1-S6) | L0→L3 | 18,000 | S2∥S4 |
| P1 | 4-Layer Cache Hierarchy (L0-L3) | 6 (0-5) | L0→L4 | 4,800 | 0∥1, 2∥3 |
| P2 | CAG/RAG Hybrid Router | 6 | L0→L2 | 3,200 | P1 sub-dep |
| P3 | Multi-Stage Compression Engine | 5 (SA1-5) | L0→L4 | 8,500 | SA1∥SA2 |
| P4 | Progressive Disclosure + Preloading | 6 (S1-S6) | L0→L4 | 5,100 | S1 alone |
| P5 | TOIN Learning + KVFlow Eviction | 6 (ST1-2+P3-5) | L0→L3 | 4,200 | ST1∥ST2 |
| P6 | Integration + MCP Tools | 5 | L0→L3 | 2,800 | Dep on P0-P5 |
| V | Verification Suite | 8 test blocks | — | 3,000 | Block-level |
| A | Audit Report | 1 | — | 1,500 | After all |

**Total estimated tokens**: ~51,100  
**Execution model**: Level-by-level DAG with parallel subtask groups  
**Verification**: Block-level per phase with rollback procedures  

---

## Phase P0: Project Type Classifier + Bundle Seeder

**Goal**: Auto-detect project type (ai_ml, web_dev, general_dev, research, devops, trading) using weighted TF-IDF keyword scoring + cascade rules. Seed `bundle_membership` table with 7 project-type bundles.

### Dependency Graph

```
L0: [S1] ProjectType Enum & Keyword Signatures
L1: [S2] Weighted TF-IDF Resource Scorer   |  [S4] Bundle Definition Schema & Criteria
L2: [S3] Cascade Decision Classifier       |  [S5] Bundle Membership Seeder Engine
L3: [S6] Integration Wiring & Health Check
```

### Subtask P0-S1: ProjectType Enum & Keyword Signature Registry

| Aspect | Analysis |
|--------|----------|
| **Data structures** | `ProjectType(StrEnum)` with 6 values. `KeywordSignature(TypedDict)`: keyword, weight, source_focus, boost_factor. `CorpusIdfCache: dict[str, float]` precomputed IDF |
| **Algorithms** | IDF precomputation: O(n·k) for 2,181 resources. Signature loading: O(1) JSON parse |
| **Design patterns** | Registry (signatures at module load), Strategy (boost per match location), Immutable Singleton |
| **Memory budget** | ~200 entries × 32 bytes + 2 KB JSON = ~8 KB |
| **Correctness** | 9/10 — deterministic, verifiable against known cluster assignments |
| **Performance** | 10/10 — <1ms module init, zero runtime allocation |
| **Safety** | 8/10 — JSON validation on load, fallback to empty signatures |

**Harness Context**:
```yaml
preconditions:
  - resources table has ≥2000 active rows with description + body_text
  - resource_sections table has ≥20000 rows
guardrails:
  - Enum values must be SQL-safe lower_snake_case
  - Signature weights must sum to 1.0 per type group
  - Max 30 keywords per type (bounded O(n) cost)
verification:
  - ProjectType enum has exactly 6 values
  - Each signature has ≥10 entries
  - Signatures produce non-zero weighted scores for every cluster
rollback:
  - Delete project_types.py (no DB changes)
```

### Subtask P0-S2: Weighted TF-IDF Resource Scorer

| Aspect | Analysis |
|--------|----------|
| **Data structures** | `ResourceSignals(NamedTuple)`, `ResourceScoreVector(NamedTuple)` with 6 scores per resource, `LRU_Cache` (500 entries) |
| **Algorithms** | `per_resource_score()`: O(k·t) where k=25 keywords × t=6 types = 150 checks. `batch_score_all()`: O(n·k·t) = 327K checks for 2,181 resources |
| **Design patterns** | Strategy (match source_focus), Flyweight (transient ResourceSignals), Proxy (LRU cache) |
| **Complexity** | O(n·k·t) = 327,150 substring checks. Target: <50ms for full batch |
| **Correctness** | 8/10 — substring matching risks false positives; use regex `\bkeyword\b` for 3+ char terms |
| **Performance** | 9/10 — Python substring on avg 500-char fields = ~30-40ms full batch |
| **Safety** | 8/10 — LRU cache key includes signature_version to prevent staleness |

**Harness Context**:
```yaml
preconditions:
  - P0-S1 complete (ProjectType enum + signatures exist)
  - resources table queryable via read-only SQL connection
guardrails:
  - Must complete in <50ms for 2,181 resources
  - Must NOT write to any table — pure read + compute
  - Must handle NULL description/body_text gracefully
  - Must cache frequently accessed data (LRU with TTL)
verification:
  - Each resource gets exactly 6 scores (one per ProjectType)
  - Scores normalized to [0.0, 1.0] range
  - Repeated calls with same inputs return identical scores
rollback:
  - Remove scorer.py + __init__.py from classifier/ — no DB mutations
```

### Subtask P0-S3: Cascade Decision Classifier

| Aspect | Analysis |
|--------|----------|
| **Data structures** | `CascadeRule(NamedTuple)`: condition+override+confidence_floor+description. `CascadeContext(TypedDict)`: user_history, file_extensions, cluster, type. `ClassificationResult(NamedTuple)` |
| **Algorithms** | 3-level cascade: (1) Cluster-based override, (2) Resource-type affinity, (3) User-history correction. Fallback to general_dev |
| **Design patterns** | Chain of Responsibility (ordered by specificity), Strategy (each level), Null Object (general_dev fallback), Memento (cascade_path) |
| **Complexity** | O(n·r) = 2,181 × ~5 cascade rules = ~10K ops. Target: <5ms |
| **Correctness** | 10/10 — deterministic, no None return, path_trace audit trail |
| **Performance** | 10/10 — pure CPU, zero IO |
| **Safety** | 9/10 — confidence_floor prevents low-confidence overrides, user_history capped at 15% influence |

**Harness Context**:
```yaml
preconditions:
  - P0-S2 complete: ResourceScoreVector[] for all 2,181 resources
  - task_classifications table available for user_history signals
guardrails:
  - Must ALWAYS return valid ProjectType — never None
  - general_dev is the ONLY acceptable fallback
  - Cascade rules ordered by specificity (most specific first)
  - Max 3 cascade levels (resource → project → user-history)
verification:
  - 2,181 resources all classified with no Nones
  - ai_ml + web_dev + devops + trading each >5% of classifications
  - Deterministic: same inputs → same outputs
rollback:
  - Revert classifier.py — no DB changes during classification
```

### Subtask P0-S4: Bundle Definition Schema & Criteria Mapping

**7 baseline bundles**: core_infra, ai_ml_bundle, web_dev_bundle, general_dev_bundle, research_bundle, devops_bundle, trading_bundle

| Aspect | Analysis |
|--------|----------|
| **Data structures** | `BundleCriteria(TypedDict)` with 9 filter fields. `BundleDefinition(NamedTuple)`. `BundleRegistry: dict[str, BundleDefinition]` |
| **Algorithms** | `match_criteria()`: O(k+e+i+x+c) where k=keywords, e=excludes — ~50 checks per resource per bundle |
| **Design patterns** | Specification (composable criteria), Registry (name→definition map) |
| **Correctness** | 10/10 — pure data definitions, explicit boolean filters |
| **Performance** | 10/10 — static data, <1ms load |
| **Safety** | 10/10 — read-only, no crashes |

**Harness Context**: Bundles defined with priority ranges: core_infra=10, project_type=30-50, is_canonical constraint enforced.

### Subtask P0-S5: Bundle Membership Seeder Engine

| Aspect | Analysis |
|--------|----------|
| **Algorithms** | Phase 1: Match criteria (O(k·n) = 15K checks), Phase 2: DELETE stale project-type rows, Phase 3: Bulk INSERT |
| **Complexity** | ~229K operations total. Target: <1s |
| **Correctness** | 9/10 — single transaction atomicity, CanonicalTracker enforces 1:1 constraint |
| **Safety** | 8/10 — WAL protects mid-transaction failure, DELETE-before-INSERT prevents PK violations |

**Harness Context**: All mutations wrapped in single `BEGIN IMMEDIATE`...`COMMIT`. Must NOT touch existing domain bundles.

---

## Phase P1: 4-Layer Cache Hierarchy (L0-L3)

**Goal**: Implement 4 cache layers with progressive tiers, LRU eviction, sqlite persistence, and serialization.

### Subtask P1-0: L0 Cache (Core Infrastructure)

| Aspect | Analysis |
|--------|----------|
| **Storage** | Heap `OrderedDict[int, _L0Entry]` (~200 entries × 512 bytes = 100 KB) + sqlite `cache_hierarchy` table |
| **Algorithms** | Eager load at `ContextCache.__init__()` via SQL `WHERE tier=1 OR bundle_kind='framework'` |
| **Eviction** | None — static, read-only, append-only across sessions |
| **Lookup** | O(1) hash by resource_id |
| **Memory** | ~225 KB total (heap + sqlite) |
| **Correctness** | 10/10 — static seed, exactly reproducible |
| **Safety** | 10/10 — never mutated, cannot stale |

**Hard cap**: 2,048 tokens total, max 256 entries. Always included in L3 active set.

### Subtask P1-1: L1 Cache (Project Type)

| Aspect | Analysis |
|--------|----------|
| **Storage** | `dict[str, _L1Bundle]` — 6 project types, ~350 resources each, ~256 bytes = 525 KB total. LRU keeps 2 hot bundles in heap |
| **Algorithms** | Lazy fill on first project_type access via SQL `WHERE r.cluster IN project_type_clusters`. Eviction: LRU across project types (keep 2) |
| **Patterns** | Cache-Aside (lazy fill), Read-Through (transparent miss→fill→return) |
| **Memory** | ~835 KB (2 hot bundles + sqlite cold) |
| **Correctness** | 8/10 — depends on classifier accuracy (~90% expected) |
| **Safety** | 7/10 — bounded at 8K tokens, miss falls through to nexus_bridge |

**Hard cap**: 8,192 tokens total, 500 entries max.

### Subtask P1-2: L2 Cache (Domain Cluster)

| Aspect | Analysis |
|--------|----------|
| **Storage** | `dict[str, _L2ClusterCache]` — 2-3 active clusters × ~200 entries × ~1 KB = ~400 KB. sqlite for all 8 clusters |
| **Algorithms** | On cluster activation via ClusterRouter. LRU at entry level, keep last 3 clusters. 3,600s TTL re-verify |
| **Complexity** | Fill: 1 DB query ~20ms for 400 rows. Lookup: O(1) |
| **Memory** | ~1.2 MB total (heap + sqlite) |
| **Correctness** | 8/10 — cluster membership recomputed periodically |
| **Safety** | 8/10 — 16K hard cap, L2 miss → L3 then nexus_bridge |

**Body text**: Compressed via msgpack + zlib (2.5-3× compression at level 3).

### Subtask P1-3: L3 Cache (Per-Task ISO Scores)

| Aspect | Analysis |
|--------|----------|
| **Storage** | `TTLLRUCache[str, _L3Entry]` — max 64 entries, 300s TTL. Heap-only, no sqlite |
| **Algorithms** | On miss: hash query → compute ISO scores against L2 via NumpyRouter matmul → top-k heap → store. O(M·D) = 200×384 = 76K ops |
| **Complexity** | ~0.3ms matmul + 0.1ms boost per miss. Cache hit: O(1), <1μs |
| **Memory** | ~236 KB total |
| **Correctness** | 9/10 — bit-identical to fresh compute |
| **Safety** | 9/10 — 4K hard cap, TTL prevents infinite stale |

**Copy-on-write concurrency**: Build _L3Entry atomically, then insert into dict — readers see either old or new, never partial.

### Subtask P1-4: Cache Orchestrator

`ContextCache` singleton manages all 4 layers with invalidation bus (Observer pattern). L2 cluster change → invalidate L3. L1 project change → invalidate L2+L3. **20 KB overhead**. Every public method has try/except with fallback to nexus_bridge.

### Subtask P1-5: Integration (Engine + MCP + Models)

`engine.harness()` wraps original flow: check L3 → compute on miss → fuse L0+L3 → return. New FastMCP endpoints: `context_layer_serve`, `context_cache_stats`, `context_project_init`. **<20 lines change to engine.py**.

---

## Phase P2: CAG/RAG Hybrid Router

**Goal**: Route requests to either Cache-Aware Generation (direct render from cache, ~0.1ms) or Retrieval-Augmented Generation (DB query via unified_route(), ~3.5s).

### Architecture

| Path | Latency | Data Source | Decision |
|------|---------|-------------|----------|
| **CAG** | ~0.1ms | Cache hierarchy (L0→L3) | `confidence = ln(P_cag / P_rag) ≥ 2.0` |
| **RAG** | ~3.5s | NEXUS DB via unified_route() | Fallback on cache miss or low confidence |

### Routing Decision (CAIDA-inspired)

```
P_cag = (cache_hit_count + 1) / (cache_total + 2)  # Laplace smoothing
P_rag = 1 - P_cag
confidence = ln(P_cag / P_rag)
if confidence >= 2.0 → CAG
else → RAG
```

### Anti-Oscillation Guard

- 5-second cooldown window after every CAG↔RAG transition
- EMA dampening with α=0.3 on confidence score
- Writeback: RAG top-1 result auto-inserted into L3 cache for subsequent lookups

### 6 Subtasks

The router breaks into 6 atomic subtasks:
1. **Cache lookup chain**: Walk L0→L1→L2→L3, return first hit + layer info
2. **Confidence tracker**: Per-query hit/miss counters with Laplace smoothing
3. **Decision gate**: Threshold comparison with cooldown + EMA
4. **RAG fallback**: Wraps existing `unified_route('cc', section_weight=true)` — no new code
5. **Writeback path**: On RAG completion, auto-insert top result into L3
6. **Persona memory**: Per-project-type route preference (CAIDA-inspired memory escalation)

### Cache Miss Cascade Protection

| Failure Mode | Detection | Recovery |
|-------------|-----------|----------|
| Miss cascade (L0→L1→L2→L3 all miss) | All layers return None | Fall through to RAG immediately; pre-warm L1+L2 during RAG |
| Confidence oscillation | >3 flips in 60s | Lock to RAG path for 60s |
| Cold start (no cache stats) | cache_total = 0 | Assume confidence = 0 → always RAG until 10+ lookups |

---

## Phase P3: Multi-Stage Compression Engine

**Goal**: Compress 22,013 resource sections (avg 7,640 chars) into cache windows using 5-stage Headroom-inspired pipeline.

### Pipeline Architecture

```
Sections → [CacheAligner] → [ContentRouter] → [SmartCrusher] → [AdaptiveSizer] → [TOIN] → Compressed Windows
```

### Stage SA-1: CacheAligner — Volatile Content Detection

| Detector | Pattern | Normalization | False Positive Risk |
|----------|---------|---------------|---------------------|
| Timestamps | ISO8601, RFC2822, mm/dd/yy | `<TS>` | Low for ISO, moderate for mm/dd/yy |
| Session IDs | `session_[a-zA-Z0-9]{16,64}` | `<SID>` | Moderate (32-char hex = SHA hash) |
| User contexts | `user_id/name/email` + emails | `<USER>` | Low for email pattern |
| Error messages | Error/Exception/Traceback + message | Strip | Low with Error/ prefix |
| Cached search | "Showing N of M results" | `<RESULTS N>` | Low — very specific |
| Auto-generated IDs | UUID, SHA1 | `<ID>` | Low for UUID, moderate for SHA1 |

**Performance**: O(n) regex per section, ~1ms each. Greedy pack O(m log m). Target: 95% capacity, 5% headroom.

**Regex safety**: Use `re.DOUBLE_BACKTRACK` (Python 3.13+) or atomic groups `(?>...)`. Timeout at 100ms. Fall through to raw text on timeout.

### Stage SA-2: ContentRouter — Type & Salience Routing

| Salience | Threshold | Strategy | Target Ratio |
|----------|-----------|----------|--------------|
| High | ≥ 0.70 | Pass-through (full fidelity) | 1.0 |
| Medium | 0.20–0.70 | Moderate compression | 0.70 |
| Low | < 0.20 | Aggressive compression | 0.40 |

**Content type classification**: Code blocks (``` or def/fn/class), JSON arrays ([ or {), frontmatter (---), prose (default), lists, tables. Heuristics on first 500 chars.

**Guard**: section_title and resolved_name ALWAYS pass through at HIGH tier.

### Stage SA-3: SmartCrusher — Type-Specific Compression

| Type | Aggressive Ops | Ratio | Moderate Ops | Ratio |
|------|---------------|-------|--------------|-------|
| Code | Strip imports, comments, blanks | 0.50 | Strip imports only | 0.75 |
| JSON | Keep 3 items + summary | 0.40 | Keep 10 items | 0.65 |
| Prose | First sentence per paragraph | 0.45 | First 2 sentences | 0.70 |
| Lists | Keep 5 items, truncate to 100c | 0.40 | — | — |
| Frontmatter | Keep title/description/version only | 0.30 | — | — |
| Tables | Header + 3 rows | 0.40 | — | — |

**Safety**: JSON output always valid (use `json.dumps`, never slice). Shebang lines preserved. License comments preserved. Prose handles Dr./Mr./etc. abbreviations.

### Stage SA-4: AdaptiveSizer — Dynamic Window Sizing

Kneedle algorithm (Satopää et al., 2011) detects inflection point in token distribution:

```
x = [1..N] normalized, y = sorted(tokens) normalized
d_i = y_i - x_i  # difference curve
knee = first local maximum of d_i
```

| Condition | Action |
|-----------|--------|
| Hit rate < 50% | Shrink to next-lower step (4K→8K→12K→16K) |
| Hit rate > 80% | Expand to next-higher step (if < 85% cache budget used) |
| 50% ≤ hit rate ≤ 80% | Kneedle analysis + map to nearest step |
| Last resize < 300s ago | No-op (anti-flap) |

### Stage SA-5: TOIN — Compression Policy Learning

```
quality = 0.4 × (1 - compression_ratio) + 0.4 × (1 - min(edit_time_ms, 60000)/60000) + 0.2 × outcome_factor

new_aggressiveness = old + 0.05 × (0.9 × avg_quality - old)
```

- **Min samples**: 10 per section before adjustment
- **Aggressiveness range**: [0.1, 0.95]
- **Consolidation**: Every 2 hours via scheduler
- **Confidence dampening**: `effective_adjustment × min(samples/50, 1.0)`
- **Edit-time collapse**: If >80% of samples have edit_time=0, reweight to drop edit factor

---

## Phase P4: Progressive Disclosure + Proactive Preloading

**Goal**: Preload next-needed resources before user request (ContextAgent gating). Reveal resources in 3-item blocks with masking (Agent Skills patterns).

### Subtask P4-S1: Workflow Graph Edge List

10,000-entry adjacency index: `defaultdict(list[tuple[int, float, str]])` mapping `target_id → [(source_id, weight, edge_type)]`. Python dict based on existing `resource_edges` table. O(1) lookup.

### Subtask P4-S2: Proactive Prediction Engine

Personalized PageRank (α=0.85, 20 iterations) over workflow graph. Cold-start fallback: BFS weighted random walk (50 walks × 4 depth). Produces `list[tuple[resource_id, score]]`, max 20 candidates.

**Hyperparameters**:
- `PPR_ALPHA = 0.85` (teleport probability)
- `PPR_ITERATIONS = 20`
- `MAX_PREDICTIONS = 20`
- `BFS_WALKS = 50`, `BFS_DEPTH = 4`

### Subtask P4-S3: M2_H3 Signal Processor

M2_H3 = medium frequency (200ms debounce), high heterogeneity (entropy ≥ 0.5 filter). Ignores burst signals from rapid-fire tool calls.

### Subtask P4-S4: CoT Gating + L3 Prefetch

2-step chain-of-thought reasoning:
1. "Has user interacted with ≥3 resources from this cluster?" → if yes, proceed
2. "Is ratio of cached next-stage resources ≥ 0.6?" → if yes, prefetch

**Guard**: Max 2 concurrent preloads, max 4K tokens total. 120s cooldown after 3rd preload-evict cycle. Use `asyncio.Semaphore(2)`.

### Subtask P4-S5: Disclosure DFA

5-state deterministic finite automaton:
```
IDLE → DISCLOSED_1 → DISCLOSED_2 → ... → DISCLOSED_N
```
- `block_small = 3`: Always send 3 resources first
- `mask_threshold = 0.15`: Hide resources with relevance < 15%
- `reveal_on`: User invocation of last resource in block
- `collapse_after = 5`: After 5 blocks, summarize entire stack

### Subtask P4-S6: Block Manager

Maintains `dict[session_id, dict[str, Any]]` for disclosure state per session. Resources sorted by ISO score descending. Summary generated at block 5+ using first-sentence extraction (~500 chars).

**Memory overhead**: ~50 KB per session.

---

## Phase P5: TOIN Feedback Learning + KVFlow Eviction

### Subtask P5-ST1: TOIN Feedback Learning

| Aspect | Analysis |
|--------|----------|
| **Data structures** | `SectionState: dict[str, SectionState]` — per-section samples + aggressiveness. `toin_section_state` + `toin_samples` SQL tables |
| **Algorithms** | Quality = weighted blend (0.4 compression + 0.4 edit_time + 0.2 outcome). EMA update: new_aggr = old + 0.05 × (0.9 × avg_quality - old) |
| **Complexity** | record_sample: O(1) dict insert. Consolidation: O(S) where S = #sections, every 2h |
| **Cold start** | Initial aggressiveness = 0.5. 10-sample minimum per section. Global fallback until any section reaches threshold |
| **Integration** | Piggybacks on existing `record_outcome()` in learning.py — no new hooks |

**Guardrails**:
- record_sample() must complete in <1ms (hot path)
- Consolidation via separate thread — never on hot path
- Aggressiveness clamped to [0.1, 0.95]
- user_edit_time_ms clamped to 0 minimum (avoid NaN)

### Subtask P5-ST2: KVFlow Workflow-Aware Eviction

| Aspect | Analysis |
|--------|----------|
| **Data structures** | `incoming_edges: dict[int, int]` precomputed from `resource_edges`. 3-tier enum: MUST_KEEP (≤2 edges), REDUCIBLE (≤5), EVICTABLE (>5 or age > TTL) |
| **Algorithms** | Batch modulo 64: 63/64 calls return immediately, 1/64 scans all resources → sorts by size×age → evicts 20% of EVICTABLE candidates |
| **Safe guards** | L0 never evicted. New resources get 1h grace. Re-entry gets 5min grace. Max 20% per batch (anti-burst) |
| **Persistence** | `kvflow_evictions` audit table logs all evictions with reason, edge count, age, size |

**Edge count**: Rebuilt every 1h from `resource_edges`. O(E) where E = total edges.

### Integration Points (P5-P3)

Both subsystems integrate into existing `record_outcome()` in learning.py. No new MCP tools. No new hooks.

```
record_outcome() → TOIN.record_sample()
ContextHarness.evict() → KVFlow.kvf_evict()
Compressor.compress() → TOIN.get_aggressiveness()
Consolidation → shared APScheduler (2h TOIN + 1h KVFlow edge rebuild)
```

---

## Phase P6: Integration + MCP Tools

Wire all subsystems together. New FastMCP endpoints:

| Tool | Purpose | Input | Output |
|------|---------|-------|--------|
| `context_init` | Warm cache for a project type | project_type, eager_layers | CacheStatsResponse |
| `context_assets` | Serve merged asset bundle | layer, bundle_name, top_k | AssetCacheResponse |
| `context_precache` | Proactive preload prediction | task_context | list[PreloadPrediction] |
| `context_pull` | Full layered serve | query, project_type | HarnessResponse |
| `context_clear` | Flush cache layer(s) | layer (optional) | CacheStatsResponse |
| `context_cache_stats` | Get cache metrics | — | CacheStatsResponse |

**Engine modification**: `harness()` method wrapped with:
```
if cache.enabled:
    l0_assets = cache.get_l0()
    l3_result = cache.get_l3(query) or _compute_iso(query, cache)
    return fuse(l0_assets + l3_result)
else:
    return original_harness(query)
```

**<20 lines change** to existing engine.py. All cache logic in new `cache.py`.

---

## Phase V: Verification Suite

### Block-Level Checks per Phase

| Phase | Verification Block | Verdict Criteria |
|-------|-------------------|------------------|
| P0 | Classifier accuracy | All 2,181 resources classified, no Nones, known test cases pass |
| P0 | Bundle integrity | 7 bundles, no PK violations, is_canonical=1 max per resource |
| P1 | L0-L3 load | Each layer populates with correct resources, token caps enforced |
| P1 | Cache walk | L0→L1→L2→L3 walk returns first hit, fallthrough works |
| P2 | CAG/RAG routing | Confidence ≥ 2 → CAG, elsewhere RAG. No oscillation >3 flips/60s |
| P2 | Writeback | RAG top-1 inserted into L3, second lookup hits cache |
| P3 | Compression ratio | Each strategy hits target ratio ±10% on 90%+ of sections |
| P3 | Volatile detector recall | >90% recall on synthetic corpus, >95% precision |
| P3 | JSON validity | All compressed JSON parseable by json.loads() |
| P4 | Preload budget | Max 2 concurrent preloads, max 4K tokens. Never blocks sync path |
| P4 | Disclosure state | DFA transitions correctly: 3→reveal→3→reveal→...→summarize |
| P5 | TOIN aggressiveness | After 10+ samples, aggressiveness ≠ 0.5. Never exceeds [0.1, 0.95] |
| P5 | KVFlow L0 guard | Zero L0 resources in kvflow_evictions audit table |
| P5 | KVFlow batch | Every 64th call triggers eviction. Max 20% candidates per batch |
| P6 | Engine regression | `harness()` returns identical results with cache on and off |
| P6 | MCP tools respond | All 6 new endpoints return valid JSON, zero exceptions |

### Pre-Commit Smoke Test

```bash
python3 -m pytest tests/test_cache.py -x -q          # P1 pass
python3 -m pytest tests/test_compression.py -x -q     # P3 pass
python3 -m pytest tests/test_preloading.py -x -q      # P4 pass
python3 -m pytest tests/test_toin_kvflow.py -x -q     # P5 pass
python3 -m pytest tests/test_integration.py -x -q     # P6 pass
```

## Phase A: Audit & Outcome Feedback

After each phase, log outcome to NEXUS:

```python
nexus_log_outcome("context-harness-P0", "correct"|"wrong", "Project classifier + bundle seeder")
nexus_log_outcome("context-harness-P1", "correct"|"wrong", "4-layer cache hierarchy")
nexus_log_outcome("context-harness-P2", "correct"|"wrong", "CAG/RAG hybrid router")
nexus_log_outcome("context-harness-P3", "correct"|"wrong", "Multi-stage compression engine")
nexus_log_outcome("context-harness-P4", "correct"|"wrong", "Proactive preloading + disclosure")
nexus_log_outcome("context-harness-P5", "correct"|"wrong", "TOIN learning + KVFlow eviction")
```

---

## Execution Order (DAG)

```
Level 0: P0-S1, P1-0, P4-S1, P5-grounding
    ↓
Level 1: P0-S2, P0-S4, P1-1, P3-SA1
    ↓
Level 2: P0-S3, P0-S5, P1-2, P1-3, P2-cache-chain, P3-SA2, P4-S2, P4-S3
    ↓
Level 3: P0-S6, P1-4, P2-conf-tracker, P2-decision-gate, P3-SA3, P3-SA4, P4-S4, P4-S5, P5-ST1, P5-ST2
    ↓
Level 4: P1-5, P2-writeback, P2-persona, P3-SA5, P4-S6, P5-P3
    ↓
Level 5: P6 (Integration), V (Verification)
    ↓
Level 6: A (Audit)
```
