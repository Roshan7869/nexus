"""Optional Redis L4 — never SoT, never required. Import-guarded.

Remediation ARCH-3 + Tencent GQA/CLA analog (quantize + dedup = follow-up).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable

logger = logging.getLogger(__name__)

try:
    from redis.asyncio import Redis as AsyncRedis
    _REDIS_AVAILABLE = True
except Exception:  # pragma: no cover - fallback path
    AsyncRedis = None  # type: ignore
    _REDIS_AVAILABLE = False

COUNTERS = {"hits": 0, "miss": 0, "fallback": 0}  # in-memory mirror for dashboard


def _enabled() -> bool:
    return os.environ.get("NEXUS_HARNESS_REDIS", "0").lower() in ("1", "true", "yes")


def get_redis() -> Any | None:
    """Return a client or None. Never raises. None unless explicitly enabled."""
    if not _enabled() or not _REDIS_AVAILABLE:
        return None
    url = os.environ.get("NEXUS_REDIS_URL", os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    try:
        return AsyncRedis.from_url(url, decode_responses=True, socket_connect_timeout=1)
    except Exception as exc:
        logger.warning("redis_cache: client creation failed: %s", exc)
        return None


async def cached_skill_lookup(redis: Any, key: str, loader: Callable[[], Any],
                              ttl: int = 300) -> tuple[Any, bool]:
    """Read-through lookup. (value, hit). Any redis error → direct loader + fallback."""
    if redis is None:
        COUNTERS["fallback"] += 1
        return await loader() if _is_awaitable(loader) else loader(), False
    try:
        raw = await redis.get(key)
        if raw is not None:
            COUNTERS["hits"] += 1
            return json.loads(raw), True
    except Exception as exc:
        logger.warning("redis_cache: GET failed for %s: %s", key, exc)
        COUNTERS["fallback"] += 1
        return await loader() if _is_awaitable(loader) else loader(), False
    try:
        value = await loader() if _is_awaitable(loader) else loader()
    except Exception:
        raise
    try:
        await redis.setex(key, ttl, json.dumps(value))
        COUNTERS["miss"] += 1
    except Exception as exc:
        logger.warning("redis_cache: SETEX failed for %s: %s", key, exc)
        COUNTERS["fallback"] += 1
    return value, False


def _is_awaitable(fn: Callable) -> bool:
    import inspect
    try:
        return inspect.iscoroutinefunction(fn)
    except Exception:
        return False
