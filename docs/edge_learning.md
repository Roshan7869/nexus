# NEXUS Edge Learning (Gap 3)

**Contract:** multi-skill terminal outcomes → `co_triggered` edges → ranking boost

## Root cause (pre-Gap 3)

| Symptom | Reality |
|---------|---------|
| `resource_edges` had 5k+ rows | Almost all `similar_to` / `member_of` static content edges |
| `co_triggered` edges | **5**, all with `co_trigger_count=0` |
| Weight updates on correct/wrong | Multiplied weights on **existing** edges only — never created co-occurrence |
| Ranking | Attached neighbor names as metadata; **score ignored weights** |

## Fix

1. **`learn_co_triggers()`** — on terminal multi-skill bundles, upsert undirected
   `co_triggered` pairs with `co_trigger_count++` and outcome-scaled weight.
2. **`record_outcome(..., co_skills=[])`** — session report / tool broker pass the
   full tool list; `routed_id` also loads `matched_skills` from `routing_log`.
3. **`_apply_edge_graph_boost()`** in `router.py` — small additive score boost
   (capped ~0.14) from live co-triggers + weaker `similar_to` prior.
4. **Backfill** — `scripts/run_edge_learning.py --apply` rebuilds from historical
   multi-skill `routing_log` rows.

## Ops gate

| Metric | Target |
|--------|--------|
| `co_triggered_with_count` | ≥ 10 |
| or `co_triggered` | ≥ 50 |

```bash
python scripts/run_edge_learning.py
python scripts/run_edge_learning.py --apply
python scripts/run_ops_eval.py   # edge_learning gate
```

## Non-goals

- Does not replace Thompson / keyword scoring (boost is intentionally small).
- Does not invent labels from `routed` alone (terminal outcomes only).
- Does not require supervisor-owned MCP (independent of Gap 2 topology).
