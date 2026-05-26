#!/usr/bin/env python3
"""Repair canonical service-layer resource assignments in nexus.db."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

NEXUS_ROOT = Path.home() / "nexus"
if str(NEXUS_ROOT) not in sys.path:
    sys.path.insert(0, str(NEXUS_ROOT))

from server.service_layer import RESOURCE_CLUSTER_OVERRIDES


DB_PATH = Path.home() / "nexus" / "db" / "nexus.db"


def main() -> None:
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    updated = 0
    missing = []

    for name, cluster in RESOURCE_CLUSTER_OVERRIDES.items():
        row = conn.execute(
            "SELECT id, cluster FROM resources WHERE name=?",
            (name,),
        ).fetchone()
        if not row:
            missing.append(name)
            continue
        if row["cluster"] != cluster:
            conn.execute(
                "UPDATE resources SET cluster=? WHERE id=?",
                (cluster, row["id"]),
            )
            updated += 1

    conn.commit()
    print(f"updated={updated}")
    if missing:
        print("missing=" + ",".join(missing))


if __name__ == "__main__":
    main()
