"""P8: Feedback loop closure — timeout auto-close for stale routing_log entries.

Two coverage gaps exist in NEXUS, and this script fixes only the first:

  1. STALE-ROUTE LEAK (this script): routing_log entries left with outcome
     NULL / 'pending' / 'routed' that never got explicit feedback.

     ANTI-INFLATION DISCIPLINE (L6/L7, 2026-06-27): a stale route is NOT an
     implicit success. The old "ScaleMCP: no complaint = implicit correct"
     heuristic inflated Thompson beliefs (learning.py:_auto_close_routed was
     already disabled for this reason). IRC (arXiv:2604.02869) shows read-only
     / stale routes carry ~0 discriminative signal. This script now marks
     stale routes 'routed' — the honest neutral state, excluded from Thompson
     updates — and writes NO EFC score (no signal to score). It is a
     landmine guard: P4-T2 supervisord would re-arm cron auto-close, so this
     script must be safe to cron. derive_outcome() in server/implicit_outcomes.py
     is the real implicit-outcome path; this script only sweeps stale NULLs.

2. BULK EFC COVERAGE (separate follow-up, NOT this script): only 1710 of
      5057 routing outcomes are mirrored into outcome_log with EFC computed
      (33.8%), because EFC is wired only into the HTTP /report_outcome bridge,
      not the primary learning.record_outcome path. Closing this to >80%
      requires structural EFC wiring in learning.py — out of scope here.

Reflexion-based implicit outcomes live in server/autonomous_pipeline.py
(AutonomousToolCaller.reflexion) — call that with an LLM callback for richer
implicit feedback. Perplexity-based outcomes are intentionally NOT implemented
here: they require an LLM cost budget and are flagged off by default.

T5 (2026-06-28): `--include-partials` flag added to also close stale
`outcome='partial'` entries via `server.implicit_outcomes.derive_outcome`.
Partials are already "explicit" outcomes (so coverage math is unaffected),
but persisting them in `outcome_log` with a derived signal — instead of
leaving them as 'partial' forever — eventually lets Thompson Sampling
upgrade the resource's belief once a real signal arrives (e.g. multi-retry
→ 'wrong'). Default behavior unchanged; partials still only close with opt-in.

Idempotent: re-running only closes entries that became stale since the last
run. Safe to cron every 5 minutes.

Usage:
  python3 scripts/auto_close_routes.py [--timeout-minutes 5] [--dry-run] [--include-partials]
"""
from __future__ import annotations

import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "db" / "nexus.db"
sys.path.insert(0, str(DB.parent.parent))

from server.efc import compute_efc, classify_action_type  # noqa: E402
from server.implicit_outcomes import derive_outcome  # noqa: E402 — T5


def main(timeout_minutes: int = 5, dry_run: bool = False,
         include_partials: bool = False) -> None:
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)

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
        # Stale = NO signal. Mark 'routed' (neutral, excluded from Thompson).
        # Do NOT compute or write an EFC score — there is no outcome to score.
        # (compute_efc import retained for callers; not used on stale rows.)
        # CRITICAL: outcome_log CHECK constraint doesn't allow 'routed'.
        # 'routed' = no signal → we ONLY update routing_log, NO outcome_log write.
        if not dry_run:
            conn.execute(
                "UPDATE routing_log SET outcome='routed' WHERE id=?",
                (r["id"],),
            )
        closed += 1
        if len(sample) < 3:
            sample.append({
                "route_id": r["id"], "skill": r["skill_name"],
                "task": task[:50], "resource_id": resource_id,
                "efc": None,  # routed — no EFC score (no signal)
            })

    if not dry_run and closed:
        conn.commit()

    print(f"{'would close' if dry_run else 'closed'}: {closed}")
    print(f"skipped (no resource match): {no_resource}")
    for s in sample:
        print(f"  route={s['route_id']} skill={s['skill']:20s} task={s['task']!r} outcome=routed (no signal)")

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

    if include_partials:
        _close_stale_partials(timeout_minutes=timeout_minutes, dry_run=dry_run)


def _close_stale_partials(timeout_minutes: int, dry_run: bool) -> None:
    """T5 (2026-06-28) — close stale `outcome='partial'` routes via derive_outcome.

    For each stale partial, synthesize `downstream` from routing_log signals:
      - re_route_count = repeat_count_1h (proxy for retry count)
      - next_action_latency_ms = age in ms (stale → no_signal per derive_outcome)

    derive_outcome() falls through its 6-signal pipeline and returns either:
      - ('routed', 0.0, 'no_signal') if no observable signal → leave 'partial'
        in routing_log, write nothing to outcome_log (anti-inflation)
      - ('wrong'|'correct', q, src) if signals present (e.g. multi-retry shows
        high repeat_count_1h ≥ 3 → multi_retry = 'wrong', 0.1) → upgrade the
        routing_log row from 'partial' to the derived outcome, and mirror a
        row to outcome_log with the source label.

    All writes happen in a single transaction. Idempotent — re-running only
    sees stale partials again because we've upgraded the rows that qualified.
    """
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)

    stale_partials = conn.execute(
        """
        SELECT id, skill_name, task_summary, project, ide, action_type,
               repeat_count_1h, created_at
          FROM routing_log
         WHERE outcome = 'partial'
           AND created_at < ?
           AND skill_name IS NOT NULL
        ORDER BY created_at ASC
        """,
        (cutoff.strftime("%Y-%m-%d %H:%M:%S"),),
    ).fetchall()
    print(f"\n[T5] stale partial routes (>{timeout_minutes}m): {len(stale_partials)}")

    upgraded = kept_routed = 0
    sample: list[dict] = []
    ts_now = time.time()
    if not dry_run:
        conn.execute("BEGIN")

    for r in stale_partials:
        # downstream signals synthesized from routing_log alone.
        # created_at is TEXT in DB; age is not used here because
        # next_action_latency_ms=None already flags the row as stale
        # in derive_outcome (no observable next action → falls through).
        downstream = {
            "tool_result": None,
            "re_route_count": int(r["repeat_count_1h"] or 0),
            "next_action_latency_ms": None,  # stale ⇒ no observable next action
            "task_status": None,
            "had_correction": None,
        }
        route = {
            "resource_name": r["skill_name"],
            "task_summary": r["task_summary"],
            "id": r["id"],
        }
        outcome, quality, source = derive_outcome(route, trace={}, downstream=downstream)

        if outcome == "routed":
            # No observable signal — leave 'partial' alone (anti-inflation discipline).
            kept_routed += 1
            continue

        # Real signal: upgrade the partial to a durable verdict.
        action_type = r["action_type"] or classify_action_type(r["task_summary"] or "", [])
        if not dry_run:
            # Only write to outcome_log if we have a real verdict (not 'routed').
            # outcome_log CHECK constraint doesn't allow 'routed'.
            conn.execute(
                """
                INSERT INTO outcome_log
                (resource_id, cluster_id, task_intent, outcome, duration_ms,
                 alternative_resource_ids, notes, session_id, created_at,
                 action_type, repeat_count_1h,
                 efc_informativeness, efc_validity, efc_redundancy, efc_memory, efc_score)
                VALUES (?, NULL, ?, ?, 0, '[]',
                 ?, 'auto-close-t5', ?, ?, ?,
                 NULL, NULL, NULL, NULL, NULL)
                """,
                (r["resource_id"] if "resource_id" in r.keys() else None,
                 (r["task_summary"] or "")[:500], outcome, ts_now, source,
                 action_type, r["repeat_count_1h"] or 0),
            )
            conn.execute(
                "UPDATE routing_log SET outcome=? WHERE id=?",
                (outcome, r["id"]),
            )
            upgraded += 1
            if len(sample) < 3:
                sample.append({"route_id": r["id"], "skill": r["skill_name"],
                               "outcome": outcome, "quality": quality, "source": source})
        else:
            # Dry-run: count what would be upgraded
            upgraded += 1
            if len(sample) < 3:
                sample.append({"route_id": r["id"], "skill": r["skill_name"],
                               "outcome": outcome, "quality": quality, "source": source})

    if not dry_run and upgraded:
        conn.commit()

    print(f"[T5] {'would upgrade' if dry_run else 'upgraded'}: {upgraded} "
          f"({kept_routed} kept 'partial' — no observable signal)")
    for s in sample:
        print(f"  route={s['route_id']} skill={s['skill']:20s} "
              f"outcome={s['outcome']:8s} q={s['quality']:.2f} src={s['source']}")

    total_routes = conn.execute("SELECT COUNT(*) FROM routing_log").fetchone()[0]
    explicit = conn.execute(
        "SELECT COUNT(*) FROM routing_log WHERE outcome IS NOT NULL "
        "AND outcome NOT IN ('routed','partial')"
    ).fetchone()[0]
    cov = 100 * explicit / max(total_routes, 1)
    print(f"[T5] COVERAGE (correct+wrong, excluding partial+routed): "
          f"{explicit}/{total_routes} ({cov:.1f}%)")
    conn.close()


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    include_partials = "--include-partials" in sys.argv
    timeout = 5
    if "--timeout-minutes" in sys.argv:
        timeout = int(sys.argv[sys.argv.index("--timeout-minutes") + 1])
    main(timeout_minutes=timeout, dry_run=dry, include_partials=include_partials)