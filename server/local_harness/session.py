"""LocalHarnessSession — revertible-effect session wrapper (Cordis §5.1.1 Alg1 + §5.1.3 Alg4/5).

Broker/execution_plane imports are LAZY (inside functions) to avoid cycles.
"""
from __future__ import annotations

from typing import Any, Callable

from server.local_harness.context import NexusContext

INACTIVE = "INACTIVE"
LOADING = "LOADING"
ACTIVE = "ACTIVE"
UNLOADING = "UNLOADING"

# Cluster-implied coeffect keys (spatial hint; enforced via M9 subscriptions).
CLUSTER_KEYS: dict[str, tuple[str, ...]] = {
    "general": ("db",),
    "coding": ("db", "faiss", "git"),
    "research": ("db", "faiss", "internet"),
}


def keys_for_cluster(cluster: str | None) -> tuple[str, ...]:
    return CLUSTER_KEYS.get((cluster or "general").lower(), ("db",))


# Wrapper registry: open_local_session registers, dispose_by_id pops + disposes.
# Process-local by design — a server restart between open and report degrades to
# the broker-only path (report still completes; wrapper inverses are skipped).
_WRAPPERS: dict[str, "LocalHarnessSession"] = {}


class LocalHarnessSession:
    """Cordis-style wrapper around a broker ToolSession."""

    def __init__(self, broker_session: Any, realm: str = "local:default",
                 ctx: NexusContext | None = None) -> None:
        self.inner = broker_session
        # Adopt a pre-registered ctx (open_local_session flow) or own a fresh one.
        self.ctx = ctx if ctx is not None else NexusContext(realm=realm)
        if ctx is not None:
            ctx.realm = realm
        self.state = INACTIVE
        self._committed: dict[str, str] = {}  # ω committed provider view
        self._resolve: Callable[[str], str] | None = None  # last commit resolver (M9 recommit)
        self.site_urls: dict[str, str] = {}   # P1.2: site:<id> key -> URL bound to this task
        self._site_live: dict[str, bool] = {}  # P1.2: session-owned site liveness
        # Armed guard lives on ctx (single source); dispose() delegates.

    def effect(self, fn: Callable[[], Any]) -> None:
        """SOLE mutation path: delegates to context.effect (one implementation)."""
        from server.local_harness.context import effect as _effect
        _effect(self.ctx, fn)

    def require(self, *keys: str) -> None:
        from server.local_harness.context import require as _require
        _require(self.ctx, *keys)

    def commit_providers(self, resolve: Callable[[str], str]) -> dict[str, str]:
        """Resolve each required key to a fresh provider uid; snapshot ω."""
        self._resolve = resolve
        for key, spec in self.ctx.deps.items():
            self._committed[key] = resolve(key)
            spec.satisfied = True
        return dict(self._committed)

    async def refresh(self, current: dict[str, bool] | None = None) -> str:
        """Recompute targets; inertial: finish in-flight transition first, chain on mismatch.

        Drives real activate/deactivate from provider liveness (M9 spatial half):
        activating → LOADING, recommit bindings, ACTIVE; deactivating → UNLOADING,
        clear bindings + satisfied, LIFO dispose of dependents, INACTIVE.
        ``ctx.dispose()`` is armed/at-most-once, so a flap (True→False→True→False)
        disposes exactly once and never crashes.
        """
        from server.local_harness.context import classify_transition
        if current is None:
            from server.local_harness.providers import liveness_snapshot
            current = liveness_snapshot(self.ctx.realm, sites=self._site_live)
        before = {k: (self._committed.get(k) is not None) for k in self.ctx.deps}
        events = classify_transition(before, {k: current.get(k, False) for k in self.ctx.deps})
        if self.state == INACTIVE and any(v == "activating" for v in events.values()):
            self.state = LOADING
            for k, ev in events.items():
                if ev == "activating":
                    resolve = self._resolve or (lambda key: f"{key}:{id(self)}")
                    self._committed[k] = resolve(k)
                    self.ctx.deps[k].satisfied = True
            self.state = ACTIVE
        elif self.state == ACTIVE and any(v == "deactivating" for v in events.values()):
            self.state = UNLOADING
            # provider gone → clear binding + run its inverse (LIFO, armed on ctx)
            for k, ev in events.items():
                if ev == "deactivating":
                    self.ctx.deps[k].satisfied = False
                    self._committed.pop(k, None)
            self.ctx.dispose()          # LIFO teardown of dependents (context.py:41)
            self.state = INACTIVE
        return self.state

    def dispose(self) -> None:
        """Drain via context (single implementation; armed/at-most-once on ctx)."""
        self.ctx.dispose()


def _task_site_urls(project: str, task_type: str) -> list[str]:
    """Sites bound to (project, task_type); [] on any registry problem (P1.1)."""
    try:
        from server.site_registry import get_task_sites
        return get_task_sites(project, task_type)
    except Exception:
        return []


def open_local_session(task: str, project: str = "default", budget: int = 8,
                       ide: str = "opencode", task_type: str = "general") -> LocalHarnessSession:
    """Route + start broker session, wrap in Cordis-style session.

    The ctx is created FIRST and passed into the broker so M7 registers the
    persisted-row inverse and cluster coeffects on the live path (not just tests).
    Sites bound to (project, task_type) become ``site:<id>`` coeffects (P1.2).
    """
    from server.local_harness.context import NexusContext
    from server.local_harness.providers import site_key
    from server.tool_session_broker import get_tool_session_broker
    broker = get_tool_session_broker()
    ctx = NexusContext(realm="local:pending")
    s = broker.start_session(project=project, query=task, ide=ide,
                             max_tools=min(max(1, budget), 8), ctx=ctx)
    lh = LocalHarnessSession(s, realm=f"local:{s.session_id}", ctx=ctx)
    lh.require(*keys_for_cluster(s.cluster))
    lh.site_urls = {site_key(u): u for u in _task_site_urls(project, task_type)}
    if lh.site_urls:
        lh.require(*lh.site_urls)
    lh._site_live = {k: True for k in lh.site_urls}  # P1.3 flips this per harvest result
    lh.commit_providers(resolve=lambda k: f"{k}:{s.session_id}")
    lh.state = ACTIVE  # providers committed at open → session is live; refresh() reacts from here
    _WRAPPERS[s.session_id] = lh
    return lh


def pop_wrapper(session_id: str) -> "LocalHarnessSession | None":
    """Remove + return the registered wrapper (None after restart — degrade gracefully)."""
    return _WRAPPERS.pop(session_id, None)


def dispose_by_id(session_id: str, outcome: str = "correct", notes: str = "",
                  quality: float = 1.0, skill_name: str = "") -> dict[str, Any]:
    """End session via execution_plane (belief_delta), then dispose wrapper LIFO.

    Report-first ordering: learning/archive are emissions (unrevertible by
    design); the broker inverse then no-ops on the completed row, as specified.
    """
    from server.execution_plane import report_session
    try:
        return report_session(session_id, outcome, notes=notes,
                              quality=quality,
                              skill_name=skill_name or None)
    finally:
        lh = pop_wrapper(session_id)
        if lh is not None:
            try:
                lh.dispose()
            except Exception:
                import logging
                logging.getLogger(__name__).exception(
                    "wrapper dispose skipped for %s", session_id)
