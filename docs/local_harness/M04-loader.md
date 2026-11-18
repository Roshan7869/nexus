# M4 — `server/local_harness/loader.py` (CREATE, declarative reconcile + HMR-lite)

## GitNexus queries
```
query({search_query: "skill resource load rebuild faiss sync"})
context({name: "bundle_for_cluster"})   # service_layer bundle resolution
context({name: "get_connection"})       # db.py connection discipline
```
Used: `read harness_mcp_bridge.py:1-60` (tool merge pattern), `scripts/rebuild_faiss.py` (implied by M-plan), Cordis §5.2.1 (entries {id,url,isolate,intercept,config,disabled}, keyed diff, per-field policy) + Thm80/73 (quiescence = f(final config)).

## Current state
No declarative loader. Skill ingestion = SQL migrations + `rebuild_faiss.py` + MCP sync path, all restart-sensitive. No keyed reconciliation.

## Blast radius: NONE (new file, no callers until M5 registers `local_harness_reconcile`)

## Pseudocode
```python
@dataclass
class Entry:
    id: str                 # reconciliation key (stable)
    kind: str               # "skill" | "bundle" | "group" | "include"
    config: dict = field(default_factory=dict)
    disabled: bool = False  # τ retirement flag
    isolate: str = "local"  # realm scope
    intercept: dict = field(default_factory=dict)

@dataclass
class EntryTree: entries: dict[str, Entry]   # id -> entry

def diff_trees(old: EntryTree, new: EntryTree) -> tuple[added, removed, changed]:
    ...

def reconcile(old: EntryTree, new: EntryTree, apply) -> ReconcileReport:
    # per-field least-disruptive:
    #   id/kind changed      -> rebuild (dispose old fiber + fresh insert)
    #   isolate changed      -> realm reassign + targeted notify (only affected sessions)
    #   intercept changed    -> in-place (read-time merge, no reload)
    #   config changed       -> hand to component; reload only on material diff
    #   disabled toggled     -> unload (True) / fresh load (False); NEVER un-retire in place
    # transactional: snapshot backup -> apply -> on error restore + rethrow (never half-applied)
    ...
```

## Build steps
1. Implement diff + per-field policy with injected `apply` callbacks (no direct DB writes — callbacks provided by caller in M5/M7 phase).
2. Test: insert→callback fired, delete→inverse fired, disabled-toggle→fresh (identity `id` survives, fiber fresh), error→backup restored.
3. Performance target: single-entry reconcile <2s to routable (FAISS rebuild stays out-of-scope; loader marks stale until rebuild).

## Tests
`test_keyed_diff`, `test_disabled_fresh_not_unretire`, `test_intercept_in_place`, `test_rollback_on_error`.
