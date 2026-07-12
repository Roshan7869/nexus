# Paper2Code: Consolidated Algorithm-to-Fix Design

## Paper-to-Fix Mapping

| Paper | Algorithm Extracted | NEXUS Fix | File(s) to Modify |
|-------|--------------------|-----------|-------------------|
| **Tool Attention** 2604.21816 | ISO scoring with adaptive θ, state-aware gating, two-phase lazy loading | FIX 3b: Adaptive θ through `server/router_v2/__init__.py`, add query-entropy threshold calibration | `server/router_v2/__init__.py`, `server/router_v2/scoring.py` |
| **Tool2Vec** 2409.02141 | Usage-driven tool embeddings, staged retrieval with cross-encoder refiner | FIX 5: Link 54k chunks→resources via Tool2Vec-style embedding; add chunk→resource scoring as refiner stage | `server/ingest/linker.py`, `server/router_v2/__init__.py` |
| **ToolkenGPT** 2305.11554 | Two-tier hot/cold with epsilon-greedy exploration, tool embedding training | FIX 4: Adaptive `cold_fraction` based on query novelty (low-entropy→0.5, high-entropy→0.2) | `server/router_v2/__init__.py:408-424` |
| **RRF Analysis** 2210.11934 | Convex Combination fusion outperforms RRF; normalize first, CC is normalization-agnostic | FIX 6b: Replace RRF with CC fusion in v2 router (`score = α*l2 + (1-α)*semantic`) | `server/router_v2/__init__.py`, `server/router_v2/scoring.py` |
| **HGMF** 2508.07602 | Two-stage GMM hierarchical pruning, centroid freshness, regularized EM | FIX 3: Recompute centroids after any recluster; add GMM-based cluster quality metric | `server/cluster_router.py`, `scripts/compute_centroids.py` |

## Execution Order (Dependency-Aware)

```
Level 0 (no deps — run in parallel):
  ├── FIX 1: Patch recluster_low_confidence.py → INSERT cluster_membership
  └── FIX 2: SQL reconciliation of 261 desync'd resources

Level 1 (depends on FIX 1+2):
  ├── FIX 3: Recompute centroids (HGMF algorithm)
  ├── FIX 4: Adaptive cold_fraction (ToolkenGPT algorithm)
  └── FIX 5: Link chunks→resources (Tool2Vec algorithm)

Level 2 (depends on FIX 3+4+5):
  └── FIX 6: Consolidate routers + CC fusion (RRF Analysis algorithm)

Level 3 (depends on FIX 6):
  └── Adaptive θ calibration (Tool Attention algorithm)
```

## Key Algorithm: Adaptive Cold Fraction from ToolkenGPT

```python
def compute_cold_fraction(query: str, base_fraction: float = 0.3) -> float:
    """
    ToolkenGPT-inspired: adapt exploration budget based on query novelty.
    Low-entropy (short/generic) queries → more exploration.
    High-entropy (long/specific) queries → more exploitation.
    """
    entropy = estimate_query_entropy(query)  # normalized 0..1
    # Low entropy = generic query, need more cold exploration
    # High entropy = specific query, hot resources likely match
    return base_fraction + (0.2 * (1.0 - entropy)) - (0.1 * entropy)
    # Range: 0.2 (high-entropy) to 0.5 (low-entropy)
```

## Key Algorithm: Convex Combination Fusion from Bruch et al.

```python
def convex_combination_fusion(
    lexical_scores: dict[int, float],
    semantic_scores: dict[int, float],
    alpha: float = 0.5
) -> list[int]:
    """
    Replace RRF with CC fusion. Bruch et al. (2023) proved CC outperforms RRF
    in both in-domain and out-of-domain settings.
    """
    all_ids = set(lexical_scores) | set(semantic_scores)
    def minmax(scores):
        if not scores: return {}
        m, M = min(scores.values()), max(scores.values())
        if M == m: return {k: 0.0 for k in scores}
        return {k: (v - m) / (M - m) for k, v in scores.items()}
    lex_norm = minmax(lexical_scores)
    sem_norm = minmax(semantic_scores)
    combined = {}
    for rid in all_ids:
        combined[rid] = alpha * sem_norm.get(rid, 0.0) + (1-alpha) * lex_norm.get(rid, 0.0)
    return sorted(combined, key=combined.get, reverse=True)
```

## Key Algorithm: GMM Cluster Quality (from HGMF)

```python
def compute_cluster_quality(cluster_membership_path: str) -> dict:
    """
    HGMF-style cluster quality assessment using likelihood scores.
    After any recluster operation, verify cluster separation.
    """
    # For each cluster, compute:
    # - inter_class_separation: Σ_i Σ_{j≠i} ||μ_i - μ_j||²
    # - intra_class_compactness: Σ_i tr(Σ_i)
    # - silhouette score: (b - a) / max(a, b)
    # Alert if quality drops below threshold
```
