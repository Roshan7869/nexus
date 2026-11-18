# M10 — `server/router_v2/fusion.py` (MODIFY, shared-expert prior + recycle)

## GitNexus queries
```
context({name: "v5_get_weights"})    # line 322: NEXUS_V5_W_<NAME> overrides, renormalize
context({name: "v5_fusion_scores"})  # line 396: convex combine entry point
impact({target: "v5_fusion_scores", direction: "upstream"})  # v5.py L3 callers
```
Used: `grep` (fusion 531 lines: RRF baseline + Thompson hook + v5 convex; `v5_get_weights` env-override pattern), `read v5.py:408-467` (L3 inputs topic/intent/skill_match/listwise). Tencent Large: 1 shared + 16 specialized top-1, recycle routing, expert-specific LR.

## Current state
L3 = fixed 4-term convex (0.45/0.25/0.20/0.10), env-overridable per-term via `NEXUS_V5_W_<NAME>`. No global prior, no capacity spill.

## Blast radius: MEDIUM — ranking changes are behavioral; defaults must be identical
Rules: default `w_shared=0.0` + `NEXUS_V5_CLUSTER_CAP=0` (disabled) → byte-identical ranking. All new behavior behind env.

## Pseudocode
```python
V5_DEFAULTS = {topic:0.45, intent:0.25, skill:0.20, listwise:0.10, shared:0.0}  # NEW key, 0 default

def v5_get_weights(weights=None) -> dict:
    w = {**V5_DEFAULTS, **(weights or {})}
    for key in w:  # existing NEXUS_V5_W_<NAME> pattern extended
        env_val = os.environ.get(f"NEXUS_V5_W_{key.upper()}")
        if env_val is not None: w[key] = float(env_val)
    return renormalize(w)   # existing renormalization unchanged

def shared_prior(resource: dict, global_stats: dict) -> float:
    # Hunyuan shared-expert analog: common-knowledge prior, e.g. log(1+times_correct)/Z
    ...

def v5_fusion_scores(topic_norm, intent, skill_match, listwise, shared=None, weights=None) -> float:
    w = v5_get_weights(weights)
    return (w[topic]*topic_norm + w[intent]*intent + w[skill]*skill_match
            + w[listwise]*listwise + w[shared]*(shared or 0.0))

def recycle_spill(ranked: list, cap: int) -> list:
    # per-cluster capacity (NEXUS_V5_CLUSTER_CAP); overflow spills to 2nd cluster order
    # (v5 already constrains L2 to top-2 clusters — enforce the capacity factor here)
    ...
```

## Build steps
1. Add `shared` key + two fns; defaults off.
2. Sweep `NEXUS_V5_W_SHARED=0.10` with others renormalized (start [0.40,0.22,0.18,0.10,0.10]) via `route_ab_compare` + 20-query `router_ab_suite`; require agreement +3-5pp, p95 flat.
3. Shadow-compare (`NEXUS_ROUTER_SHADOW=true`) before promoting any default.

## Tests
`test_weights_default_identical`, `test_shared_prior_bounded`, `test_recycle_spill_preserves_top2`.
