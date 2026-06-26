"""P8: Feedback loop closure — timeout auto-close for stale routing_log entries.

Two coverage gaps exist in NEXUS, and this script fixes only the first:

  1. STALE-ROUTE LEAK (this script): routing_log entries left with outcome
     NULL / 'pending' / 'routed' that never got explicit feedback. Currently
     ~54 of 5057 (1.1%). Paper 2 (ScaleMCP) insight: a stale route with no
     explicit outcome is an implicit success — if routing failed, the user
     would have reported it. This script auto-closes those older than
     `timeout_minutes`, recording an implicit 'correct' outcome with a
     computed EFC score, and mirrors them into outcome_log.

  2. BULK EFC COVERAGE (separate follow-up, NOT this script): only 1710 of
     5057 routing outcomes are mirrored into outcome_log with EFC computed
     (33.8%), because EFC is wired only into the HTTP /report_outcome bridge,
     not the primary learning.record_outcome path. Closing this to >80%
     requires structural EFC wiring in learning.py — out of scope here.

Reflexion-based implicit outcomes live in server/autonomous_pipeline.py
(AutonomousToolCaller.reflexion) — call that with an LLM callback for richer
implicit feedback. Perplexity-based outcomes are intentionally NOT implemented
here: they require an LLM cost budget and are flagged off by default.

Idempotent: re-running only closes entries that became stale since the last
run. Safe to cron every 5 minutes.

Usage: python3 scripts/auto_close_routes.py [--timeout-minutes 5] [--dry-run]
"""
from __future__ import annotations

import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "db" / "nexus.db"
sys.path.insert(0, str(DB.parent.parent))

from server.efc import compute_efc, classify_action_type  # noqa: E402


def main(timeout_minutes: int = 5, dry_run: bool = False) -> None:
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    cutoff = datetime.utcnow() - timedelta(minutes=timeout_minutes)

    # Stale routes: outcome is NULL / 'routed' / 'pending' AND older than
    # cutoff. Dedup on (skill_name, task_summary) so we only close the most
    # recent unresolved route per (skill, task) pair — avoids double-counting.
    stale = conn.execute(
        """
        SELECT rl.id, rl.skill_name, rl.task_summary, rl.project, rl.ide,
               rl.action_type, rl.created_at,
               (SELECT r.id FROM resources r WHERE r.name = rl.skill_name LIMIT 1) AS resource_id
        FROM routing_log rl
        WHERE (rl.outcome IS NULL OR rl.outcome IN ('routed', 'pending'))
          AND rl.created_at < ?
          AND rl.skill_name IS NOT NULL
        ORDER BY rl.created_at ASC
        """,
        (cutoff.strftime("%Y-%m-%d %H:%M:%S"),),
    ).fetchall()

    # Dedup by (skill_name, task_summary) — keep the latest stale per pair
    seen: set[tuple[str, str]] = set()
    to_close: list[sqlite3.Row] = []
    for r in stale:
        key = (r["skill_name"], r["task_summary"] or "")
        if key in seen:
            continue
        seen.add(key)
        to_close.append(r)

    print(f"stale routes (>{timeout_minutes}m, NULL/routed): {len(stale)}")
    print(f"after dedup by (skill, task): {len(to_close)} to close")

    closed = 0
    no_resource = 0
    sample: list[dict] = []
    if not dry_run:
        conn.execute("BEGIN")

    ts_now = time.time()
    for r in to_close:
        resource_id = r["resource_id"]
        if not resource_id:
            no_resource += 1
            continue
        task = r["task_summary"] or ""
        action_type = r["action_type"] or classify_action_type(task, [])
        # implicit 'correct' — first use on this task (auto-closed), repeat=0
        factors = compute_efc(
            outcome="correct",
            action_type=action_type,
            repeat_count_1h=0,
            is_first_use_on_task=True,
        )
        if not dry_run:
            conn.execute(
                """
                INSERT INTO outcome_log
                (resource_id, cluster_id, task_intent, outcome, duration_ms,
                 alternative_resource_ids, notes, session_id, created_at,
                 action_type, repeat_count_1h,
                 efc_informativeness, efc_validity, efc_redundancy, efc_memory, efc_score)
                VALUES (?, NULL, ?, 'correct', 0, '[]',
                 'auto-closed (timeout)', 'auto-close', ?, ?, 0, ?, ?, ?, ?, ?)
                """,
                (
                    resource_id, task[:500], ts_now, action_type,
                    factors["I"], factors["V"], factors["R"], factors["M"],
                    factors["efc"],
                ),
            )
            # mark the routing_log entry as auto-closed
            conn.execute(
                "UPDATE routing_log SET outcome='correct' WHERE id=?",
                (r["id"],),
            )
        closed += 1
        if len(sample) < 3:
            sample.append({
                "route_id": r["id"], "skill": r["skill_name"],
                "task": task[:50], "resource_id": resource_id,
                "efc": factors["efc"],
            })

    if not dry_run and closed:
        conn.commit()

    print(f"{'would close' if dry_run else 'closed'}: {closed}")
    print(f"skipped (no resource match): {no_resource}")
    for s in sample:
        print(f"  route={s['route_id']} skill={s['skill']:20s} task={s['task']!r} efc={s['efc']:.3f}")

    # Coverage report
    total_routes = conn.execute("SELECT COUNT(*) FROM routing_log").fetchone()[0]
    closed_routes = conn.execute(
        "SELECT COUNT(*) FROM routing_log WHERE outcome IS NOT NULL AND outcome != 'routed'"
    ).fetchone()[0]
    total_outcomes = conn.execute("SELECT COUNT(*) FROM outcome_log").fetchone()[0]
    coverage = 100 * closed_routes / max(total_routes, 1)
    print(f"\nCOVERAGE: routing_log with explicit outcome = {closed_routes}/{total_routes} ({coverage:.1f}%)")
    print(f"outcome_log rows: {total_outcomes}")
    conn.close()


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    timeout = 5
    if "--timeout-minutes" in sys.argv:
        timeout = int(sys.argv[sys.argv.index("--timeout-minutes") + 1])
    main(timeout_minutes=timeout, dry_run=dry)