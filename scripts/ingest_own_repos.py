#!/usr/bin/env python3
"""Clone and absorb the account's own repos that carry agent resources.

Driven by `D:\\nexus-data\\repo_resource_scan.json` (produced by a git-trees scan of
every repo). Repos are ranked by (resources / size) so we clone value first, and
oversized repos are skipped by default rather than silently blowing up disk.

    python scripts/ingest_own_repos.py --list
    python scripts/ingest_own_repos.py --batch 8 --max-mb 60
    python scripts/ingest_own_repos.py --repos antigravity-awesome-skills
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = ROOT.parent
sys.path.insert(0, str(ROOT))

from server.db import get_db_path  # noqa: E402
from server.ingest.repo_resources import absorb, discover  # noqa: E402

GH = r"C:\Program Files\GitHub CLI\gh.exe"
SCAN = Path(r"D:\nexus-data\repo_resource_scan.json")
OWNER = "Roshan7869"


def load_scan() -> list[dict]:
    return json.loads(SCAN.read_text(encoding="utf-8"))


def already(repo_name: str) -> bool:
    import sqlite3
    con = sqlite3.connect(f"file:{get_db_path()}?mode=ro", uri=True)
    try:
        return con.execute("SELECT 1 FROM resources WHERE repo_name=? LIMIT 1",
                           (repo_name,)).fetchone() is not None
    finally:
        con.close()


def clone(name: str, dest: Path) -> tuple[bool, str]:
    if dest.exists():
        return True, "exists"
    p = subprocess.run([GH, "repo", "clone", f"{OWNER}/{name}", str(dest), "--", "--depth", "1"],
                       capture_output=True, text=True, timeout=1800)
    return p.returncode == 0, (p.stderr or "").strip()[:160]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--max-mb", type=int, default=60, help="skip repos larger than this")
    ap.add_argument("--repos", default=None, help="comma list of exact repo names")
    ap.add_argument("--include-large", action="store_true")
    args = ap.parse_args()

    scan = load_scan()
    ranked = sorted(scan, key=lambda x: -(x["total"] / max(1, x["kb"])))
    if args.repos:
        want = {r.strip() for r in args.repos.split(",") if r.strip()}
        ranked = [x for x in scan if x["name"] in want]
    if not args.include_large:
        ranked = [x for x in ranked if x["kb"] <= args.max_mb * 1024]

    if args.list:
        print(f"{'repo':40} {'tot':>5} {'MB':>7} {'done':>5}")
        for x in ranked[:40]:
            print(f"{x['name'][:38]:40} {x['total']:>5} {x['kb']/1024:>7.0f} "
                  f"{'yes' if already(x['name']) else 'no':>5}")
        print(f"\n{len(ranked)} eligible repos")
        return 0

    todo = [x for x in ranked if not already(x["name"])][:args.batch]
    if not todo:
        print("nothing to ingest")
        return 0

    total_new = total_upd = 0
    for x in todo:
        name = x["name"]
        dest = WORKSPACE / name
        ok, msg = clone(name, dest)
        if not ok:
            print(f"[fail]  {name}: {msg}")
            continue
        found = discover(dest)
        if not found:
            print(f"[none]  {name}: {x['total']} paths in scan but discover() found none")
            continue
        s = absorb(dest, get_db_path(), repo_name=name)
        total_new += s["inserted"]
        total_upd += s["updated"]
        print(f"[absorb] {name[:34]:36} scan={x['total']:>5} discovered={s['discovered']:>5} "
              f"+{s['inserted']:>4} new, {s['updated']:>4} upd, conflicts={len(s['conflicts'])}")

    print(f"\nbatch done: +{total_new} new, {total_upd} updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
