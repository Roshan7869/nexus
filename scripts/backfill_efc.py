"""Backfill EFC columns for all existing outcome_log rows.

Root cause (2026-06-26): the EFC bridge in nexus_http_daemon._bridge() only runs
on the HTTP /report_outcome path. The other 1,709 outcome_log rows were inserted
via learning.record_outcome / MCP paths that bypass EFC computation entirely.
Result: efc_score populated for 1/1710 rows.

This script recomputes EFC for every outcome_log row using the data already
captured (outcome, action_type, repeat_count_1h, task_intent, resource_id) and
the server.efc.compute_efc function. It writes the 5 dedicated EFC columns:
    efc_informativeness, efc_validity, efc_redundancy, efc_memory, efc_score

is_first_use_on_task is approximated as: this is the first outcome_log row
(ordered by created_at) for the (resource_id, task_intent) pair. This matches
the paper's M-factor semantics (first signal on a task = strong memory-write).

Reversible: only writes NULL-able REAL columns. To revert:
    UPDATE outcome_log SET efc_score = NULL,
        efc_informativeness = NULL, efc_validity = NULL,
        efc_redundancy = NULL, efc_memory = NULL;

Usage: python3 scripts/backfill_efc.py [--dry-run]
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "db" / "nexus.db"
sys.path.insert(0, str(DB.parent.parent))

from server.efc import compute_efc, classify_action_type  # noqa: E402


def main(dry_run: bool = False) -> None:
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        """
        SELECT id, resource_id, task_intent, outcome, action_type, repeat_count_1h, created_at
        FROM outcome_log
        ORDER BY created_at ASC
        """
    ).fetchall()

    print(f"outcome_log rows to process: {len(rows)}")

    # Pre-compute first-use flags in one pass: first occurrence of
    # (resource_id, task_intent) ordered by created_at is a "first use".
    seen_pairs: set[tuple[int | None, str]] = set()
    first_use_by_id: dict[int, bool] = {}
    for r in rows:
        key = (r["resource_id"], (r["task_intent"] or "")[:200])
        first_use_by_id[r["id"]] = key not in seen_pairs
        seen_pairs.add(key)

    updated = 0
    skipped = 0
    sample: list[dict] = []

    if not dry_run:
        conn.execute("BEGIN")

    for r in rows:
        outcome = r["outcome"]
        if outcome is None:
            skipped += 1
            continue
        action_type = r["action_type"] or classify_action_type(r["task_intent"] or "", [])
        repeat = int(r["repeat_count_1h"] or 0)
        first_use = first_use_by_id[r["id"]]

        factors = compute_efc(
            outcome=outcome,
            action_type=action_type,
            repeat_count_1h=repeat,
            is_first_use_on_task=first_use,
        )

        if not dry_run:
            conn.execute(
                """
                UPDATE outcome_log
                SET action_type = ?,
                    efc_informativeness = ?,
                    efc_validity = ?,
                    efc_redundancy = ?,
                    efc_memory = ?,
                    efc_score = ?
                WHERE id = ?
                """,
                (
                    action_type,
                    factors["I"], factors["V"], factors["R"], factors["M"],
                    factors["efc"],
                    r["id"],
                ),
            )
        updated += 1
        if len(sample) < 5:
            sample.append({
                "id": r["id"], "outcome": outcome, "action_type": action_type,
                "repeat": repeat, "first_use": first_use,
                "efc": factors["efc"], "nrs_efc": factors["nrs_efc"],
            })

    if not dry_run:
        conn.commit()

    print(f"{'would update' if dry_run else 'updated'}: {updated}")
    print(f"skipped (NULL outcome): {skipped}")
    print("sample:")
    for s in sample:
        print(f"  id={s['id']:5d} outcome={s['outcome']:8s} act={s['action_type']:18s} "
              f"rep={s['repeat']} first={s['first_use']} efc={s['efc']:.3f} nrs={s['nrs_efc']:.3f}")

    # Verify
    populated = conn.execute(
        "SELECT COUNT(*) FROM outcome_log WHERE efc_score IS NOT NULL"
    ).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM outcome_log").fetchone()[0]
    print(f"\nPOST: efc_score NOT NULL = {populated}/{total} ({100*populated/max(total,1):.1f}%)")

    # Distribution
    print("efc_score distribution by outcome:")
    for r in conn.execute(
        "SELECT outcome, COUNT(*), ROUND(AVG(efc_score), 3), ROUND(MIN(efc_score), 3), ROUND(MAX(efc_score), 3) "
        "FROM outcome_log WHERE efc_score IS NOT NULL GROUP BY outcome"
    ).fetchall():
        print(f"  {r[0]:8s} n={r[1]:5d} avg={r[2]} min={r[3]} max={r[4]}")

    conn.close()


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    main(dry_run=dry)