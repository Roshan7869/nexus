#!/usr/bin/env python3
"""
NEXUS Thompson Sampling Belief Backfill
=========================================
Reconciles cold model_beliefs (alpha=1, beta=1) with historical
routing_log outcomes for resources.

The learning system stores resource beliefs as:
    provider='nexus', model='resource:{skill_name}', task_type=<classified>

This script counts correct/wrong outcomes per skill_name from routing_log
and updates the alpha/beta priors accordingly.

Run once after initial deployment, or on-demand for reconciliation.
"""
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "db" / "nexus.db"


def backfill_beliefs():
    print(f"Connecting to: {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.row_factory = sqlite3.Row

    # Step 1: Find all resource skill_names that appear in routing_log with real outcomes
    outcome_rows = conn.execute("""
        SELECT skill_name,
               SUM(CASE WHEN outcome='correct' THEN 1 ELSE 0 END) as correct_cnt,
               SUM(CASE WHEN outcome='wrong' THEN 1 ELSE 0 END) as wrong_cnt,
               SUM(CASE WHEN outcome='partial' THEN 0.5 ELSE 0 END) as partial_alpha,
               SUM(CASE WHEN outcome='partial' THEN 0.5 ELSE 0 END) as partial_beta,
               COUNT(*) as total_cnt
        FROM routing_log
        WHERE outcome IS NOT NULL AND skill_name IS NOT NULL AND skill_name != ''
        GROUP BY skill_name
    """).fetchall()

    print(f"Found {len(outcome_rows)} distinct resources with routing_log outcomes")

    updated = 0
    skipped_no_belief = 0
    skipped_already_warm = 0

    for row in outcome_rows:
        skill_name = row["skill_name"]
        correct = row["correct_cnt"] or 0.0
        wrong = row["wrong_cnt"] or 0.0
        partial_a = row["partial_alpha"] or 0.0
        partial_b = row["partial_beta"] or 0.0

        new_alpha = 1.0 + correct + partial_a
        new_beta = 1.0 + wrong + partial_b

        # Build resource key as stored in model_beliefs
        resource_key = f"resource:{skill_name}"

        # Check for existing belief rows (any task_type)
        existing = conn.execute("""
            SELECT id, alpha, beta, task_type FROM model_beliefs
            WHERE provider = 'nexus' AND model = ?
        """, (resource_key,)).fetchall()

        if not existing:
            # No belief exists — create a general one
            conn.execute("""
                INSERT OR IGNORE INTO model_beliefs
                    (provider, model, task_type, alpha, beta, total_requests, total_reward, avg_reward)
                VALUES ('nexus', ?, 'general', ?, ?, ?, ?, ?)
            """, (
                resource_key,
                new_alpha, new_beta,
                int(correct + wrong),
                float(correct + partial_a * 0.5),
                float(correct + partial_a * 0.5) / max(int(correct + wrong), 1),
            ))
            updated += 1
            skipped_no_belief += 1
        else:
            for belief in existing:
                # Only update if still at cold prior
                if belief["alpha"] == 1.0 and belief["beta"] == 1.0:
                    conn.execute("""
                        UPDATE model_beliefs
                        SET alpha = ?,
                            beta = ?,
                            total_requests = total_requests + ?,
                            last_updated = datetime('now')
                        WHERE id = ?
                    """, (new_alpha, new_beta, int(correct + wrong), belief["id"]))
                    updated += 1
                else:
                    skipped_already_warm += 1

    conn.commit()

    # Verification
    cold_after = conn.execute(
        "SELECT COUNT(*) FROM model_beliefs WHERE alpha = 1.0 AND beta = 1.0"
    ).fetchone()[0]
    total_beliefs = conn.execute("SELECT COUNT(*) FROM model_beliefs").fetchone()[0]

    conn.close()

    print(f"\nBackfill complete:")
    print(f"  Updated beliefs: {updated}")
    print(f"  Created new beliefs: {skipped_no_belief}")
    print(f"  Skipped (already warm): {skipped_already_warm}")
    print(f"  Cold beliefs after: {cold_after}/{total_beliefs} ({cold_after/total_beliefs*100:.1f}%)")


if __name__ == "__main__":
    backfill_beliefs()
