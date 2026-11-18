# M11 — `server/router_v2/v5.py` (MODIFY, adaptive gate + Chimera fusion)

## GitNexus queries
```
context({name: "unified_route_v5"})  # :408-597: L1→short-circuit→L2 top-2→L3→L4 autocut
impact({target: "unified_route_v5", direction: "upstream"})  # cutover + execution_plane + MCP
query({search_query: "short circuit confidence cluster pool"})
```
Used: `read v5.py:1-80` (HGMF refiner docstring, weights, targets agreement>60% p95<300ms) + `:408-467` (signature: top_k/cold_fraction/short_circuit_confidence/min_pool/intent/autocut/fusion_mode/shadow). Tencent TurboS (adaptive long-short CoT) + MT-Chimera (weak-to-strong fuse).

## Current state
Fixed `SHORT_CIRCUIT_CONFIDENCE` + `SHORT_CIRCUIT_MIN_POOL`; L3 single-path argmax via `v5_fusion_scores`; `fusion_mode` convex/wrrf + `shadow` compare exists.

## Blast radius: HIGH — production ranker (`USE_UNIFIED_ROUTER=2`)
Rules: default policy = current constants; Chimera off (`NEXUS_V5_CHIMERA=0`). Any default change needs A/B + shadow agreement.

## Pseudocode
```python
def should_short_circuit(conf: float, pool: int, task_complexity: float,
                         conf_thr=SHORT_CIRCUIT_CONFIDENCE, pool_min=SHORT_CIRCUIT_MIN_POOL) -> bool:
    # TurboS analog: fast no-think path only when confident AND pool deep AND task simple
    # default: (conf > conf_thr and pool >= pool_min and task_complexity < 0.5)
    # == current behavior when complexity estimator returns 0.0
    ...

def estimate_complexity(query: str) -> float:
    # cheap heuristics first (length, code spans, multi-verb); LLM judge later
    return 0.0  # default preserves current behavior

def chimera_fuse(candidates: list, k_variants=3) -> list:
    # MT-Chimera analog: score K cheap variants (varied weight jitters), fuse to one ranking
    # behind NEXUS_V5_CHIMERA=1; shadow-compare vs single-path
    ...

# in unified_route_v5 body:
  complexity = estimate_complexity(query)
  if should_short_circuit(confidence, pool_size, complexity, short_circuit_confidence, short_circuit_min_pool):
      return short_circuit_path(...)   # unchanged
  ...
  if os.environ.get("NEXUS_V5_CHIMERA", "0") == "1":
      merged = chimera_fuse(merged)
```

## Build steps
1. Extract gate to `should_short_circuit` + stub estimator (returns 0.0).
2. Add `chimera_fuse` behind flag; shadow-compare agreement@5 vs convex.
3. Promote only on +agreement, flat p95.

## Tests
`test_gate_default_identical`, `test_complexity_stub_zero`, `test_chimera_behind_flag`, `test_shadow_agreement_reported`.
