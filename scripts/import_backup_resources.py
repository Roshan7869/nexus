#!/usr/bin/env python3
"""Import the legacy resource corpus from db/nexus.db.backup into the live NEXUS DB.

The backup is the author's pre-migration snapshot: 916 resources across many repos
(ruflo 231, open-design/skills 122, everything-claude-code 105, gstack 70, llm-wiki 61,
claude-agents 43, ...). A fresh bootstrap only had the 127 resources from the two repos
absorbed in this workspace, so the corpus looked far smaller than it is.

Behaviour:
  * INSERT only — an existing live row with the same name is never overwritten.
  * `repo_name` is preserved from the backup so provenance survives.
  * Thin rows (body shorter than --min-body) are imported but counted separately, since
    a 5-character body cannot rank or retrieve meaningfully.
  * Read-only on the backup; no schema change.

    python scripts/import_backup_resources.py --dry-run
    python scripts/import_backup_resources.py --min-body 1
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.db import get_db_path  # noqa: E402

BACKUP = ROOT / "db" / "nexus.db.backup"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backup", default=str(BACKUP))
    ap.add_argument("--db", default=None)
    ap.add_argument("--min-body", type=int, default=1,
                    help="skip rows whose body_text is shorter than this (0 keeps all)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    backup = Path(args.backup)
    if not backup.exists():
        print(f"error: backup not found: {backup}", file=sys.stderr)
        return 2
    live = Path(args.db or get_db_path())

    src = sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    rows = src.execute(
        "SELECT name, resource_type, repo_name, file_path, description, body_text, "
        "       status, tier FROM resources ORDER BY name").fetchall()
    src.close()
    print(f"backup      : {backup} ({len(rows)} rows)")

    dst = sqlite3.connect(str(live), timeout=60)
    dst.execute("PRAGMA journal_mode=WAL")
    dst.execute("PRAGMA busy_timeout=60000")
    existing = {r[0] for r in dst.execute("SELECT name FROM resources")}
    print(f"live        : {live} ({len(existing)} existing)")

    inserted = skipped_existing = skipped_thin = 0
    by_type: dict[str, int] = {}
    by_repo: dict[str, int] = {}

    for r in rows:
        name = (r["name"] or "").strip()
        if not name:
            continue
        if name in existing:
            skipped_existing += 1
            continue
        body = r["body_text"] or ""
        if args.min_body and len(body) < args.min_body:
            skipped_thin += 1
            continue
        rtype = r["resource_type"] or "skill"
        tokens = max(1, len(body.split()))
        if not args.dry_run:
            dst.execute(
                """INSERT OR IGNORE INTO resources
                       (name, resource_type, repo_name, file_path, description,
                        body_text, status, tier, token_count,
                        times_triggered, times_correct, times_wrong)
                   VALUES (?,?,?,?,?,?,?,?,?,0,0,0)""",
                (name, rtype, r["repo_name"] or "unknown", r["file_path"] or "",
                 r["description"] or "", body, r["status"] or "active",
                 int(r["tier"] or 2), tokens))
        inserted += 1
        by_type[rtype] = by_type.get(rtype, 0) + 1
        repo = r["repo_name"] or "unknown"
        by_repo[repo] = by_repo.get(repo, 0) + 1

    if not args.dry_run:
        dst.commit()
    total = dst.execute("SELECT count(*) FROM resources").fetchone()[0] if not args.dry_run else None
    dst.close()

    print(f"\n{'would insert' if args.dry_run else 'inserted'} : {inserted}")
    print(f"skipped (exists) : {skipped_existing}")
    print(f"skipped (thin)   : {skipped_thin}")
    if total is not None:
        print(f"live total       : {total}")
    print("\nby type:")
    for t, n in sorted(by_type.items(), key=lambda kv: -kv[1]):
        print(f"  {t:14} {n}")
    print("\nby repo (top 12):")
    for repo, n in sorted(by_repo.items(), key=lambda kv: -kv[1])[:12]:
        print(f"  {str(repo)[:38]:40} {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
