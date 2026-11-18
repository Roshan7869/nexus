# `server/learning.py` — stamp `outcome_source` + `quality` on routing_log (EDIT)

| Field | Value |
|-------|-------|
| Layer | L2 (learning-loop quality) |
| Status | IN PROGRESS |
| New or edit | **EDIT** (additive optional kwarg; existing counter/Thompson logic untouched) |
| Depends on | companion migration in `server/db.py` (two new columns) |
| First-principle atom | **A5_feedback** |
| Gaps served | **G12** (audit marker: derived/timeout vs explicit verdicts) |

## Role — what this file is
`record_outcome(...)` (line 321) is the **primary** feedback path — 18 direct callers, 51 total,
17 processes (impact: **CRITICAL**). It writes the `routing_log` outcome (UPDATE by `routed_id`
or INSERT), bumps resource counters, bridges Thompson beliefs, and triggers degradation +
reflection. It has a non-terminal early-return branch (357–394) and a terminal branch (396+).

## Issue it handles
`routing_log` has no column recording *how* an outcome was produced, so an auto-closed derived
`correct` (from `broker_runtime._close_routing_outcomes`, quality 0.3) is indistinguishable from
an explicit human `correct` (quality 1.0). The G12 audit marker fixes this.

## Change sketch (surgical — CRITICAL hub, additive only)
- New optional kwarg **`outcome_source: str = "explicit"`** on `record_outcome`. Default keeps
  every existing caller (MCP `report_outcome`, human verdicts) tagged `explicit` with zero edits.
- Stamp `outcome_source` **and** the already-present `quality` into both routing_log writes:
  - terminal UPDATE (L401) and INSERT (L405);
  - non-terminal UPDATE (L362) and INSERT (L367).
- **No** change to counters, Thompson bridge, degradation, reflection, or the `quality`-weighting
  math — this only persists two audit fields already flowing through the function.
- Callers that pass a derived verdict tag themselves: `_close_routing_outcomes` →
  `outcome_source="timeout"`; `implicit_outcomes` consumers → `"derived"`.

Signature change is a trailing optional kwarg → none of the 51 upstream callers break (verified
via `impact(record_outcome, upstream)` — all call sites are keyword/positional-safe).

## Companion edits (same G12 atom)
- `server/db.py::ensure_routing_log_schema` — add `outcome_source TEXT` + `quality REAL` to the
  guarded additive-ALTER block (impact: LOW / 0 upstream). Legacy rows stay NULL.
- `server/rl_feedback_loop.py::get_feedback_coverage` — **additive** breakdown keys
  (`explicit_with_outcome`, `derived_with_outcome`, `by_outcome_source`) so the 96% headline
  becomes auditable. Existing keys/`routing_coverage_pct` unchanged (impact: LOW; callers only
  read the dict). Guarded so a pre-migration DB still returns the legacy shape.

## Acceptance
- [ ] Explicit `report_outcome` → row has `outcome_source='explicit'`, `quality=1.0`.
- [ ] Timeout auto-close → row has `outcome_source='timeout'`, `quality=0.3`.
- [ ] `get_feedback_coverage()` exposes explicit-vs-derived split; `routing_coverage_pct`
      numerically unchanged from before (headline held per G12 decision).
- [ ] No counter/Thompson/degradation behavior change (existing contract tests still green).
- [ ] Full suite green; migration idempotent (re-run adds nothing).
