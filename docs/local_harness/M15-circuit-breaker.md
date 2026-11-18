# M15 — `server/resilience/circuit_breaker.py` (MODIFY, realm-partitioned keys)

## GitNexus queries
```
context({name: "CircuitBreaker"})   # CLOSED→OPEN→HALF_OPEN, fail_count/reset_timeout
context({name: "Bulkhead"})         # per-category semaphore
impact({target: "get_breaker", direction: "upstream"})
```
Used: `read circuit_breaker.py:1-140` (`CircuitState`, `CircuitBreakerOpen/BulkheadFull`, `_RedisStateBackend` prefix `nexus:circuit`, `_make_redis`, `CircuitBreaker.__init__` with dual asyncio+threading locks). Cordis §3.2 isolate (realm table ρ + store σ).

## Current state
Single global keyspace `nexus:circuit:<name>` — concurrent OpenCode sessions share breaker state (one session's failures open the circuit for all).

## Blast radius: MEDIUM — breaker decisions affect all tool calls; default realm preserves current keys
Rules: default `realm="default"` reproduces today's keys exactly; session realm only when caller passes it.

## Pseudocode
```python
class _RedisStateBackend:
    def __init__(self, redis, key_prefix="nexus:circuit", realm="default"):  # NEW kwarg, default compat
        ...
        self._realm = realm or "default"
    def _key(self, name):
        # was: f"{prefix}:{name}" → now: f"{prefix}:{realm}:{name}" with realm=default == old? NO —
        # compat requires old keys verbatim, so:
        if self._realm == "default": return f"{self._key_prefix}:{name}"
        return f"{self._key_prefix}:{self._realm}:{name}"

# in-memory breaker registry (module-level dict name->breaker): key by (realm, name)
def get_breaker(name, fail_count=5, reset_timeout=30.0, redis=None, realm="default"):  # NEW kwarg
    ...
```

## Build steps
1. Add `realm` kwargs with `"default"`; verify default keys byte-identical (log sample before/after).
2. M5/M7 pass `realm=lh.ctx.realm` (`local:<session_id>`) for session-scoped breakers; global tools keep default.
3. Memory-registry growth guard: LRU-cap session realms (sessions dispose → registry entry dropped in `dispose()`).

## Tests
`test_default_keys_unchanged`, `test_realm_isolation`, `test_registry_cleanup_on_dispose`.
