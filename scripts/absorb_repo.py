#!/usr/bin/env python3
"""Absorb a repository's skills/agents/commands into the NEXUS resources table.

    python scripts/absorb_repo.py ../elephant-goldfish-model
    python scripts/absorb_repo.py ../elephant-goldfish-model --dry-run
    python scripts/absorb_repo.py ../elephant-goldfish-model --cluster architect --json

Absorption registers resources and writes retrieval chunks (ingestion_chunks +
chunks_fts) so both routing and FTS5 search can find them. It does not score,
verify, or assign outcomes to anything.

The target must already be on disk (clone it first); a URL is rejected rather
than cloned, so the clone options stay under the caller's control.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.db import get_db_path  # noqa: E402
from server.ingest.repo_resources import absorb, discover, parse_frontmatter  # noqa: E402

# Honour NEXUS_DB_PATH like every other maintenance script; the previous
# hardcoded repo-relative default silently ignored it and wrote to a second
# database that the live server never reads.
DEFAULT_DB = get_db_path()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo", help="path to an existing local clone")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--repo-name", default=None,
                    help="override the repo label stored in resources.repo_name")
    ap.add_argument("--cluster", default=None,
                    help="cluster name to attach resources to (optional)")
    ap.add_argument("--tier", type=int, default=2, choices=[1, 2, 3])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    raw = args.repo
    if "://" in raw or raw.startswith("git@"):
        print(f"error: {raw!r} looks like a remote URL.\n"
              f"       Clone it first, then pass the local path:\n"
              f"       git clone <url> && python scripts/absorb_repo.py <dir>",
              file=sys.stderr)
        return 2

    repo = Path(raw).expanduser().resolve()
    if not repo.is_dir():
        print(f"error: not a directory: {repo}", file=sys.stderr)
        return 2

    db = Path(args.db).expanduser().resolve()
    if not db.exists():
        print(f"error: database not found: {db}\n"
              f"       run: python scripts/bootstrap_nexus_db.py", file=sys.stderr)
        return 2

    summary = absorb(repo, db, repo_name=args.repo_name, cluster=args.cluster,
                     tier=args.tier, dry_run=args.dry_run)

    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 0

    print(f"repo        : {summary['repo']}")
    print(f"root        : {summary['repo_root']}")
    print(f"database    : {summary['db']}")
    print(f"discovered  : {summary['discovered']}  "
          f"({', '.join(f'{k}={v}' for k, v in sorted(summary['by_type'].items()))})")
    if summary["dry_run"]:
        print("\n[dry run] would register:")
        for name in summary["names"]:
            print(f"  {name}")
        return 0

    print(f"inserted    : {summary['inserted']}")
    print(f"updated     : {summary['updated']}")
    print(f"unchanged   : {summary['unchanged']}")
    if summary["conflicts"]:
        print(f"conflicts   : {len(summary['conflicts'])} (left untouched)")
        for c in summary["conflicts"]:
            print(f"  {c['name']} already owned by {c['owner']!r}")
    if summary["errors"]:
        print(f"errors      : {len(summary['errors'])}")
        for e in summary["errors"]:
            print(f"  {e}")
    print("\nregistered:")
    for name in summary["names"]:
        print(f"  {name}")
    return 0 if not summary["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
