# `server/local_harness/apply_db.py` — HMR live-apply callback (NEW)

| Field | Value |
|-------|-------|
| Layer / Phase | L0 / okf-native-cordis **Phase 5** (HMR loader live-apply) |
| Status | DONE (implemented 2026-09-25, okf-native-cordis Phase 5) |
| New or edit | **NEW** (additive; delete-to-rollback) |
| Depends on | none (parallel to Phases 1–2); consumes existing `loader.py:49 reconcile` |
| First-principle atoms | **A1_registry**, **A6_serve** |
| Gaps served | **G1** (registry integrity), **G2** (schema/data contract) |

## Role — what this file is
The **side-effecting callback factory** the HMR loader was designed to receive but never had.
`loader.py:49 reconcile(old, new, apply)` already computes a transactional keyed diff and calls
`apply(action, old, new)` for each op, reversing applied ops LIFO on failure. `make_apply(conn, realm)`
returns the `apply` function that turns those diff actions into **real mutations of the resource
surface** (`resources.status`, section re-materialization, cluster re-link, intercept meta).

It is the piece that flips `nexus_local_harness_reconcile` from preview-only (`applied=false`)
to live add / remove / reload — while preserving Cordis' atomic, LIFO-reversible contract.

## Issue it handles
`nexus_local_harness_reconcile` currently calls `reconcile(old, new, apply=lambda *a: None)` —
a no-op — so it can only *preview* (`applied=false`). The registry is never actually mutated by
the harness. `apply_db.py` supplies the callback so reconciles take effect, are transactional,
and are audited into `local_harness_reconciles` (migration 020, already exists).

## Research grounding
- **`arXiv:2506.16051` (Data-centric infrastructure for reproducible ML)** — version
  Dataset/Execution/Asset; treat registry state as versioned data. We take: every reconcile is
  a recorded, attributable change (audit row), not an opaque in-place edit.
- **`arXiv:2509.00293` (SmartDiff, large-scale data difference analysis)** — migration/diff
  **validation gates**. We take: the diff is computed first (loader), applied under a gate, and
  a failing op rolls the whole batch back — a diff gate, not fire-and-forget.
- **`arXiv:2512.09762` (Operation-based data versioning)** — model changes as reversible
  *operations*. We take: each `action` (`add/remove/reload/rebuild/reassign/intercept`) is an
  op with a defined inverse, enabling loader's `rollback:<action>` LIFO reverse.
- Cordis atomic recomposition / LIFO reverse (`C_ATOMIC`, `C_REVERSE`) — the transactional +
  reverse-on-error semantics this callback must honor.

## Interface / contract
```python
def make_apply(conn, realm: str):
    """Return apply(action, old, new) that mutates the resource surface.
    action ∈ {add, remove, reload, rebuild, reassign, intercept, rollback:<action>}.
    MUST NOT commit — the caller owns the transaction (atomic reconcile)."""
```
Invariants (do **not** violate — they are what make LIFO reverse correct):
- **No `conn.commit()` inside `apply`.** The MCP handler owns one txn around the whole batch.
- **Archive, never hard-delete.** `remove` sets `status='archived'`; disabled toggle likewise.
- **Never un-retire in place** (mirrors loader's disabled-toggle contract).
- **`rollback:<action>`** re-dispatches the base action with loader-swapped `(old,new)` — so
  every branch must be inverse-consistent.

## Guided plan (diff sketch from Phase 5 doc)
```python
def make_apply(conn, realm: str):
    def apply(action, old, new):
        if action == "add":
            conn.execute("UPDATE resources SET status='active'   WHERE name=?", (new.id,))
        elif action == "remove":
            conn.execute("UPDATE resources SET status='archived' WHERE name=?", (old.id,))
        elif action in ("reload", "rebuild", "reassign"):
            _rematerialize(conn, new)             # re-link sections / cluster for new.config
        elif action == "intercept":
            _merge_intercept(conn, new.id, new.intercept)   # read-time policy meta
        elif action.startswith("rollback:"):
            return apply(action.split(":", 1)[1], old, new) # loader already swapped args
        # NOTE: no commit — caller owns the txn
    return apply
```
Handler edit (in `nexus_local_harness_reconcile`, not this file): open conn → `reconcile(old,
new, make_apply(conn, realm))` → `commit()` on success / `rollback()` + re-raise on error →
`applied = not report.rolled_back` → write one `local_harness_reconciles` row.

## Acceptance (subset of Phase 5)
- [x] Reconcile returns `applied:true` and mutates `resources.status`
      (`test_hmr_handler_live_applied` + live run on a real-DB copy).
- [x] A failing apply mid-batch → loader LIFO reverse → `rolled_back:true` **and DB unchanged**
      (assert row counts equal pre-state) — `test_hmr_mid_batch_failure_lifo_reverse`,
      `test_hmr_handler_failure_rolls_back_and_records`.
- [x] Disabled toggle archives (never hard-deletes, never un-retires in place)
      — `test_hmr_disabled_toggle_archives_not_deletes`.
- [x] Exactly one `local_harness_reconciles` row per call with correct counts
      — `test_hmr_audit_row_per_call` (success) + failure-path audit test.

## As built (deltas vs sketch, 2026-09-25)
- **Rollback restores a preimage, it does not re-dispatch the base action.** The
  sketch's re-dispatch reads ids off the wrong side for add/remove (loader swaps
  `(old,new)` at `loader.py:102`) and re-running "remove" would archive again
  instead of un-archiving. Every forward op snapshots
  `(status, cluster, resource_type, intercept, row-exists?)`; rollback restores it.
  Rollback of an add that CREATED a row deletes exactly that row (true prestate);
  a pre-existing row only ever returns to its prior status.
- **Intercept** → process-local `_INTERCEPTS` registry + `get_intercept()` read
  seam (plus a snapshot in the audit row), NOT `resource_policies.preconditions_json`
  — that column is list-typed and read by `broker_runtime.py:352`.
- **`record_reconcile` lives in this module** (doc sketch had it in the handler);
  the handler also gained an `apply: bool = True` dry-run valve after a live trial
  showed partial desired trees mass-archive omitted actives (contract now in the
  tool docstring).
- `reassign` has no realm column to persist — it rematerializes config (realm is
  per-session at open).

## Impact gate + rollback
```
impact({target:"reconcile", direction:"upstream", file_path:"server/local_harness/loader.py"})
#   → LOW, 0 callers at gate time (MCP handler not in index; verified by reading)
```
Risk: MEDIUM (mutates `resources`). Mitigations: single txn, loader transactional reverse,
archive-not-delete, per-run audit row, `apply=false` dry-run valve.
Rollback: restore preview `apply=lambda *a: None` / pass `apply=false`; `apply_db.py`
is additive (delete it).
