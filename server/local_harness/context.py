"""NexusContext + CoeffectSpec — the single mediated context type (Cordis §3.2–3.3).

Stdlib only. No server.* imports.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Literal

logger = logging.getLogger(__name__)

Inverse = Callable[[], None]  # idempotent, at-most-once (armed in session.py)
Notify = Literal["activating", "deactivating", "neutral"]


@dataclass
class CoeffectSpec:
    keys: set[str]                      # e.g. {"db", "faiss", "embedder"}
    provider_uids: dict[str, str]       # key -> fresh uid, resolved at commit
    satisfied: bool = False


@dataclass
class NexusContext:
    state: dict = field(default_factory=dict)
    accumulator: list[Inverse] = field(default_factory=list)  # Cordis φ, LIFO
    deps: dict[str, CoeffectSpec] = field(default_factory=dict)
    realm: str = "local:default"
    intercept: dict[str, dict] = field(default_factory=dict)  # policy meta, context wins
    _armed: bool = True  # at-most-once dispose guard (single source; session mirrors it)

    def effect(self, fn: Callable[[], object]) -> None:
        """Method form of the module-level effect() (M07 broker call-site)."""
        effect(self, fn)

    def require(self, *keys: str) -> None:
        """Declare coeffect keys (method form; see require())."""
        require(self, *keys)

    def dispose(self) -> None:
        """Run accumulator LIFO, armed/at-most-once. Never raises (per-inverse guard)."""
        if not self._armed:
            return
        self._armed = False
        while self.accumulator:
            inv = self.accumulator.pop()
            try:
                inv()
            except Exception:
                logger.exception("local-harness disposer failed; continuing chain")


def was_satisfied(before: bool, after: bool) -> Notify:
    """Single-key notify trichotomy: (F,T)->activating; (T,F)->deactivating; else neutral."""
    if not before and after:
        return "activating"
    if before and not after:
        return "deactivating"
    return "neutral"


def classify_transition(before: dict[str, bool], after: dict[str, bool]) -> dict[str, Notify]:
    """Per-key trichotomy over the union of keys."""
    return {k: was_satisfied(before.get(k, False), after.get(k, False))
            for k in (set(before) | set(after))}


def effect(ctx: "NexusContext", fn: Callable[[], object]) -> None:
    """SOLE mutation path on a context: run fn now, hold its inverse LIFO.

    Method-equivalent kept as a function so both NexusContext holders
    (LocalHarnessSession.effect, broker ctx hooks) share one implementation.
    """
    inv = fn()
    ctx.accumulator.append(inv if callable(inv) else (lambda: None))


def require(ctx: "NexusContext", *keys: str) -> None:
    """Declare coeffect keys on a context (shared by session + broker)."""
    for k in keys:
        ctx.deps.setdefault(k, CoeffectSpec(keys=set(keys), provider_uids={}))
