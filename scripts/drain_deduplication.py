#!/usr/bin/env python3
"""
NEXUS Deduplication Drain Script
=================================
Drains pending candidate pairs from dedup_candidates with similarity >= threshold (default 0.90).
For each pair, selects the canonical / more-triggered resource as survivor, and marks the other as:
    status = 'archived'
    archive_reason = 'dedup_merge'
    lifecycle_state = 'merged'

Updates dedup_candidates:
    status = 'merged'
    merged_into_id = survivor_id
    reviewed_at = datetime('now')
"""

import os
import sys
import argparse
import sqlite3
import logging
from pathlib import Path

# Add project root to sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from server.db import resolve_db_path, get_connection

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("drain_dedup")


def drain_deduplication(db_path: Path | str, threshold: float = 0.90) -> dict:
    db_file = Path(db_path).resolve()
    if not db_file.exists():
        raise FileNotFoundError(f"Database not found at {db_file}")

    conn = sqlite3.connect(str(db_file), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")

    try:
        candidates = conn.execute("""
            SELECT id, resource_id_a, resource_id_b, similarity_score
            FROM dedup_candidates
            WHERE status = 'pending' AND similarity_score >= ?
            ORDER BY similarity_score DESC, id ASC
        """, (threshold,)).fetchall()

        logger.info(f"Found {len(candidates)} pending dedup candidates with similarity >= {threshold}")

        merged_count = 0
        archived_victims = set()
        survivors = set()

        for cand in candidates:
            cand_id = cand["id"]
            id_a = cand["resource_id_a"]
            id_b = cand["resource_id_b"]

            ra = conn.execute("SELECT id, name, status, times_triggered, times_correct FROM resources WHERE id = ?", (id_a,)).fetchone()
            rb = conn.execute("SELECT id, name, status, times_triggered, times_correct FROM resources WHERE id = ?", (id_b,)).fetchone()

            if not ra or not rb:
                logger.warning(f"Skipping candidate {cand_id}: missing resource {id_a} or {id_b}")
                continue

            # Determine survivor vs victim
            # Prefer active over archived
            if ra["status"] == "active" and rb["status"] != "active":
                survivor_id, victim_id = id_a, id_b
            elif rb["status"] == "active" and ra["status"] != "active":
                survivor_id, victim_id = id_b, id_a
            # Prefer higher times_triggered
            elif ra["times_triggered"] > rb["times_triggered"]:
                survivor_id, victim_id = id_a, id_b
            elif rb["times_triggered"] > ra["times_triggered"]:
                survivor_id, victim_id = id_b, id_a
            # Prefer higher times_correct
            elif ra["times_correct"] > rb["times_correct"]:
                survivor_id, victim_id = id_a, id_b
            elif rb["times_correct"] > ra["times_correct"]:
                survivor_id, victim_id = id_b, id_a
            # Prefer earlier creation (smaller ID) as canonical
            elif id_a <= id_b:
                survivor_id, victim_id = id_a, id_b
            else:
                survivor_id, victim_id = id_b, id_a

            # Archive the victim
            conn.execute("""
                UPDATE resources
                SET status = 'archived',
                    archive_reason = 'dedup_merge',
                    lifecycle_state = 'merged',
                    updated_at = datetime('now')
                WHERE id = ?
            """, (victim_id,))

            # Update dedup candidate
            conn.execute("""
                UPDATE dedup_candidates
                SET status = 'merged',
                    merged_into_id = ?,
                    reviewed_at = datetime('now')
                WHERE id = ?
            """, (survivor_id, cand_id))

            merged_count += 1
            archived_victims.add(victim_id)
            survivors.add(survivor_id)

        conn.commit()

        # Check remaining pending count
        remaining_pending = conn.execute(
            "SELECT COUNT(*) FROM dedup_candidates WHERE status = 'pending'"
        ).fetchone()[0]

        summary = {
            "processed": len(candidates),
            "merged": merged_count,
            "unique_victims_archived": len(archived_victims),
            "unique_survivors": len(survivors),
            "remaining_pending": remaining_pending,
        }

        logger.info(f"Drain complete: {summary}")
        return summary
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Drain pending dedup_candidates")
    parser.add_argument("--db-path", default=None, help="Path to SQLite database")
    parser.add_argument("--threshold", type=float, default=0.90, help="Similarity threshold (default: 0.90)")
    args = parser.parse_args()

    db_path = args.db_path or resolve_db_path()
    res = drain_deduplication(db_path, args.threshold)
    print(f"Drain Results: {res}")


if __name__ == "__main__":
    main()
