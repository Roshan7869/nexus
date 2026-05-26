#!/usr/bin/env python3
"""~/nexus/server/feedback.py — Log routing outcomes to immune system

Delegates to learning.py:record_outcome() for the full learning pipeline
(Thompson Sampling, reflection, cluster affinity) instead of only updating
the routing_log and resources tables in isolation.

This fixes the Split-Path Problem (Gap 2+3+5) where feedback.py and
learning.py operated on independent copies of the same data.
"""
import sys
from pathlib import Path

DB = Path.home() / 'nexus/db/nexus.db'


def log_feedback(action: str):
    sys.path.insert(0, str(Path.home() / 'nexus'))
    """Log feedback for the most recent pending routing entry.

    Bridges the CLI/legacy feedback entry point into the unified
    record_outcome() pipeline so all 5 learning layers fire.
    """
    import sqlite3
    conn = sqlite3.connect(str(DB))
    try:
        conn.enable_load_extension(True)
        import sqlite_vec
        sqlite_vec.load(conn)
    except Exception:
        pass

    last = conn.execute("""
        SELECT id, skill_name, task_summary, project FROM routing_log
        WHERE outcome IS NULL
        ORDER BY created_at DESC LIMIT 1
    """).fetchone()

    if not last:
        print("No pending routing log entries to update.")
        conn.close()
        return

    log_id, skill_name, task_summary, project = last
    conn.close()

    if action not in ('correct', 'wrong'):
        print("action must be 'correct' or 'wrong'")
        return

    # Delegate to unified learning pipeline
    from learning import record_outcome
    result = record_outcome(
        skill_name=skill_name or "unknown",
        outcome=action,
        task_summary=task_summary or "",
        project=project or "default",
        ide="unknown",
    )
    status = result.get("status", "error")
    print(f"routing #{log_id}: marked {action.upper()} → learning={status}")
    if result.get("warning"):
        print(f"  ⚠ {result['warning']}")
    layers = result.get("layers_active", {})
    active = [k for k, v in layers.items() if v]
    print(f"  layers triggered: {', '.join(active)}")


if __name__ == '__main__':
    if len(sys.argv) < 2 or sys.argv[1] not in ('correct', 'wrong'):
        print("Usage: feedback.py correct|wrong")
        sys.exit(1)
    log_feedback(sys.argv[1])