# M14 — `server/integrations/rate_limited_proxy.py` (MODIFY, docs-only)

## GitNexus queries
```
context({name: "MemoryStore"})
query({search_query: "rate limit quota token bucket proxy"})
```
Used: `grep` (line 27: `MemoryStore` satisfies `redis.asyncio.Redis` interface; line 127: store may be either). No behavior change needed for local harness — memory default is already the correct local-first choice.

## Current state
Dual-backend by interface compatibility. Correct as-is.

## Blast radius: NONE (docstring + comments only — no code edits)

## Pseudocode (docs, not code)
```python
class MemoryStore:
    """Local-harness default quota store.

    Satisfies the subset of the redis.asyncio.Redis interface used by the proxy
    (get/set/delete with TTL). Prefer this for IDE-local sessions: zero deps,
    zero network, per-process isolation. Pass a Redis client only to share quota
    across processes (see server/cache/redis_cache.py M12).
    """
```

## Build steps
1. Docstring + module comment only.
2. No test changes; existing proxy tests must pass untouched.

## Tests
None new (existing suite is the gate).
