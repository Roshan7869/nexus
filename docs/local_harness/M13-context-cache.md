# M13 — `server/context_harness/cache.py` (MODIFY, L4 hooks + DB_PATH fix)

## GitNexus queries
```
context({name: "L0Cache"}) / ({name: "L3Cache"})  # fill/get/set/clear, token budgets
impact({target: "get_l0_cache", direction: "upstream"})  # eager-load callers
query({search_query: "cache persistence sqlite msgpack"})
```
Used: `read cache.py:1-100` (L0 256/2048, L1 500/8192, L2 400/16384, L3 64/4096 TTL300, `_L0_SQL` core_infra, msgpack-optional) + `bash` (line 29 `DB_PATH = Path.home()/"nexus/db/nexus.db"` vs `server/db.py` `get_db_path()` honoring `NEXUS_DB_PATH`/`NEXUS_BROKER_DB_PATH`).

## Current state
L0-L3 correct and token-budgeted, but DB path ignores `NEXUS_DB_PATH` (breaks `.mcp.json` custom paths + local harness portability). No L4.

## Blast radius: MEDIUM — hot path (fill/get/set refs 137/2371/580)
Rules: L0-L3 semantics byte-identical; L4 = read-through wrapper only; DB_PATH fix defaults to old path when env unset.

## Pseudocode
```python
# top of file (additive):
try:
    from server.cache.redis_cache import get_redis, cached_skill_lookup  # M12, may not exist yet
    _L4 = get_redis()   # None by default; never raises
except Exception:
    _L4 = None; cached_skill_lookup = None

def _db_path() -> Path:   # NEW resolver; replaces direct DB_PATH uses at call sites
    from server.db import get_db_path   # lazy, avoids cycle
    try: return get_db_path()
    except Exception: return DB_PATH    # fallback = current hardcoded path (compat)

# in L3 get path (additive wrapper):
def l3_get(query_hash):
    hit = existing_l3_lookup(query_hash)   # UNCHANGED
    if hit: return hit
    if _L4 is not None and cached_skill_lookup is not None:
        value, _ = await_maybe(cached_skill_lookup(_L4, f"nexus:l3:{query_hash}", loader=db_loader, ttl=300))
        ...
    return existing_miss_path(...)
```

## Build steps
1. Add guarded import + `_db_path()`; migrate the two `sqlite3.connect(DB_PATH…)` sites (lines ~140/149) to `_db_path()`.
2. Add L3 read-through only (no write-path change in this step).
3. Assert: `NEXUS_DB_PATH` unset → identical path as today; set → honored.

## Tests
`test_db_path_override`, `test_l4_none_by_default`, `test_l3_semantics_unchanged`.
