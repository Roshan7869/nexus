#!/usr/bin/env python3
"""
Migration 008: Orphan cleanup + resource_delete trigger
========================================================
1. Cleans orphan cluster_membership rows (resource not active)
2. Adds trigger to auto-clean cluster_membership when resource is deleted
3. Seeds model_beliefs from routing_log for resources with outcomes but no beliefs
4. Adds nightly edge decay to nexus-maintenance.py
"""
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "db" / "nexus.db"


def run():
    print(f"=== Migration 008 ===")
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.row_factory = sqlite3.Row

    # ── Step 1: Clean orphan cluster_membership rows ─────────────────────
    orphans_before = conn.execute("""
        SELECT COUNT(*) FROM cluster_membership
        WHERE resource_id NOT IN (SELECT id FROM resources WHERE status = 'active')
    """).fetchone()[0]
    print(f"  Orphan cluster memberships before: {orphans_before}")

    conn.execute("""
        DELETE FROM cluster_membership
        WHERE resource_id NOT IN (SELECT id FROM resources WHERE status = 'active')
    """)
    conn.commit()

    orphans_after = conn.execute("""
        SELECT COUNT(*) FROM cluster_membership
        WHERE resource_id NOT IN (SELECT id FROM resources WHERE status = 'active')
    """).fetchone()[0]
    print(f"  Orphan cluster memberships after:  {orphans_after}")

    # ── Step 2: Add trigger to auto-clean cluster_membership on resource delete ──
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS cleanup_cluster_membership_on_resource_delete
        AFTER DELETE ON resources
        BEGIN
            DELETE FROM cluster_membership WHERE resource_id = OLD.id;
        END;
    """)
    conn.commit()
    print("  Created trigger: cleanup_cluster_membership_on_resource_delete")

    # ── Step 3: Backfill model_beliefs from routing_log (P0-3) ──────────
    backfilled = conn.execute("""
        INSERT OR IGNORE INTO model_beliefs
            (provider, model, task_type, alpha, beta, total_requests, total_reward, avg_reward, last_updated)
        SELECT
            'nexus',
            'resource:' || rl.skill_name,
            'general',
            SUM(CASE WHEN rl.outcome='correct' THEN 1.0 ELSE 0 END) + 1.0,
            SUM(CASE WHEN rl.outcome='wrong'   THEN 1.0 ELSE 0 END) + 1.0,
            COUNT(*),
            SUM(CASE WHEN rl.outcome='correct' THEN 1.0
                     WHEN rl.outcome='partial' THEN 0.5
                     ELSE 0 END),
            SUM(CASE WHEN rl.outcome='correct' THEN 1.0
                     WHEN rl.outcome='partial' THEN 0.5
                     ELSE 0 END) / CAST(COUNT(*) AS REAL),
            datetime('now')
        FROM routing_log rl
        WHERE rl.outcome IN ('correct', 'wrong', 'partial')
          AND rl.skill_name IS NOT NULL
          AND rl.skill_name != ''
        GROUP BY rl.skill_name
    """).rowcount
    conn.commit()
    print(f"  Backfilled {backfilled} new model_beliefs from routing_log")

    # ── Step 4: Sync resources.cluster from cluster_membership (final sync) ─
    synced = conn.execute("""
        UPDATE resources
        SET cluster = (
            SELECT cluster_name
            FROM cluster_membership
            WHERE resource_id = resources.id
            ORDER BY created_at DESC LIMIT 1
        )
        WHERE EXISTS (
            SELECT 1 FROM cluster_membership
            WHERE resource_id = resources.id
        )
        AND (
            cluster IS NULL
            OR cluster != (
                SELECT cluster_name FROM cluster_membership
                WHERE resource_id = resources.id
                ORDER BY created_at DESC LIMIT 1
            )
        )
    """).rowcount
    conn.commit()
    print(f"  Re-synced {synced} divergent resources.cluster from cluster_membership")

    # ── Summary ──────────────────────────────────────────────────────────
    total_active = conn.execute("SELECT COUNT(*) FROM resources WHERE status='active'").fetchone()[0]
    total_beliefs = conn.execute("SELECT COUNT(*) FROM model_beliefs").fetchone()[0]
    cold_beliefs = conn.execute("SELECT COUNT(*) FROM model_beliefs WHERE alpha=1.0 AND beta=1.0").fetchone()[0]
    final_orphans = conn.execute("""
        SELECT COUNT(*) FROM cluster_membership
        WHERE resource_id NOT IN (SELECT id FROM resources WHERE status='active')
    """).fetchone()[0]

    conn.close()

    print(f"\n  Active resources: {total_active}")
    print(f"  Model beliefs: {total_beliefs} ({cold_beliefs} still cold, {cold_beliefs/max(total_beliefs,1)*100:.1f}%)")
    print(f"  Remaining orphans: {final_orphans}")
    print("Migration 008 complete.")


if __name__ == "__main__":
    run()
