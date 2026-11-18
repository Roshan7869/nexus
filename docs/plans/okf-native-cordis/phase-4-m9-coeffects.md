# Phase 4 — M9 spatial coeffects (real provider reactivity)

Status: NOT STARTED
Depends on: Phase 3 (+ Phase 2 if FAISS is removed)

## Goal
Replace the two `# ... M9 wiring ...` stubs in `session.py:70,74` with actual provider-liveness
subscriptions so that when a coeffect provider (`db`, `faiss`, `git`, `internet`, `embedder`)
dies, dependents auto-deactivate via the LIFO accumulator. This makes Cordis' **spatial**
discipline real — today only the **temporal** half (dispose/effect) runs on the live path.
`classify_transition` (`context.py:63`) is already built and unit-clean; this phase feeds it
live signal.

## Files to touch
| file:line | current | change |
|-----------|---------|--------|
| `server/local_harness/session.py:63` `refresh` | flips state, stubbed handshake | drive real activate/deactivate from provider liveness |
| `server/local_harness/session.py:70` | `# ... provider handshake (M9) ...` | LOADING→ACTIVE only after providers commit |
| `server/local_harness/session.py:74` | `# ... dependents teardown (M9) ...` | run dependent inverses via ctx accumulator |
| `server/local_harness/session.py:17` `CLUSTER_KEYS` | `faiss` in coding/research | drop `faiss` iff Phase 2 adopted arm C |
| `server/local_harness/context.py` | `CoeffectSpec.satisfied` unused | set on commit / clear on provider loss |
| (new) `server/local_harness/providers.py` | — | `liveness_snapshot() -> dict[str,bool]` |

## Diff sketch — providers.py (new, stdlib + lazy server imports)
```python
def liveness_snapshot(realm: str) -> dict[str, bool]:
    """Best-effort {coeffect_key: alive}. Never raises."""
    live = {}
    # db: can we open + PRAGMA?
    try:
        from server.db import get_connection
        c = get_connection(); c.execute("SELECT 1"); c.close(); live["db"] = True
    except Exception: live["db"] = False
    # faiss: only if still on hot path (Phase 2 may have removed it)
    try:
        from server.cluster_faiss import index_ready
        live["faiss"] = bool(index_ready())
    except Exception: live["faiss"] = False
    live["git"] = _git_ok()
    live["internet"] = _net_ok(timeout=0.3)
    return live
```

## Diff sketch — session.py refresh (session.py:63)
```python
# BEFORE (stubbed)
async def refresh(self, current: dict[str, bool]) -> str:
    before = {k: (self._committed.get(k) is not None) for k in self.ctx.deps}
    events = classify_transition(before, {k: current.get(k, False) for k in self.ctx.deps})
    if self.state == INACTIVE and any(v == "activating" for v in events.values()):
        self.state = LOADING
        # ... provider handshake happens here (M9 wiring) ...
        self.state = ACTIVE
    elif self.state == ACTIVE and any(v == "deactivating" for v in events.values()):
        self.state = UNLOADING
        # ... dependents teardown while bindings readable (M9 wiring) ...
        self.state = INACTIVE
    return self.state

# AFTER (live)
async def refresh(self, current: dict[str, bool] | None = None) -> str:
    from server.local_harness.providers import liveness_snapshot
    current = current if current is not None else liveness_snapshot(self.ctx.realm)
    before = {k: (self._committed.get(k) is not None) for k in self.ctx.deps}
    events = classify_transition(before, {k: current.get(k, False) for k in self.ctx.deps})

    if self.state == INACTIVE and any(v == "activating" for v in events.values()):
        self.state = LOADING
        for k, ev in events.items():
            if ev == "activating":
                self._committed[k] = self._committed.get(k) or f"{k}:{id(self)}"
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
```
> `ctx.dispose()` is armed/at-most-once (`context.py:43`) — a second deactivation no-ops,
> preserving Cordis' idempotent teardown contract.

## Diff sketch — CLUSTER_KEYS (session.py:17), only if Phase 2 adopted arm C
```python
# BEFORE
CLUSTER_KEYS = {"general": ("db",),
                "coding": ("db","faiss","git"),
                "research": ("db","faiss","internet")}
# AFTER (embedder/FAISS off hot path)
CLUSTER_KEYS = {"general": ("db",),
                "coding": ("db","git"),
                "research": ("db","internet")}
```

## Pseudocode — reactive loop (illustrative; driver TBD: poll or event)
```
on tick / on provider-event:
    snap = liveness_snapshot(realm)
    await session.refresh(snap)
    # activating: bind provider, mark satisfied
    # deactivating: clear binding, dispose() dependents LIFO, INACTIVE
```

## What must work (acceptance)
- [x] Unit: fake provider goes False → `refresh()` transitions ACTIVE→INACTIVE and runs the
      registered inverse exactly once (assert accumulator drained, `_armed=False`).
- [x] Unit: provider flaps True→False→True → no double-dispose, no crash.
- [x] `keys_for_cluster("coding")` no longer lists `faiss` when arm C adopted.
      → N/A under verdict A (faiss KEPT; existing test still asserts it present.)
- [x] Live: killing DB connectivity mid-session deactivates the session cleanly (no leaked
      bindings; `report`/`dispose_by_id` still completes per session.py:108).

## Verify commands
```
python -m pytest tests/ -k "local_harness and (refresh or coeffect or m9)" -q
```

## Impact gate (before editing)
```
impact({target:"refresh", direction:"upstream", file_path:"server/local_harness/session.py"})
impact({target:"open_local_session", direction:"upstream"})
```

## Risk & rollback
- Risk: MEDIUM. Teardown paths must stay idempotent; mitigated by ctx armed-guard + tests.
- Rollback: restore stubbed `refresh`; providers.py is additive (delete import).
