#!/usr/bin/env python3
"""Publish the ENTIRE NEXUS resource index into Obsidian and Wenlan.

Publishes every row in `resources` (skill / agent / command / hook / cli_tool / mcp),
not just skills. Nothing is invented: each note/page contains the resource's own
description and `body_text` from the DB.

Layout
------
Obsidian:  NEXUS/<Type>/<safe-name>.md          + NEXUS/<Type>/_index.md + NEXUS/_index.md
Wenlan:    page "NEXUS <type>: <name>"          + page "NEXUS <type> index"

Filenames are sanitised for Windows (``:`` ``?`` ``*`` etc. are illegal there) while
Wenlan keeps the original name in the page title.

    python scripts/publish_resources.py --dry-run
    python scripts/publish_resources.py --only obsidian
    python scripts/publish_resources.py --types skill,agent --limit 20
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.db import get_db_path  # noqa: E402

TYPE_DIRS = {
    "skill": "Skills", "agent": "Agents", "command": "Commands",
    "hook": "Hooks", "cli_tool": "CLI-Tools", "mcp": "MCP-Tools",
    "doc": "Docs", "spec": "Specs", "topic": "Topics", "craft": "Craft",
    "design_system": "Design-Systems",
}
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
MAX_BODY = 9000


def safe_name(name: str) -> str:
    """Windows-safe filename from a resource name."""
    cleaned = _ILLEGAL.sub("-", name).strip().strip(".")
    return cleaned[:120] or "unnamed"


def unique_paths(rows: list[dict], folder_for) -> dict[str, str]:
    """Map resource name -> vault-relative path, disambiguating case collisions.

    NTFS is case-insensitive, so two resources differing only by case
    (`SIMPLIFY-IGNORE` vs `simplify-ignore`) resolve to one file and the second
    write fails with HTTP 500 "File already exists". The first keeps the plain
    name; later ones get a short hash of the exact original name.
    """
    import hashlib
    from collections import Counter

    # Which names collide on a case-insensitive filesystem?
    base_counts = Counter(safe_name(r["name"]).lower() for r in rows)

    used: set[str] = set()
    out: dict[str, str] = {}
    for r in rows:
        folder = folder_for(r["resource_type"])
        base = safe_name(r["name"])
        # If any sibling differs only by case, EVERY member gets a suffix: letting
        # one keep the plain name means the other can never be written (NTFS folds
        # them, and the REST API answers 500 "File already exists").
        if base_counts[base.lower()] > 1:
            base = f"{base}-{hashlib.sha1(r['name'].encode('utf-8')).hexdigest()[:6]}"
        key = f"{folder.lower()}/{base.lower()}"
        out[r["name"]] = f"{folder}/{base}.md"
        used.add(key)
    return out


def load_resources(db_path: str | None, types: list[str] | None,
                   limit: int | None) -> list[dict]:
    con = sqlite3.connect(str(db_path or get_db_path()), timeout=60)
    con.row_factory = sqlite3.Row
    try:
        sql = ("SELECT id, name, resource_type, description, body_text, repo_name, "
               "cluster, token_count FROM resources WHERE status='active'")
        params: list = []
        if types:
            sql += " AND resource_type IN (" + ",".join("?" * len(types)) + ")"
            params += types
        sql += " ORDER BY resource_type, name"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [dict(r) for r in con.execute(sql, params)]
    finally:
        con.close()


def obsidian_note(r: dict) -> str:
    body = (r.get("body_text") or "")[:MAX_BODY].rstrip()
    meta = {"nexus_resource": r["name"], "nexus_type": r["resource_type"],
            "repo": r.get("repo_name") or "", "cluster": r.get("cluster") or "",
            "tokens": r.get("token_count") or 0, "source": "nexus"}
    front = "\n".join(f"{k}: {json.dumps(v)}" for k, v in meta.items())
    desc = (r.get("description") or "").replace("\n", " ").strip()
    return f"---\n{front}\n---\n\n> {desc}\n\n{body}\n"


def wenlan_content(r: dict) -> str:
    desc = (r.get("description") or "").strip()
    body = (r.get("body_text") or "")[:MAX_BODY].rstrip()
    return (f"Type: {r['resource_type']}\nRepo: {r.get('repo_name') or ''}\n"
            f"Cluster: {r.get('cluster') or ''}\n\n{desc}\n\n{body}\n")


def push_obsidian(rows: list[dict], dry_run: bool, log=print) -> dict:
    from server.integrations import obsidian_rest as O

    done, failed = [], []
    by_type: dict[str, list[dict]] = {}
    for r in rows:
        by_type.setdefault(r["resource_type"], []).append(r)

    paths = unique_paths(rows, lambda t: f"NEXUS/{TYPE_DIRS.get(t, t.title())}")

    for rtype, items in by_type.items():
        folder = f"NEXUS/{TYPE_DIRS.get(rtype, rtype.title())}"
        for i, r in enumerate(items, 1):
            path = paths[r["name"]]
            if dry_run:
                done.append(path)
                continue
            try:
                O.write_note(path, obsidian_note(r))
                done.append(path)
            except Exception as exc:
                failed.append(f"{r['name']}: {type(exc).__name__}: {exc}")
            if i % 100 == 0:
                log(f"    obsidian {rtype}: {i}/{len(items)}")

        index = ["---", f'title: "NEXUS {TYPE_DIRS.get(rtype, rtype)}"',
                 'source: "nexus"', "---", "",
                 f"# NEXUS {TYPE_DIRS.get(rtype, rtype)} ({len(items)})", ""]
        for r in items:
            desc = (r.get("description") or "").replace("\n", " ")[:110]
            stem = paths[r["name"]].rsplit("/", 1)[-1][:-3]
            index.append(f"- [[{stem}]] — {desc}")
        if not dry_run:
            try:
                O.write_note(f"{folder}/_index.md", "\n".join(index) + "\n")
                done.append(f"{folder}/_index.md")
            except Exception as exc:
                failed.append(f"{rtype} index: {exc}")

    master = ["---", 'title: "NEXUS Resource Index"', 'source: "nexus"', "---", "",
              f"# NEXUS Resource Index ({len(rows)} resources)", ""]
    for rtype, items in sorted(by_type.items()):
        master.append(f"## {TYPE_DIRS.get(rtype, rtype)} ({len(items)})")
        master.append(f"→ NEXUS/{TYPE_DIRS.get(rtype, rtype)}/_index.md")
        master.append("")
    if not dry_run:
        try:
            O.write_note("NEXUS/_index.md", "\n".join(master) + "\n")
            done.append("NEXUS/_index.md")
        except Exception as exc:
            failed.append(f"master index: {exc}")
    return {"written": done, "failed": failed}


def push_wenlan(rows: list[dict], dry_run: bool, log=print) -> dict:
    from server.integrations.wenlan_mcp import WenlanMCP

    done, failed = [], []
    if dry_run:
        return {"written": [f"page:{r['name']}" for r in rows], "failed": []}

    with WenlanMCP(timeout=90) as w:
        by_type: dict[str, list[dict]] = {}
        for r in rows:
            by_type.setdefault(r["resource_type"], []).append(r)
        for rtype, items in by_type.items():
            for i, r in enumerate(items, 1):
                try:
                    w.write_page(title=f"NEXUS {rtype}: {r['name']}",
                                 content=wenlan_content(r))
                    done.append(f"page:{r['name']}")
                except Exception as exc:
                    failed.append(f"{r['name']}: {type(exc).__name__}: {exc}")
                if i % 100 == 0:
                    log(f"    wenlan {rtype}: {i}/{len(items)}")
            try:
                listing = "\n".join(
                    f"- {r['name']}: {(r.get('description') or '')[:100]}" for r in items)
                w.write_page(title=f"NEXUS {rtype} index",
                             content=f"NEXUS publishes {len(items)} {rtype} resources.\n\n{listing}\n")
                done.append(f"page:NEXUS {rtype} index")
            except Exception as exc:
                failed.append(f"{rtype} index: {exc}")
    return {"written": done, "failed": failed}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--only", choices=["obsidian", "wenlan"], default="obsidian")
    ap.add_argument("--types", default=None, help="comma list, e.g. skill,agent")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    types = [t.strip() for t in args.types.split(",")] if args.types else None
    rows = load_resources(args.db, types, args.limit)
    print(f"resources to publish: {len(rows)}")
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["resource_type"]] = counts.get(r["resource_type"], 0) + 1
    print("  by type:", dict(sorted(counts.items(), key=lambda kv: -kv[1])))

    results = {}
    if args.only in (None, "obsidian"):
        print("\n-- obsidian --")
        try:
            results["obsidian"] = push_obsidian(rows, args.dry_run)
        except Exception as exc:
            results["obsidian"] = {"error": f"{type(exc).__name__}: {exc}"}
    if args.only in (None, "wenlan"):
        print("\n-- wenlan --")
        try:
            results["wenlan"] = push_wenlan(rows, args.dry_run)
        except Exception as exc:
            results["wenlan"] = {"error": f"{type(exc).__name__}: {exc}"}

    for system, res in results.items():
        print(f"\n== {system} ==")
        if "error" in res:
            print("  ERROR:", res["error"])
            continue
        print(f"  written: {len(res['written'])}")
        if res["failed"]:
            print(f"  failed : {len(res['failed'])}")
            for f in res["failed"][:6]:
                print("    !", f)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
