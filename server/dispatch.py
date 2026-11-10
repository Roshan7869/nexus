#!/usr/bin/env python3
"""
NEXUS Dispatcher — the interception primitive.
==============================================

Five dispatch modes, from the pattern catalogue in `_research/dsh.md` §4
(the Cordis "every interception point uses it" surface):

    emit       fire-and-forget; no return value
    parallel   all listeners run concurrently; results collected
    serial     listeners run in declared order; results collected, no chaining
    bail       stop at the first listener returning a non-None value
    waterfall  chained — each listener receives (value, next); a listener that
               does NOT call next() vetoes the chain, and its return value
               (if any) becomes the result

The load-bearing property, and the reason this module is written to be boring:

    **zero listeners == pass-through, by construction.**

Calling `waterfall(name, x)` with nothing registered returns `x` itself — the
same object, not a copy, not a rebuilt list. Registering no listener is
therefore byte-identical to not having the seam at all, which is what makes it
safe to place a hook point into a live 1.2-2.1 s routing path (`04-FEASIBILITY.md`
§3) before anything is plugged into it.

Latency note (§3 of the feasibility doc): a **serial** listener chain is additive
on a 2 s path. `serial` and `waterfall` are therefore explicit opt-ins — nothing
here is serial by default, and `waterfall` short-circuits the moment a listener
vetoes.

Disposal is idempotent: every `register` returns an unregister callable, so a
listener's teardown can be recorded next to its registration (the Cordis
"every resource records a cleanup function" constraint).
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "register", "unregister", "listeners", "count", "clear",
    "emit", "parallel", "serial", "bail", "waterfall", "modes",
    "POST_AUTOCUT",
]

# ── The interception points ─────────────────────────────────────────────
# Named channels. Values are stable strings so telemetry and the plan graph can
# key off them.
POST_AUTOCUT = "router.post_autocut"   # v5.py, after _autocut() returns (A2-2)

_lock = threading.RLock()
_registry: Dict[str, List[Tuple[str, Callable]]] = {}


# ── Registry ────────────────────────────────────────────────────────────

def register(channel: str, fn: Callable, label: Optional[str] = None) -> Callable[[], None]:
    """Register `fn` on `channel`. Returns an idempotent unregister callable."""
    entry = (label or getattr(fn, "__name__", "anon"), fn)
    with _lock:
        _registry.setdefault(channel, []).append(entry)

    def _unregister() -> None:
        with _lock:
            bucket = _registry.get(channel)
            if bucket and entry in bucket:
                bucket.remove(entry)

    return _unregister


def unregister(channel: str, fn: Callable) -> bool:
    """Remove every registration of `fn` on `channel`. Returns True if any went."""
    with _lock:
        bucket = _registry.get(channel)
        if not bucket:
            return False
        before = len(bucket)
        _registry[channel] = [e for e in bucket if e[1] is not fn]
        return len(_registry[channel]) != before


def listeners(channel: str) -> List[Tuple[str, Callable]]:
    """Snapshot of `(label, fn)` pairs for `channel`, in declaration order."""
    with _lock:
        return list(_registry.get(channel, ()))


def count(channel: str) -> int:
    with _lock:
        return len(_registry.get(channel, ()))


def clear(channel: Optional[str] = None) -> None:
    """Drop listeners for one channel, or all channels when None."""
    with _lock:
        if channel is None:
            _registry.clear()
        else:
            _registry.pop(channel, None)


def modes() -> Tuple[str, ...]:
    return ("emit", "parallel", "serial", "bail", "waterfall")


# ── Dispatch modes ──────────────────────────────────────────────────────

def emit(channel: str, *args: Any, **kwargs: Any) -> None:
    """Fire-and-forget. Listener exceptions are logged, never raised."""
    for label, fn in listeners(channel):
        try:
            fn(*args, **kwargs)
        except Exception:
            logger.exception("emit listener %r on %r failed", label, channel)


def parallel(channel: str, *args: Any, **kwargs: Any) -> List[Any]:
    """Run all listeners concurrently; return results in declaration order."""
    entries = listeners(channel)
    if not entries:
        return []
    with ThreadPoolExecutor(max_workers=min(len(entries), 8)) as pool:
        futures = [pool.submit(_safe, label, fn, channel, args, kwargs) for label, fn in entries]
        return [f.result() for f in futures]


def serial(channel: str, *args: Any, **kwargs: Any) -> List[Any]:
    """Run listeners in order as independent calls. No chaining, no veto.

    Additive by construction — a serial chain on a 2 s path costs 2 s + sum of
    listener times. Use only when ordering is genuinely required.
    """
    out = []
    for label, fn in listeners(channel):
        out.append(_safe(label, fn, channel, args, kwargs))
    return out


def bail(channel: str, *args: Any, **kwargs: Any) -> Any:
    """Return the first non-None listener result; stop there."""
    for label, fn in listeners(channel):
        value = _safe(label, fn, channel, args, kwargs)
        if value is not None:
            return value
    return None


def waterfall(channel: str, value: Any = None, *args: Any, **kwargs: Any) -> Any:
    """Chained dispatch. A listener that skips `next()` vetoes the chain.

    Each listener is called as `fn(value, next_, *args, **kwargs)`.

      * Calls `next_(new_value)` -> chain continues with `new_value`
        (or with the current `value` when `new_value` is None/omitted).
      * Returns without calling `next()` -> **veto**: the chain stops, and the
        listener's return value becomes the result. A listener that returns
        None while vetoing leaves `value` unchanged.

    With no listeners registered this returns `value` unchanged — the same
    object — so an unregistered dispatcher is a pass-through.
    """
    entries = listeners(channel)
    if not entries:
        return value                       # pass-through: same object, no work

    def step(i: int, current: Any) -> Any:
        if i >= len(entries):
            return current
        label, fn = entries[i]
        state = {"called": False}

        def next_(new_value: Any = None) -> Any:
            state["called"] = True
            return step(i + 1, current if new_value is None else new_value)

        try:
            result = fn(current, next_, *args, **kwargs)
        except Exception:
            logger.exception("waterfall listener %r on %r failed", label, channel)
            # A broken listener must not corrupt the chain: continue untransformed.
            return step(i + 1, current) if not state["called"] else current

        if state["called"]:
            return result
        # Vetoed: the listener declined to continue.
        return current if result is None else result

    return step(0, value)


# ── internals ───────────────────────────────────────────────────────────

def _safe(label: str, fn: Callable, channel: str, args: tuple, kwargs: dict) -> Any:
    try:
        return fn(*args, **kwargs)
    except Exception:
        logger.exception("listener %r on %r failed", label, channel)
        return None


# ── Module identity ─────────────────────────────────────────────────────
# This file is reachable under two names: bare `dispatch` (production — every
# module in server/ puts its own directory on sys.path and imports siblings
# bare, e.g. `from router_v2.fts5 import ...`) and `server.dispatch` (the
# package form, used by tests with the repo root on sys.path).
#
# Python treats those as two distinct modules with two distinct `_registry`
# dicts. A listener registered through one is invisible to the other, so the
# interception silently does nothing at exactly the moment it is supposed to
# fire. Aliasing both keys to this one module object makes the registry
# canonical however it is reached.
import sys as _sys

for _alias in ("dispatch", "server.dispatch"):
    _sys.modules.setdefault(_alias, _sys.modules[__name__])
del _alias, _sys
