# A2 — Single `record_outcome` API

Source: plan §A2 · Commit: `A2: single record_outcome API with non-terminal guard` · Depends on: A1 · Gates: G2, G3

## Problem (verified)

- `learning.record_outcome` exists at `server/learning.py:321` with signature
  `(skill_name, outcome, task_summary="", project="default", ide="unknown", tokens_used=0, routed_id=None, quality=1.0, co_skills=None, outcome_source="explicit")`.
- `event_bridge.py` has its own `record_outcome` (plan lines 141–153 — **re-locate**).
- `broker_runtime.py:_emit_outcome_for_trace` (line 648) emits outcomes for every trace.
- `broker_runtime.py:review_task_run` writes `execution_reviews` and `task_runs` itself.
- Terminal vs non-terminal guard is supposed to live in `learning.py` (plan says 357–394 — **re-locate**).

## Corrections to source plan

| Plan says | Reality |
|---|---|
| Test uses `hermes-agent` on live DB | Row exists (`id=1520`) but test must not mutate the live DB. |
| `review_task_run` pseudocode | Columns verified: `execution_reviews(task_run_id, verdict, comment)`, `task_runs.review_status`, `task_runs.updated_at`, `tool_traces.task_run_id`. Pseudocode is usable. |
| `log_outcome(skill_name=…, outcome=…, task_summary=…, project=…, ide=…)` | Matches `learning.record_outcome` signature. ✅ |
| `normalize_legacy_ide` | Not verified — grep before use. |

## Pseudocode (corrected)

```python
# server/broker_runtime.py
from server.learning import record_outcome as log_outcome

TERMINAL = {"correct", "wrong", "partial", "skip"}

def review_task_run(task_run_id: str, verdict: str, comment: str = "") -> Dict[str, Any]:
    if verdict not in TERMINAL:
        return {"status": "error", "message": f"verdict must be one of {sorted(TERMINAL)}"}
    initialize_runtime()
    conn = _get_conn()
    try:
        run = conn.execute("SELECT * FROM task_runs WHERE id=?", (task_run_id,)).fetchone()
        if not run:
            return {"status": "error", "message": f"task_run {task_run_id} not found"}
        traces = conn.execute(
            "SELECT resource_name FROM tool_traces WHERE task_run_id=? AND status='executed'",
            (task_run_id,)).fetchall()
        for row in traces:
            log_outcome(skill_name=row["resource_name"], outcome=verdict,
                        task_summary=run["task_text"], project=run["project"],
                        ide=run["ide_name"], outcome_source="review")
        conn.execute("INSERT INTO execution_reviews(task_run_id, verdict, comment) VALUES (?,?,?)",
                     (task_run_id, verdict, comment))
        conn.execute("UPDATE task_runs SET review_status='reviewed', updated_at=datetime('now') WHERE id=?",
                     (task_run_id,))
        conn.commit()
        return {"status": "reviewed", "task_run_id": task_run_id, "verdict": verdict}
    finally:
        conn.close()
```

Non-terminal guard (in `learning.record_outcome`, before counters/Thompson/edges):

```python
if outcome not in TERMINAL:
    _insert_routing_log_only(skill_name, outcome, ...)   # keep the row, skip beliefs
    return {"updated": False, "reason": "non_terminal"}
```

Replace `event_bridge.record_outcome` with a call to `learning.record_outcome`; delete the duplicate.

## Steps

1. Read `learning.py` 321–400 and confirm where the terminal/non-terminal split happens. Record the line numbers in the commit body.
2. Grep callers: `grep -rn "record_outcome(" server core --include=*.py`. Every call must pass a terminal outcome or hit the guard.
3. Delete `event_bridge.record_outcome`, `shutdown_bridge`, `get_learning_stats` **after** confirming no callers (`grep`). Keep `EventBridge.outcome_to_event` but make it update Thompson beliefs only.
4. Replace `review_task_run` body with the pseudocode.
5. Write tests (below) using in-memory fixtures.

## Tests

New `tests/test_learning_loop.py` — **do not use the live DB**:

```python
import pytest
from server.learning import record_outcome

@pytest.fixture
def seeded(mock_rw_conn, make_db_conn):
    conn = make_db_conn()
    conn.execute("INSERT INTO resources(name, resource_type, times_triggered, times_correct) "
                 "VALUES ('probe-skill','skill',0,0)")
    conn.commit()
    return conn

def _counts(conn):
    return tuple(conn.execute("SELECT times_triggered, times_correct FROM resources "
                              "WHERE name='probe-skill'").fetchone())

def test_non_terminal_does_not_update_counters(seeded, mock_rw_conn):
    before = _counts(seeded)
    record_outcome("probe-skill", "routed", task_summary="t", project="nexus", ide="test")
    assert _counts(seeded) == before                       # G2

def test_terminal_updates_counters(seeded, mock_rw_conn):
    record_outcome("probe-skill", "correct", task_summary="t", project="nexus", ide="test")
    t, c = _counts(seeded)
    assert (t, c) == (1, 1)                                # G3
```

`make_db_conn` and `mock_rw_conn` are existing fixture shapes in `tests/conftest.py` (`make_db`, `mock_rw_conn`). If they don't fit `learning.py`'s `_get_conn`, patch `server.learning._get_conn` instead.

Run: `pytest tests/test_learning_loop.py -v`

## Rollback

Revert the commit. `routing_log` writes are unchanged by design, so coverage numbers only move if the guard is wrong.

## Open decisions

- `outcome_source="review"` is a new value. Check whether `learning.py` validates `outcome_source` against an enum first.
