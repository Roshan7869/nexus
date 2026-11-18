# `server/broker_runtime.py` — route-id lifecycle: timeout-gated auto-close (EDIT)

| Field | Value |
|-------|-------|
| Layer | L2 (learning-loop quality) |
| Status | IN PROGRESS |
| New or edit | **EDIT** (adds an optional timeout gate; verdict/quality unchanged) |
| Depends on | routing_log `created_at` (exists); companion migration in `server/db.py` |
| First-principle atom | **A5_feedback** |
| Gaps served | **G12** (route-id lifecycle: open → timeout auto-close → explicit outcome) |

## Role — what this file is
`_close_routing_outcomes(conn, project, ide, max_rows, quality=0.3)` (line 985) is the
auto-close path: it scans `routing_log` for unclosed rows (`outcome NULL / routed / pending`)
that carry a `skill_name` and closes each through `learning.record_outcome(outcome="correct",
quality=0.3)`. Three callers: `close_outcomes` (MCP tool), `review_task_run` (broker_runtime:1129),
`_close_stale_routes` (rl_feedback_loop:416). Impact: **HIGH** (3 direct, 7 total, 3 processes).

## Issue it handles
Two defects in the current auto-close, both flagged by `server/implicit_outcomes.py`'s own
anti-inflation docstring (the "disabled `_auto_close_routed` / dormant `auto_close_routes.py`
landmine"):

1. **No timeout gate.** It closes rows of *any* age, `ORDER BY created_at DESC` — i.e. it
   sweeps the **newest** rows first, the ones most likely to still receive an explicit human
   verdict. A route created 3s ago gets auto-verdicted before the IDE can report on it. The
   lifecycle should be **open → (window for explicit outcome) → timeout → auto-close**.
2. **Derived verdicts are indistinguishable from explicit ones.** Once closed, an auto-`correct`
   row is byte-identical to a human `correct` in `routing_log`; the 96% coverage number is not
   auditable and can't be split into explicit-vs-derived.

Per the G12 decision (user: *keep current behavior, just harden* + *add audit columns*): the
**verdict stays `correct` and quality stays 0.3** — this is deliberately NOT rerouted through
`implicit_outcomes.derive_outcome` (that stale→no-signal redefinition was declined). We only add
the timeout gate and the audit marker.

## Change sketch
- New param `min_age_seconds: int = 60` (matches `implicit_outcomes.STALE_MS = 60_000`).
- WHERE gains `AND (julianday('now') - julianday(created_at)) * 86400 >= :min_age`.
- `ORDER BY created_at DESC` → **`ASC`** (sweep the oldest stale backlog; leave fresh rows their
  explicit-outcome window).
- Each closure passes `outcome_source="timeout"` into `record_outcome` (see learning.py doc).
- Return dict gains `min_age_seconds` for observability. `status` gains no new value.

All three callers pass no new arg → they inherit the safe 60s default. `_close_stale_routes` is
literally named for stale routes, so the gate is exactly its intended semantics; `review_task_run`
closing project routes after a human review is unaffected (session routes are older than 60s).

## Acceptance
- [ ] A fresh row (`created_at = now`) is **not** closed; the same row after `min_age_seconds`
      **is** closed as `correct` / `outcome_source='timeout'` / quality 0.3.
- [ ] Verdict + quality unchanged vs today (still `correct`, still 0.3) — no `derive_outcome`.
- [ ] All 3 callers still compile and pass no new arg; default 60s gate applied.
- [ ] Full suite green; `gitnexus check --cycles` unchanged (no new imports).
