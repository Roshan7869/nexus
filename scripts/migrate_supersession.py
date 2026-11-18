"""Migration 023_supersession_ledger — S1 of the distraction-resistant context design.

Implements the supersession substrate (docs/designs/DISTRACTION-RESISTANT-CONTEXT.md §4):
  - resources: absorbed_at_commit / absorbed_at_date / invalidated_at (additive, instant)
  - resource_versions: per-row version history (in-place updates keep ids stable; the
    version table records each content version's validity interval)
  - note_ledger: bi-temporal ledger for experience notes (user_learnings)
  - hygiene_flags: composite-PK flag table feeding the pack-time filter
  - invalidation_audit: append-only, reversible invalidation log
  - watchdog_events: shadow-mode anomaly sink (S6, empty until that stage ships)

Backfill: absorbed_at_commit/date from each repo's current HEAD (what a fresh absorb
would have recorded). Idempotent; registers itself in schema_migrations.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

DB = Path(r"D:/nexus-data/db/nexus.db")
VERSION = "023_supersession_ledger"

SCHEMA = """
ALTER TABLE resources ADD COLUMN absorbed_at_commit TEXT;
ALTER TABLE resources ADD COLUMN absorbed_at_date TEXT;
ALTER TABLE resources ADD COLUMN invalidated_at TEXT;

CREATE INDEX IF NOT EXISTS idx_resources_validity
    ON resources(repo_name, file_path, invalidated_at);

CREATE TABLE IF NOT EXISTS resource_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_id INTEGER NOT NULL,
    body_hash TEXT NOT NULL,
    absorbed_at_commit TEXT,
    absorbed_at_date TEXT,
    valid_from TEXT NOT NULL,
    invalidated_at TEXT,
    UNIQUE(resource_id, valid_from)
);
CREATE INDEX IF NOT EXISTS idx_rv_resource ON resource_versions(resource_id, invalidated_at);

CREATE TABLE IF NOT EXISTS note_ledger (
    note_id INTEGER PRIMARY KEY,
    resource_name TEXT,
    claim_key TEXT,
    body_hash TEXT,
    valid_from TEXT NOT NULL,
    invalidated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_note_ledger_key
    ON note_ledger(resource_name, claim_key, invalidated_at);

CREATE TABLE IF NOT EXISTS hygiene_flags (
    resource_id INTEGER NOT NULL,
    flag TEXT NOT NULL,
    detail TEXT,
    found_at TEXT NOT NULL,
    resolved_at TEXT,
    PRIMARY KEY (resource_id, flag)
);

CREATE TABLE IF NOT EXISTS invalidation_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_id INTEGER NOT NULL,
    reason TEXT NOT NULL,
    invalidated_at TEXT NOT NULL,
    reopened_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_invaudit_res ON invalidation_audit(resource_id);

CREATE TABLE IF NOT EXISTS watchdog_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    route_id TEXT NOT NULL,
    anomaly TEXT NOT NULL,
    ts TEXT NOT NULL,
    detail TEXT
);
"""


def git_head(repo_root: str) -> tuple[str, str] | None:
    """(full_sha, iso_date) of a repo's HEAD, or None."""
    try:
        sha = subprocess.run(
            ["git", "-C", repo_root, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=20,
        ).stdout.strip()
        date = subprocess.run(
            ["git", "-C", repo_root, "log", "-1", "--format=%cI", "HEAD"],
            capture_output=True, text=True, timeout=20,
        ).stdout.strip()[:10]
        return (sha, date) if sha else None
    except Exception:
        return None


def already_applied(con: sqlite3.Connection) -> bool:
    row = con.execute(
        "SELECT 1 FROM schema_migrations WHERE version=?", (VERSION,)
    ).fetchone()
    return bool(row)


def columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}


def main() -> int:
    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    try:
        if already_applied(con):
            print(f"[{VERSION}] already applied — nothing to do")
            return 0

        # --- schema (ALTERs guarded so a partial prior run is tolerated) ---
        have = columns(con, "resources")
        for stmt in SCHEMA.strip().split(";"):
            stmt = stmt.strip()
            if not stmt:
                continue
            # tolerate re-added columns on a half-applied prior attempt
            if stmt.startswith("ALTER TABLE resources ADD COLUMN"):
                col = stmt.split("ADD COLUMN")[1].split()[0].strip()
                if col in have:
                    print(f"  skip (exists): {col}")
                    continue
            con.execute(stmt)
        con.commit()
        print(f"[{VERSION}] schema applied")

        # --- backfill absorbed_at_* per repo from current HEAD ---
        # (this is what a fresh absorb would have stamped; honest semantics:
        #  "version of the repo the ingest saw". Untracked/missing repos are skipped.)
        sources = con.execute(
            "SELECT source_path, metadata FROM ingestion_sources "
            "WHERE source_type='repo_resources'"
        ).fetchall()
        repo_map: dict[str, str] = {}
        for s in sources:
            try:
                meta = json.loads(s["metadata"] or "{}")
            except json.JSONDecodeError:
                meta = {}
            name = meta.get("repo")
            if name:
                repo_map[name] = s["source_path"]

        stamped = skipped = 0
        for repo_name, root in repo_map.items():
            head = git_head(root) if Path(root).exists() else None
            if not head:
                skipped += 1
                continue
            sha, date = head
            cur = con.execute(
                "UPDATE resources SET absorbed_at_commit=?, absorbed_at_date=? "
                "WHERE repo_name=? AND absorbed_at_commit IS NULL",
                (sha, date, repo_name),
            )
            stamped += cur.rowcount
        con.commit()
        print(f"[{VERSION}] backfill: {stamped} rows stamped, "
              f"{skipped} repos skipped (no HEAD on disk)")
        for repo in set() | {r[0] for r in con.execute(
                "SELECT DISTINCT repo_name FROM resources "
                "WHERE absorbed_at_commit IS NULL")}:
            print(f"  unstamped repo: {repo}")

        # --- register ---
        con.execute(
            "INSERT INTO schema_migrations(version, applied_at, description) "
            "VALUES(?, ?, ?)",
            (VERSION, time.time(),
             "Supersession ledger: absorbed_at_*/invalidated_at on resources, "
             "resource_versions, note_ledger, hygiene_flags, invalidation_audit, "
             "watchdog_events + HEAD backfill (S1, DISTRACTION-RESISTANT-CONTEXT.md)"),
        )
        con.commit()

        # --- report ---
        n_null = con.execute(
            "SELECT COUNT(*) FROM resources WHERE absorbed_at_commit IS NULL"
        ).fetchone()[0]
        total = con.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
        print(f"[{VERSION}] done: {total - n_null}/{total} resources stamped; "
              f"{n_null} unstamped (repo dirs gone or never pulled)")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
