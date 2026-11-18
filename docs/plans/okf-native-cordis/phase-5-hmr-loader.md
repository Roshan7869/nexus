# Phase 5 — HMR loader live-apply

Status: DONE (2026-09-25) — acceptance passed; see "As built" at the bottom.
Depends on: — (independent of Phases 1–2; ran in parallel)

## Goal
Wire `reconcile()`'s injected `apply` callback to the real resource surface + DB so
`nexus_local_harness_reconcile` flips from preview-only (`applied=false`) to live add /
remove / reload, recording each run in `local_harness_reconciles` (migration 020, already
exists). The keyed-diff engine (`loader.py:49`) is done and transactional; this phase supplies
the side-effecting callback it was designed to receive.

## Files to touch
| file:line | current | change |
|-----------|---------|--------|
| `server/local_harness/loader.py:49` `reconcile` | pure diff + injected apply | unchanged (already correct) |
| (new) `server/local_harness/apply_db.py` | — | `make_apply(conn, realm)` → the callback |
| MCP `nexus_local_harness_reconcile` handler | returns `applied=false` | call `reconcile(old,new,make_apply(...))`, `applied=true` |
| `local_harness_reconciles` (migration 020) | table exists | insert one row per reconcile |

## Contract recap (loader.py:49-60 — do NOT change these semantics)
```
id/kind changed   -> rebuild   (dispose old fiber + fresh insert)
isolate changed   -> reassign  (realm reassign + targeted notify)
intercept changed -> intercept (in-place, read-time merge, no reload)
config changed    -> reload    (only on material diff)
disabled toggled  -> reload    (unload True / fresh load False; NEVER un-retire in place)
on error          -> reverse applied ops LIFO, rolled_back=True, re-raise
```

## Diff sketch — apply_db.py (new)
```python
def make_apply(conn, realm: str):
    """Return an apply(action, old, new) callback that mutates the resource surface.

    `action` ∈ {"add","remove","reload","rebuild","reassign","intercept",
                "rollback:<action>"}  (loader passes rollback:* with args swapped).
    Every branch must be reversible enough that loader's LIFO reverse works.
    """
    def apply(action, old, new):
        if action == "add":
            conn.execute("UPDATE resources SET status='active' WHERE name=?", (new.id,))
        elif action == "remove":
            conn.execute("UPDATE resources SET status='archived' WHERE name=?", (old.id,))
        elif action in ("reload", "rebuild", "reassign"):
            # re-materialize sections / re-link cluster for new.config
            _rematerialize(conn, new)
        elif action == "intercept":
            _merge_intercept(conn, new.id, new.intercept)   # read-time policy meta
        elif action.startswith("rollback:"):
            # loader already swapped (n,o); re-dispatch the base action
            return apply(action.split(":",1)[1], old, new)
        # NOTE: no conn.commit() here — caller owns the txn (atomic reconcile)
    return apply
```

## Diff sketch — MCP handler
```python
# BEFORE  (preview only)
def nexus_local_harness_reconcile(desired_json, project="default"):
    old, new = _current_tree(project), _parse(desired_json)
    report = reconcile(old, new, apply=lambda *a: None)   # no-op
    return {"added": report.added, "removed": report.removed,
            "changed": report.reloaded, "applied": False}

# AFTER  (live, transactional)
def nexus_local_harness_reconcile(desired_json, project="default"):
    from server.local_harness.apply_db import make_apply
    from server.db import get_connection
    old, new = _current_tree(project), _parse(desired_json)
    conn = get_connection()
    try:
        report = reconcile(old, new, apply=make_apply(conn, realm=f"local:{project}"))
        conn.commit()
        applied = not report.rolled_back
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()
    _record_reconcile(project, report)     # INSERT into local_harness_reconciles
    return {"added": report.added, "removed": report.removed,
            "changed": report.reloaded, "rebuilt": report.rebuilt,
            "rolled_back": report.rolled_back, "applied": applied}
```

## Pseudocode — record row (migration 020 table)
```
_record_reconcile(project, report):
    INSERT INTO local_harness_reconciles(project, added, removed, changed, report_json)
    VALUES(project, len(report.added), len(report.removed),
           len(report.reloaded)+len(report.rebuilt), json(report))
```

## What must work (acceptance)
- [x] `nexus_local_harness_reconcile` returns `applied: true` and mutates `resources.status`.
- [x] A failing apply mid-batch triggers loader LIFO reverse → `rolled_back: true` and DB
      unchanged (assert row counts equal pre-state).
- [x] Disabled toggle archives (never hard-deletes / never un-retires in place).
- [x] One `local_harness_reconciles` row written per call with correct counts.

## As built (deltas vs sketches, 2026-09-25)
- **Rollback = preimage restore, not base re-dispatch.** The sketch's
  `apply("rollback:<base>", old, new)` misreads ids for add/remove (loader swaps
  `(old,new)` at `loader.py:102`, so `rollback:remove` arrives as `(old=None,
  new=E)` → `old.id` would blow up, and re-running "remove" archives again instead
  of un-archiving). `make_apply` snapshots (status, cluster, resource_type,
  intercept, row-exists?) before every forward op and restores it on rollback —
  strictly inverse-consistent, and the txn rollback in the handler is belt-and-braces.
- **Intercept** → process-local registry (`apply_db.get_intercept`) + snapshot in
  the audit row, NOT `resource_policies.preconditions_json` (that column is
  list-typed and read by `broker_runtime.py:352`; merging a dict would corrupt
  policy reads). Matches the "in-place, read-time merge" contract.
- **`reassign`** rematerializes config (no realm column exists on resources —
  realm is assigned per-session at open).
- **`add` with `disabled:true`** is a no-op (declared-but-retired never activates);
  `add` of a missing row INSERTs (kind CHECK-mapped, `file_path='unknown'`).
- **`apply: bool = True` parameter added** (dry-run valve) + docstring contract
  warning: desired_json is the complete desired active set — a partial tree
  archives every omitted active resource (observed live on a DB copy).
- **Audit on failure**: counts come from the pre-diff; `report_json` carries
  `rolled_back:true` + error string. Written after `conn.rollback()`, then committed.
- Fix bundled: Phase 3 `_adapter_okf_sections` now passes `db_path=get_db_path()`
  (env override honored; fixes the one `tests/test_context_assembly.py` failure).

## Verify commands
```
python -m pytest tests/ -k "reconcile or loader or hmr" -q
# integration: call the MCP tool with a 1-add/1-remove desired tree, assert applied=true
```

## Impact gate (before editing)
```
impact({target:"reconcile", direction:"upstream", file_path:"server/local_harness/loader.py"})
```

## Risk & rollback
- Risk: MEDIUM (mutates `resources`). Mitigated: single txn, loader's transactional reverse,
  archive-not-delete, per-run audit row.
- Rollback: restore preview `apply=lambda *a: None`; `apply_db.py` is additive.
