#!/usr/bin/env python3
"""
scripts/edge_decay.py
======================
Applies nightly temporal decay to resource_edges.weight.
Decay factor: 0.995 per day (50% half-life ≈ 138 days).
Floor: 0.05 (prevents total extinction of edges).

Run via cron or nexus-maintenance.py:
    0 3 * * * cd /home/roshan/nexus && python3 scripts/edge_decay.py
"""
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "db" / "nexus.db"

DECAY_FACTOR = 0.995
FLOOR = 0.05


def run_decay():
    print(f"Edge weight decay: factor={DECAY_FACTOR}, floor={FLOOR}")
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")

    total_edges = conn.execute("SELECT COUNT(*) FROM resource_edges").fetchone()[0]
    result = conn.execute(f"""
        UPDATE resource_edges
        SET weight = MAX({FLOOR}, weight * {DECAY_FACTOR}),
            updated_at = datetime('now')
        WHERE weight > {FLOOR}
    """)
    affected = result.rowcount
    conn.commit()

    avg_w = conn.execute("SELECT AVG(weight) FROM resource_edges").fetchone()[0] or 0
    conn.close()

    print(f"  Total edges: {total_edges}")
    print(f"  Edges decayed: {affected}")
    print(f"  Average weight after decay: {avg_w:.4f}")
    return affected


if __name__ == "__main__":
    run_decay()
