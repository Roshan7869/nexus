# M9 — `server/precondition.py` (MODIFY, reactive coeffects)

## GitNexus queries
```
context({name: "StateGates"})     # check()/snapshot()/GLOBAL_GATES/can_invoke/enforce_state
context({name: "StateVariable"})  # 11-member enum, 45 refs
impact({target: "StateGates.check", direction: "upstream"})  # router + tool gates
query({search_query: "precondition gating tool invocation"})
```
Used: full `read precondition.py` (125 lines). `check(tool)` is pure boolean over `TOOL_PRECONDITIONS` (7 tools); `GLOBAL_GATES` module singleton; config override from `~/nexus/config/default.json` (missing → silent defaults).

## Current state
Boolean gates only — no subscribe/realm/notify; callers poll `check()` at route time. Cordis spatial (Thm70) needs push: dependents deactivate *before* provider withdraws, bindings stable during activation.

## Blast radius: HIGH — router v4/v5 + tool gates depend on `check()` semantics
Rules: keep `check()` signature + return contract identical; add additive `subscribe`/`realm` fields + `notify()` emitter. `GLOBAL_GATES` behavior unchanged when no subscribers.

## Pseudocode
```python
@dataclass
class StateVariable: ...  # Enum UNCHANGED (11 members)

@dataclass
class CoeffectSubscription:   # NEW, parallel to Precondition (not replacing it)
    key: str                  # "db" | "faiss" | "embedder" | ...
    realm: str = "global:default"
    on_change: Callable[[Notify], None] | None = None

class StateGates:
    def __init__(self, config_path=None):
        ...existing defaults...
        self._subs: dict[str, list[CoeffectSubscription]] = {}   # NEW

    def check(self, tool_name): ...identical...                   # UNCHANGED

    # NEW (additive):
    def subscribe(self, key: str, realm="global:default", on_change=None) -> None: ...
    def set_and_notify(self, var: StateVariable, value: bool) -> dict[str, Notify]:
        before = self.snapshot()
        self.set(var, value)
        after = self.snapshot()
        events = classify_transition(before, after)   # from M2
        for key, notify in events.items():
            for sub in self._subs.get(key, []) + self._subs.get("*", []):
                if sub.realm in ("global:default", current_realm, "*"):
                    guarded_call(sub.on_change, notify)  # never raises into gate
        return events
```

## Build steps
1. Add `_subs` + two methods; zero changes to existing methods.
2. Kill-FAISS drill: `set_and_notify(HAS_MCP, False)` → subscribers get `deactivating` while `check()` still returns cached view during teardown (read-view-not-store).
3. Run `impact({target:"StateGates"})` + router tests before commit.

## Tests
`test_check_unchanged`, `test_subscribe_notify_trichotomy`, `test_subscriber_error_isolated`.
