# `server/intent_classifier.py` — fail-loud learning-signal writes (EDIT)

| Field | Value |
|-------|-------|
| Layer | L2 (learning-loop quality) |
| Status | DONE |
| New or edit | **EDIT** (behavior-preserving on the happy path; only the failure path changes) |
| Depends on | nothing new — module-level `logging` only |
| First-principle atom | **A5_feedback** |
| Gaps served | **G13** (no silent learning-signal loss) |

## Role — what this file is
The opencode-side intent telemetry path. `log_intent_telemetry(task, predicted_skills,
actual_outcome)` (line 393) is the single sink that either (a) delegates terminal outcomes to
`learning.record_outcome`, or (b) writes a non-terminal `routed` row directly to `routing_log`.
`update_weights` and the `intent_learning` insert are its sibling writers in the same file.
`router.py:30` imports it as `intent_log`; `tests/test_p0_p1_learning_contract.py` pins its
delegate contract.

## Issue it handles
Four writes in this file are wrapped in **silent `except: pass`**:

| Line | Write that gets swallowed | Signal lost |
|------|---------------------------|-------------|
| 412 | `record_outcome(...)` (terminal learning) | Thompson α/β update, EFC, edges |
| 425 | `intent_learning` insert (verb→skills) | verb-map learning |
| 444 | **`routing_log` INSERT** (non-terminal `routed`) | the coverage denominator itself |
| 461 | `update_weights` `intent_learning` insert | verb-weight learning |

The 444 swallow is the G13 headline: when a `routing_log` write fails (locked DB, schema
drift), the row silently never lands. `feedback_coverage` is computed *from* `routing_log`
(rl_feedback_loop.py:74), so a swallowed write doesn't lower coverage — it **vanishes from both
numerator and denominator**. The 96% coverage number cannot see its own dropped writes. This is
the exact silent-signal-loss Reflexion (`arXiv:2303.11366`) and observability
(`arXiv:2602.10479`) name as the thing that makes a learning loop un-auditable.

Note (as-investigated): the *other* `routing_log` writer, `router.py:1286`, is **already
fail-loud** (`logger.warning("Routing log INSERT failed: {e}")`). This edit brings the
intent-classifier path up to that same standard — it does not invent a new convention.

## Research grounding
- **`arXiv:2303.11366` (Reflexion)** — a learning loop must never discard its own outcome
  signal; a swallowed write is an invisible reward drop. We take: every learning/telemetry
  write failure must surface.
- **`arXiv:2602.10479` (observability)** — failures need a countable, greppable signal, not a
  dropped exception. We take: a structured, scrapeable log tag now; a durable sink is G12's
  budgeted "small new", not G13's.

## Interface / contract
No signature change. `log_intent_telemetry` / `update_weights` keep their exact happy-path
behavior. Only the failure branch changes: `except: pass` → `except Exception as e:` that
(1) logs `logger.warning` and (2) emits a greppable metric line via a local seam.

```python
logger = logging.getLogger(__name__)            # module-level (was: local import at L555)

def _metric_fail(op: str, exc: Exception) -> None:
    """Fail-loud seam for swallowed learning-signal writes (G13).
    Today: a greppable structured warning. G12 may upgrade this to a durable
    counter sink WITHOUT touching call sites."""
    logger.warning("NEXUS_WRITE_FAIL op=%s err=%s", op, exc)
```
Invariants:
- **Happy path unchanged** — success emits nothing new; the pinned delegate contract
  (`test_p0_p1_learning_contract.py`) still holds.
- **No new schema, no new table, no new flag** (D4-safe; the durable sink is deferred to G12).
- **One seam, four call sites** — the sink can be upgraded in one place later.

## Guided plan
1. Add module-level `import logging` + `logger`; add `_metric_fail` seam.
2. Replace the 4 silent `except: pass` (L412 record_outcome, L425 intent_learning,
   L444 routing_log, L461 update_weights) with
   `except Exception as e: logger.warning(...); _metric_fail("<op>", e)`.
3. Keep the local `import logging` at L555 harmless (or drop — module-level now covers it).

## Acceptance
- [x] A forced `routing_log` write failure emits `NEXUS_WRITE_FAIL op=routing_log` at WARNING
      and does **not** raise (test with a patched connection that throws).
      — `test_routing_log_write_failure_is_fail_loud_not_swallowed` (patches `sqlite3.connect`
      → `OSError`, asserts `NEXUS_WRITE_FAIL` + `op=routing_log` in logs, no raise).
- [x] Happy path unchanged: `test_p0_p1_learning_contract.py` `TestIntentTelemetry` still green
      (routed → no record_outcome; wrong → delegates once with co_skills). — 8/8 pass.
- [x] No new table / flag introduced (grep the diff — only `logging`/`logger`/`_metric_fail`).
      — confirmed; also folded the stray local `import logging` at old L555 into the module logger.
- [x] Full suite green; `gitnexus check --cycles` unchanged (no new imports beyond stdlib).
      — 466 passed / 24 skipped (was 465; +1 new test); cycles unchanged (3 pre-existing harness
      cycles, none in intent_classifier).

## As built (deltas vs the sketch)
- Fixed **all four** silent swallows in the file (L412 record_outcome, L425 intent_learning,
  L444 routing_log, L461 update_weights) — same signal-loss anti-pattern, one coherent atom.
- Also removed the redundant function-local `import logging` in `recalibrate` (now uses the
  module `logger`).
- **Metric sink = log-based** (`NEXUS_WRITE_FAIL` structured WARNING), not a DB table: G13 is
  scoped "edit"; a durable counter sink is G12's budgeted "small new". The `_metric_fail` seam
  is the single upgrade point — G12 can back it with a table without touching call sites.
- `impact(log_intent_telemetry)` = LOW / 0 upstream. `detect_changes(all)` reported CRITICAL,
  but that risk is entirely the pre-existing uncommitted router/retrieval track; the
  intent_classifier symbols appear in **no** affected process (leaf telemetry, isolated).

## Impact gate + rollback
```
impact({target:"log_intent_telemetry", direction:"upstream", file_path:"server/intent_classifier.py"})
  → LOW (0 indexed upstream; leaf telemetry, called via router.py:30 dynamic import + tests)
```
Risk: LOW (failure-path-only; happy path byte-for-byte). Rollback: revert the 4 `except`
bodies to `pass` and drop the seam — no schema/flag to unwind.
