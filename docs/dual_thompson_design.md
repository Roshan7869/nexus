# Dual Thompson Sampling — Design Note

## Overview

NEXUS has two Thompson Sampling systems that share the same `model_beliefs`
table but use **different keyspaces**. This is by design, not a bug.

## Keyspace 1: `core/learner.py` (LLM Proxy)

- **Used by**: `core/server.py` (LLM gateway)
- **Trigger**: When `nexus.yaml:routing.enabled: true`
- **Key format**: `(provider, model, task_type)` where:
  - `provider` ∈ {`ollama_cloud`, `anthropic`, `openai`, `bedrock`, etc.}
  - `model` is an LLM model name like `qwen3-coder:480b-cloud`
- **Current rows**: ~15 distinct beliefs (per LLM proxy usage)
- **Update signals**:
  - IDE events (`code_accepted`, `test_passed`, etc.) — 50% weight
  - Benchmark results — 30% weight
  - Implicit signals (latency, success) — 20% weight

## Keyspace 2: `server/learning.py` (MCP Resource Router)

- **Used by**: `server/router.py`, `server/broker_runtime.py`, etc.
- **Trigger**: Always (default router)
- **Key format**: `(provider='nexus', model='resource:<name>', task_type)` where:
  - `provider` is hardcoded to `nexus` (not ideal naming — see below)
  - `model` is `resource:<resource_name>` (e.g., `resource:adaptive-imagining-cat`)
- **Current rows**: ~1,812 distinct beliefs
- **Update signals**:
  - `record_outcome()` from routing decisions
  - `_update_thompson_belief()` after each route

## Why two systems?

The LLM proxy and the MCP resource router answer **different questions**:

1. **LLM Proxy** (keyspace 1): "For this *task type*, which *LLM model* should
   I call?" — used at the network layer.

2. **MCP Router** (keyspace 2): "For this *task type*, which *resource/skill*
   should I invoke?" — used at the routing layer.

The 80 rows with `provider='nexus'` but non-`resource:*` models are leftover
from hand-inserted test data or from early bridge code. They are
**harmless** — they just have low request counts.

## Where they could collide

If `core/learner.py` ever calls `get_belief('nexus', 'some-skill', ...)`,
it would write to keyspace 2's territory. To prevent this:

- `core/learner.py` is restricted to LLM provider names (no `nexus`)
- `server/learning.py` is restricted to `provider='nexus'` and `model LIKE 'resource:%'`

## Future cleanup

The naming `provider='nexus'` for resources is confusing. A future refactor
should split `model_beliefs` into two tables:

- `llm_beliefs` (provider, model, task_type, alpha, beta, ...)
- `resource_beliefs` (resource_id, task_type, alpha, beta, ...)

This would be a breaking change to the schema and require migration. Not
worth doing until the routing team grows.

## Verification

```sql
-- Keyspace 1 (LLM models)
SELECT COUNT(DISTINCT provider||':'||model) FROM model_beliefs
WHERE provider != 'nexus';

-- Keyspace 2 (resources)
SELECT COUNT(DISTINCT provider||':'||model) FROM model_beliefs
WHERE provider = 'nexus' AND model LIKE 'resource:%';

-- Overlap / anomalies
SELECT COUNT(*) FROM model_beliefs
WHERE provider = 'nexus' AND model NOT LIKE 'resource:%';
```
