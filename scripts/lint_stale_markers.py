"""S2 stale-marker linter — CLI wrapper over server.ingest.stale_lint.

Runs the ingestion-time scan over active resources; safe to re-run (flags and
tracker lookups are cached/upserted). Called by the weekly refresh after
absorb, or standalone:

    python scripts/lint_stale_markers.py [--budget 200] [--asof-days 180]
                                         [--stub-chars 40] [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--budget", type=int, default=200)
    ap.add_argument("--asof-days", type=int, default=180)
    ap.add_argument("--stub-chars", type=int, default=40)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    import sqlite3

    def _db_path() -> str:
        if args.db:
            return args.db
        try:
            from server.db import get_db_path
            return str(get_db_path())
        except Exception:
            return r"D:/nexus-data/db/nexus.db"

    from server.ingest.stale_lint import GhResolver, scan_resources

    con = sqlite3.connect(_db_path(), timeout=60)
    con.row_factory = sqlite3.Row
    resolver = GhResolver(budget=args.budget)
    if args.dry_run:
        # no gh calls in dry-run: everything unresolvable becomes pending
        resolver.budget = 0
    summary = scan_resources(
        con, resolver, asof_days=args.asof_days, stub_chars=args.stub_chars)
    con.close()

    print(f"scanned            : {summary['scanned']}")
    print(f"issue URLs         : {summary['issue_urls']} "
          f"(tracker calls {summary['tracker_calls']}, cache hits {summary['cache_hits']})")
    print(f"TODO/FIXME refs    : {summary['todo_refs']}")
    print(f"plain TODOs        : {summary['plain_todos']}")
    print(f"aged date-claims   : {summary['asof_old']}")
    print(f"stubs              : {summary['stubs']}")
    print(f"resolved open      : {summary['resolved_open']}")
    print(f"resolved CLOSED    : {summary['resolved_closed']}  "
          f"-> pack-excluded + curation queue")
    print(f"pending (tracked)  : {summary['pending']}  -> NOT pack-excluded")
    if summary.get("resolver_failures"):
        print(f"resolver failures  : {summary['resolver_failures'][:5]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
