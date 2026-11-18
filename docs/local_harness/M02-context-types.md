# M2 — `server/local_harness/context.py` (CREATE, context paradigm types)

## GitNexus queries
```
context({name: "StateVariable"})   # 45 refs — the type being extended by coeffects
context({name: "StateGates"})      # check()/snapshot() callers
query({search_query: "precondition gating state variable"})
```
Used: `read server/precondition.py` (full 125 lines: `StateVariable` enum 11 values, `Precondition`, `TOOL_PRECONDITIONS` 7 tools, `StateGates.check/snapshot`, `GLOBAL_GATES`). Cordis §3.2–3.3 (Σ, notify trichotomy, Γ∞).

## Current state
`StateVariable` is a plain `Enum`; satisfaction = boolean `get()`; no subscribe/realm/notify. This file adds the coeffect layer *without* touching precondition.py.

## Blast radius: NONE (new file)
Downstream dependents after M7/M9: broker + gates. Keep this file dependency-free (stdlib only).

## Pseudocode
```python
from dataclasses import dataclass, field
from typing import Callable, Literal
Inverse = Callable[[], None]  # idempotent, at-most-once (armed in session.py)
Notify = Literal["activating", "deactivating", "neutral"]

@dataclass
class CoeffectSpec:
    keys: set[str]                      # e.g. {"db","faiss","embedder"}
    provider_uids: dict[str, str]       # key -> fresh uid, resolved at commit
    satisfied: bool = False

@dataclass
class NexusContext:
    state: dict = field(default_factory=dict)
    accumulator: list[Inverse] = field(default_factory=list)  # Cordis φ, LIFO
    deps: dict[str, CoeffectSpec] = field(default_factory=dict)
    realm: str = "local:default"
    intercept: dict[str, dict] = field(default_factory=dict)  # policy meta, context wins

def was_satisfied(before: bool, after: bool) -> Notify:
    # (False,True)->activating; (True,False)->deactivating; else neutral
    ...

def classify_transition(before: dict[str,bool], after: dict[str,bool]) -> dict[str, Notify]:
    # per-key trichotomy over union of keys
    ...
```

## Build steps
1. Write file with stdlib only; no `server.*` imports.
2. Unit: `(F,T)=activating`, `(T,F)=deactivating`, `(T,T)/(F,F)=neutral`; multi-key classify.
3. Document realm convention: `local:<session_id>` private, `global:<name>` shared (used by M15 breaker keys).

## Tests
`test_notify_trichotomy`, `test_classify_multi_key`, `test_context_defaults`.
