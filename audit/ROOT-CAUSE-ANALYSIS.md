# NEXUS Routing Failure — Root Cause Analysis

## Executive Summary

The claim that 49% of resources are "stale" is misleading. These resources are **not being found by the routing system** due to 5 compounding defects in NEXUS's own pipeline. Every resource was added with a purpose — the routing system fails to surface them.

---

## ROOT CAUSE 1 [CRITICAL]: Cluster Membership Desync

### Symptom
261 resources have `resources.cluster` set but NO corresponding `cluster_membership` entry. 20 more have conflicting values between the two tables.

### Mechanism
The architecture has a trigger chain:
```
INSERT INTO cluster_membership → TRIGGER → UPDATE resources.cluster
```

This ensures `resources.cluster` is always a denormalized copy of `cluster_membership`. **BUT** two scripts write directly to `resources.cluster`, bypassing the triggers:

**File: `scripts/recluster_low_confidence.py:116`**
```python
conn.execute(
    "UPDATE resources SET cluster = ?, cluster_confidence = ?, updated_at = datetime('now') WHERE id = ?",
    (best_cluster, round(best_score, 3), rid),
)
```

**File: `scripts/assign-clusters.py:71`**
```python
cur.execute("UPDATE resources SET cluster=? WHERE name=? AND (cluster IS NULL OR cluster='')", (cluster, name))
```

Neither script adds the corresponding `INSERT INTO cluster_membership`. The triggers on `cluster_membership` fire only when `cluster_membership` is modified — they do NOT fire on `resources.cluster` UPDATE.

### Impact on Routing
The routing pipeline reads from inconsistent sources:

| Code Path | Reads From | Finds Desync'd? |
|-----------|-----------|----------------|
| `ClusterRouter.get_cluster_resource_ids()` | `resources.cluster` | ✅ Yes |
| `ClusterRouter.search()` | `cluster_membership` JOIN | ❌ No |
| `unified_route._resource_search()` | `cluster_membership` JOIN (preferred) | ❌ No |
| `unified_route._filter_by_clusters()` | `cluster_membership` | ❌ No |
| `ClusterRouter._load_centroids()` | `cluster_definitions` (built from `cluster_membership`) | ❌ No |

Resources written by recluster scripts are **invisible** to the cluster-constrained search paths. They can only be found by unconstrained (no cluster filter) search — which is the minority of queries.

### Research Reference
- **State drift in distributed systems** — the `cluster_membership` and `resources.cluster` tables have diverged. This is a textbook cache-invalidation bug.
- **Fix pattern**: all writes to `resources.cluster` must go through `cluster_membership` INSERT (which triggers the sync). Or add a trigger on `resources.cluster` UPDATE that back-syncs to `cluster_membership`.

---

## ROOT CAUSE 2 [HIGH]: Cold-Start Trap in Two-Tier Split

### Symptom
1068 resources (49%) have `times_triggered=0`. Once a resource hits zero, it stays zero because the routing system actively suppresses it.

### Mechanism
**File: `server/router_v2/__init__.py:408-424`**
```python
if cold_fraction > 0.0:
    hot_k, cold_k = two_tier_split(top_k, cold_fraction)
    ...
    hot = [x for x in ranked if x.get("resource_id") in triggered][:hot_k]
    cold = [x for x in ranked if x.get("resource_id") not in triggered][:cold_k]
```

With `cold_fraction=0.3` and `top_k=10`: **only 3 cold slots per query**.

A cold resource must:
1. Beat 1068 other cold resources for one of 3 slots
2. Have a high enough RRF/blend score despite having zero usage history
3. Match the query closely enough through either lexical or semantic search

Additionally, **File: `server/router_v2/scoring.py:97`**
```python
"SELECT COALESCE(SUM(times_triggered), 0) FROM resources"
```
The UCB1 bonus formula uses `times_triggered` in the denominator — resources with zero triggers get maximum exploration bonus. BUT the two-tier split caps cold resources at 30% before UCB1 even applies.

### The Feedback Loop Trap
```
Resource has times_triggered=0
→ Two-tier split assigns to cold tier (max 3 slots)
→ Rarely selected because 1068 compete for 3 slots
→ times_triggered stays 0
→ Forever cold
```

### Research References
- **ToolkenGPT (arXiv:2310.03054)** — proposes **adaptive** cold_fraction based on query novelty, not fixed 0.3. Novel/unusual queries should get more cold exploration (0.5+).
- **Epsilon-greedy in recommender systems** — a minimum exploration rate (ε=0.1-0.2) ensures every resource has a *chance* regardless of history.
- **Sutton & Barto, "Reinforcement Learning" Ch.2** — the explore/exploit dilemma. Fixed cold_fraction is a poor exploration strategy. Thompson Sampling (already used in the blend) should be the sole exploration signal, not a hard tier split.

---

## ROOT CAUSE 3 [HIGH]: Sign Quantization Information Loss

### Symptom
Resources with short/empty bodies produce near-random 384-dim embeddings. After sign quantization (binary: 1 if positive, else 0), these become near-equal 48-byte vectors that match nothing.

### Mechanism
**File: `server/router_v2/bitvec.py:44-61`**
```python
@staticmethod
def pack(float_vec: np.ndarray | list[float]) -> bytes:
    bits = (arr > 0).astype(np.uint8)
    packed = np.packbits(bits).tobytes()
    return packed
```

Sign quantization loses ALL magnitude information. A vector `[0.01, 0.02, 0.03]` becomes `[1, 1, 1]` — same as `[100, 200, 300]`. Resources with near-zero embeddings (from short/empty bodies) get near-random bit patterns that match nothing semantically.

The `vec_chunks` index stores these 48-byte sign-quantized vectors for ALL resources. The Hamming distance search at query time finds resources only if their sign pattern agrees — which requires non-trivial embeddings for both the query AND the resource.

### Research Reference
- **Sign quantization works well for high-magnitude embeddings (tier 1 resources with rich content) but fails for low-magnitude ones**
- **Fix**: maintain a separate float32 index for cold/low-token resources. Use HNSW for the float index for fast approximate search.
- **Alternative**: use product quantization (PQ) instead of sign quantization — retains more information at similar compression rates.

---

## ROOT CAUSE 4 [MEDIUM]: Chunk→Resource Unlinking

### Symptom
54,633 chunks (81.4% of 67,074) have NO `resource_id` link. The chunk content describes what resources DO but is disconnected from resource routing.

### Mechanism
The `ingestion_chunks` table stores 67,074 content chunks extracted from skill files, docs, etc. Each chunk has a `resource_id` foreign key — but 54,633 have NULL.

**File: `server/router_v2/__init__.py:13-16`** (docstring):
```
DATA NOTE: chunks_fts (66495 chunks) has 0/66495 chunks linked to a
resource_id, so a chunk-level FTS path returns no named skills. Until
chunk→resource linking is backfilled...
```

The chunk ingestion pipeline (`server/ingest/pipeline.py`) creates chunks but the linker (`server/ingest/linker.py`) only links ~12K of them (266 distinct resources). The rest are orphaned content describing resource capabilities that routing can't use.

### Impact
The `use_chunks=True` path in `unified_route` would use chunk content for retrieval — but with only 2,441 linked chunks (from 266 resources), most resources have NO chunk content contributing to their routing signal.

### Fix
- Run the linker against ALL chunks, not just newly ingested ones
- The linker maps chunk headings to resource names — improve heading→name matching

---

## ROOT CAUSE 5 [MEDIUM]: Multiple Competing Routers

### Symptom
NEXUS has 9+ routing implementations, each with slightly different logic, reading from different data sources, and producing different results.

### Router Inventory

| Router | File | Entry Point | Uses Clusters | Increments Triggered |
|--------|------|-------------|---------------|---------------------|
| v2 unified_route | `router_v2/__init__.py` | `unified_route()` | ✅ (prefers cluster_membership) | ❌ |
| v5 unified_route | `router_v2/v5.py` | L1+L2+L3 pipeline | ✅ (ClusterRouter → resources.cluster) | ❌ |
| v4 ToolAttention | `tool_attention_v4.py` | ISO scoring | ❌ (full scan) | ✅ |
| v3 ToolAttention | `tool_attention.py` | FAISS + keyword | ❌ | ❌ |
| ClusterRouter | `cluster_router.py` | centroid affinity | ✅ (resources.cluster) | ❌ |
| Main Router | `router.py` | hybrid search | ✅ | ✅ |
| CrossType | `cross_type_router.py` | cross-type | ❌ | ❌ |
| NumpyRouter | `numpy_router.py` | numpy similarity | ❌ | ❌ |
| HierarchyRouter | `hierarchy_router.py` | hierarchy | ❌ | ❌ |

The `opencode.json` MCP config selects between these via `USE_UNIFIED_ROUTER=0|1|2`. Each router has different query handling, different cold-start behavior, and different cluster awareness. A query that finds nothing in v2 might find resources in v4 — but the live system only runs one.

### Research Reference
- **arXiv:2409.02141 (ToolRefiner)** — advocates for a SINGLE refiner that unifies all signals, not multiple competing routers. The refiner should consume all signals (lexical, semantic, cluster, chunk) and produce one ranking.

---

## SUMMARY: Why Resources Don't Get Triggered

The resources are not "stale" — they're **invisible to the routing system**:

```
Resource ingested with cluster assignment
→ cluster assigned by recluster_low_confidence.py or assign-clusters.py
   → writes to resources.cluster ONLY (does NOT insert into cluster_membership)
→ ClusterRouter loads centroids from cluster_definitions (built from cluster_membership)
→ Centroids MISS this resource, so query-centroid affinity doesn't activate its cluster
→ Even if cluster activates, v2 router's _resource_search JOINs cluster_membership
   → MISSES the resource because it has no cluster_membership entry
→ Resource never appears in routing results
→ times_triggered stays 0
→ Labeled "stale"
```

**The resource never had a chance. It's not stale — it's been silenced by a data integrity bug.**

Research papers referenced:
- arXiv:2604.21816 (Tool Attention v4)
- arXiv:2409.02141 (Tool2Vec/ToolRefiner)
- arXiv:2310.03054 (ToolkenGPT)
- arXiv:2210.11934 (Bruch et al. RRF analysis)
- arXiv:2508.07602 (HGMF)
- Sutton & Barto, Reinforcement Learning (explore/exploit)
