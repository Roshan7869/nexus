# M12 — `server/cache/redis_cache.py` (CREATE, optional L4)

## GitNexus queries
```
query({search_query: "redis cache centroid embedding lookup"})
context({name: "_RedisStateBackend"})  # best-effort get/set/delete pattern to copy
context({name: "MemoryStore"})         # rate proxy interface Redis must satisfy
```
Used: `read circuit_breaker.py:61-108` (`_RedisStateBackend`, `_make_redis`: `NEXUS_REDIS_URL/REDIS_URL`, 1s timeout, decode_responses, warn-and-None), `grep requirements` (no redis dep — keep it that way), remediation plan ARCH-3 (TTL + hit/miss), Tencent GQA/CLA (quantize + dedup) as design analog.

## Current state
File does not exist. Remediation docs specify `get_redis() -> redis.Redis` + skill/centroid TTL cache, but nothing implemented.

## Blast radius: NONE (new file; imported under try/except only in M13)

## Pseudocode
```python
"""Optional Redis L4 — never SoT, never required. Import-guarded."""
try:
    from redis.asyncio import Redis as AsyncRedis
    _REDIS_AVAILABLE = True
except Exception:
    AsyncRedis = None; _REDIS_AVAILABLE = False

def get_redis():
    # returns None unless NEXUS_HARNESS_REDIS=1 AND package installed AND server reachable
    if os.environ.get("NEXUS_HARNESS_REDIS", "0") not in ("1","true","yes"): return None
    if not _REDIS_AVAILABLE: return None
    url = os.environ.get("NEXUS_REDIS_URL", os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    try: return AsyncRedis.from_url(url, decode_responses=True, socket_connect_timeout=1)
    except Exception: logger.warning(...); return None

async def cached_skill_lookup(redis, key, loader: Callable, ttl=300) -> tuple[value, bool]:
    # hit -> (json.loads, True) + HINCR hits; miss -> value=await loader(); SETEX + HINCR miss
    # any redis error -> loader() direct, (value, False), warn once
    ...

COUNTERS = {"hits": 0, "miss": 0, "fallback": 0}   # in-memory mirror for dashboard when redis off
```

## Build steps
1. Write module with no import-time side effects; `get_redis()` never raises.
2. GQA/CLA analog (follow-up, not in M-scope): int8-quantize L1/L2 payloads before SETEX; L1↔L2 dedup via shared field.
3. Dashboard reads `COUNTERS` when redis None.

## Tests
`test_get_redis_none_by_default`, `test_fallback_on_no_server`, `test_hit_miss_counters`.
