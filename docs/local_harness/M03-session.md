# M3 — `server/local_harness/session.py` (CREATE, revertible-effect session)

## GitNexus queries
```
context({name: "ToolSessionBroker"})   # start/end_session, _active, _persist_session
context({name: "open_session"})        # execution_plane payload shape to mirror
context({name: "report_session"})      # outcome normalization + belief_delta
impact({target: "start_session", direction: "upstream"})
```
Used: `read tool_session_broker.py:1-102` (ToolSession dataclass, MAX_SESSION_TOOLS=8, schema ensure), `:102-221` (start/end/get), `read execution_plane.py:166-280` (open/report, budget clamp 1–12, skill_names prefer cutover order). Cordis §5.1.1 Alg1 + §5.1.3 Alg4/5.

## Current state
Broker owns `_active: dict[session_id, ToolSession]`, persists to SQLite, links `ide_manager`, archives context, records learning. No inverse tracking — `end_session` pops without disposing acquired handles.

## Blast radius: NONE as new file; BECOMES HIGH once M7 wires it into broker
Keep broker imports lazy (`from server.tool_session_broker import get_tool_session_broker` inside methods).

## Pseudocode
```python
class SessionState: INACTIVE, LOADING, ACTIVE, UNLOADING = ...

class LocalHarnessSession:
    def __init__(self, broker_session, realm="local:default"):
        self.inner = broker_session   # ToolSession (tools_loaded ≤8, harness_tools ≤16)
        self.ctx = NexusContext(realm=realm)
        self.state = INACTIVE
        self._armed = True            # at-most-once dispose
        self._committed: dict[key, uid] = {}   # ω committed view

    def effect(self, fn) -> None:
        # SOLE mutation path: run fn now, push its inverse (or no-op) LIFO
        inv = fn()
        self.ctx.accumulator.append(inv or (lambda: None))

    def require(self, *keys) -> None:
        for k in keys: self.ctx.deps.setdefault(k, CoeffectSpec(set(keys), {}))

    def commit_providers(self, resolve: Callable[[str], str]) -> dict:
        # resolve each required key -> fresh uid; snapshot ω; return it
        ...

    async def refresh(self, current: dict[str,bool]) -> str:
        # classify vs committed; LOADING->ACTIVE on activating; ACTIVE->UNLOADING on deactivating
        # inertial: finish in-flight transition first, chain on target mismatch
        ...

    def dispose(self) -> None:
        if not self._armed: return
        self._armed = False
        while self.ctx.accumulator:
            inv = self.ctx.accumulator.pop()   # LIFO
            try: inv()
            except Exception: log_and_continue  # never break the chain
        # parent compose: caller chains parent.dispose AFTER (∂² recursion)

def open_local_session(task, project="default", budget=8, ide="opencode") -> LocalHarnessSession:
    broker = get_tool_session_broker()          # lazy import
    s = broker.start_session(project=project, query=task, ide=ide, max_tools=min(max(1,budget),8))
    lh = LocalHarnessSession(s, realm=f"local:{s.session_id}")
    lh.require(*keys_for_cluster(s.cluster))    # e.g. general->{db}, coding->{db,faiss,git}
    lh.commit_providers(resolve=current_provider_uid)
    return lh
```

## Build steps
1. Implement states + effect/require/commit/refresh/dispose with zero `server.*` top-level imports.
2. Chaos test: 100 sessions, random mid-session exceptions → assert accumulator empty + `_active` popped after dispose.
3. Wire to broker only in M7 — do not import this module from broker yet.

## Tests
`test_lifo_dispose`, `test_armed_at_most_once`, `test_inertial_chain`, `test_chaos_no_leak`.
