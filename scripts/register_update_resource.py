"""Register nexus-update-all as a NEXUS resource so routing surfaces catalogue-maintenance tasks."""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from server.db import get_db_path  # noqa: E402

NAME = "nexus-update-all"
TYPE = "cli_tool"
REPO = "nexus"
PATH = "scripts/update_all.py"
DESC = ("One-trigger update of the entire NEXUS catalogue: parallel-pulls all ~109 "
        "source repos (8 workers), re-absorbs changed ones, enriches tool stubs, "
        "rebuilds the FTS index, publishes to Obsidian and commits the vault. "
        "Modes: fast (default) or full (+ re-embed, re-cluster, graph edges). "
        "MCP tools nexus_update_all / nexus_update_status.")
BODY = """# nexus-update-all

Update every NEXUS resource to its latest upstream version in one trigger.

## What it does

`scripts/update_all.py` swarm-pulls ALL workspace source repos in parallel
(8 workers, `git pull --ff-only`), while the old registry-based path only
updated 6 tracked sources. Protected repos (uncommitted local work, e.g.
`nexus` itself) are skipped, never pulled.

Stages: pull -> absorb changed -> enrich -> fts -> publish -> commit.
`--mode full` adds: embed (--force) + seed_clusters + build_edges (heavy CPU).

## Usage

    python scripts/update_all.py --check        # read-only freshness report
    python scripts/update_all.py                # fast update
    python scripts/update_all.py --mode full     # + embeddings + re-cluster

MCP (any agent session): `nexus_update_all(mode="fast")` then poll
`nexus_update_status()`. Progress is written to
`D:/nexus-data/update_status.json` after every stage, with per-repo pull
results visible even mid-run.
"""


def main() -> int:
    con = sqlite3.connect(get_db_path(), timeout=30)
    con.row_factory = sqlite3.Row
    existing = con.execute("SELECT id FROM resources WHERE name=? AND resource_type=?",
                           (NAME, TYPE)).fetchone()
    tokens = max(1, len(BODY.split()))
    if existing:
        con.execute("UPDATE resources SET description=?, body_text=?, token_count=?, "
                    "file_path=?, updated_at=datetime('now') WHERE id=?",
                    (DESC, BODY, tokens, PATH, existing["id"]))
        rid = existing["id"]
        action = "updated"
    else:
        cur = con.execute(
            """INSERT INTO resources
                   (name, resource_type, repo_name, file_path, description, body_text,
                    status, tier, token_count, times_triggered, times_correct, times_wrong)
               VALUES (?,?,?,?,?,?, 'active', 1, ?, 0, 0, 0)""",
            (NAME, TYPE, REPO, PATH, DESC, BODY, tokens))
        rid = cur.lastrowid
        action = "inserted"
    con.execute("INSERT OR IGNORE INTO cluster_membership (resource_id, cluster_name, affinity) "
               "VALUES (?, 'general', 0.6)", (rid,))
    con.commit()
    row = con.execute("SELECT name, resource_type, cluster FROM resources WHERE id=?",
                     (rid,)).fetchone()
    total = con.execute("SELECT count(*) FROM resources").fetchone()[0]
    con.close()
    print(f"{action} resource id={rid}: {row['name']} ({row['resource_type']})")
    print(f"total resources: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
