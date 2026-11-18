# Phase 3 — Wire OKF sections into the local-harness context block

Status: NOT STARTED
Depends on: Phase 1

## Goal
Fill the reserved OKF-sections adapter slot in `context_assembly.py` so a local-harness
session's prepended context block actually contains retrieved OKF sections. Today the slot is
documented but empty (`context_assembly.py:5`), and `open_local_session` inherits only whatever
the broker prepends.

## Files to touch
| file:line | current | change |
|-----------|---------|--------|
| `server/context_assembly.py:72` `_ADAPTER_NAMES` | 3 adapters (repo_map, memory, notes) | add `_adapter_okf_sections` |
| `server/context_assembly.py` (new fn) | — | adapter calling `retrieve_chunks(corpus="sections")` |
| `server/local_harness/session.py:83` `open_local_session` | broker start only | ensure broker prepends `assemble(...)` block (verify path) |

## Diff sketch — context_assembly.py

### New adapter (mirrors _adapter_notes at :54; obeys the tuple[str,str] contract)
```python
def _adapter_okf_sections(query: str, project: str, git_root: str | None) -> tuple[str, str]:
    try:
        from server.router_v2.chunk_retrieval import retrieve_chunks
        # corpus/vec follow the Phase-2 verdict via retrieve_chunks defaults
        rc = retrieve_chunks(query, top_k=5, corpus="sections")
        rows = rc.get("results", [])
        if not rows:
            return "", "okf_sections(empty)"
        body = "\n".join(
            f"- **{r['resource_name']}** · {r['heading'] or ''}: {r['snippet'][:160]}"
            for r in rows if r.get("resource_name")
        )
        return (f"## Relevant OKF sections\n{body}", "okf_sections") if body \
               else ("", "okf_sections(empty)")
    except Exception:
        return "", "okf_sections(error)"
```

### Register it (context_assembly.py:72)
```python
# BEFORE
_ADAPTER_NAMES = ("_adapter_repo_map", "_adapter_project_memory", "_adapter_notes")
# AFTER
_ADAPTER_NAMES = ("_adapter_okf_sections", "_adapter_repo_map",
                  "_adapter_project_memory", "_adapter_notes")
```
> Placed first so OKF context leads the block. The existing `budget_ms` loop (:81-93)
> already skips adapters that overrun — no new time-budget logic needed.

## Pseudocode — end-to-end path
```
open_local_session(task):
    ctx = NexusContext(realm="local:pending")
    s   = broker.start_session(project, query=task, ide, ctx=ctx)
        └─ broker PREPENDS context_assembly.assemble(task, project)   # <-- OKF now inside
               assemble runs adapters in order under budget_ms:
                 _adapter_okf_sections  → retrieve_chunks(corpus="sections")  # NEW
                 _adapter_repo_map / _project_memory / _notes                 # existing
    wrap s in LocalHarnessSession; require(cluster keys); commit providers
    return lh
```

## What must work (acceptance)
- [x] `assemble("fix a login bug", project)["sources"]` includes `okf_sections` (or a
      documented `okf_sections(empty)` when no hits).
- [x] The assembled `block` contains a `## Relevant OKF sections` section with resource names.
- [x] Adapter respects `budget_ms` (never blows the 800ms default — verify with a slow-DB test).
      (budget=0 → `(budget)`; default: adapter ~40-70ms, total 265ms.)
- [x] `nexus_local_harness_open` payload context reflects OKF sections (integration check).
      (live MCP session 671d0d32398e4db7, outcome=correct.)

## Verify commands
```
python -c "from server.context_assembly import assemble; \
           r=assemble('fix a login bug','default'); print(r['sources']); print(r['block'][:400])"
```

## Impact gate (before editing)
```
impact({target:"assemble", direction:"upstream"})            # who prepends this block
impact({target:"open_local_session", direction:"upstream"})  # MCP nexus_local_harness_open
```

## Risk & rollback
- Risk: LOW–MEDIUM. Adapter is additive and time-boxed; failure returns empty string, never
  raises into the block.
- Rollback: remove `_adapter_okf_sections` from `_ADAPTER_NAMES`.
