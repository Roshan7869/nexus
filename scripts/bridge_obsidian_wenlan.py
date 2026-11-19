#!/usr/bin/env python3
"""RETIRED for the Wenlan half (2026-11-19) — Wenlan was uninstalled.

The Obsidian half still works; `publish_resources.py --only obsidian` is the
supported publisher and `refresh_nexus.py` drives it. Do not run this script's
Wenlan path — the binary is gone. See docs/plans/W6-WENLAN-RETIREMENT.md.

Configure NEXUS skills into Obsidian and Wenlan.

Reads NEXUS `resources` (resource_type='skill') from the live DB and publishes each
one into both knowledge systems:

* **Obsidian** — one note per skill under ``NEXUS/Skills/<name>.md`` with YAML
  frontmatter + the skill body, plus ``NEXUS/Skills/_index.md``.
* **Wenlan** — one wiki page per skill via the `write_page` MCP tool (falls back to
  `capture` when the Space requires sources), plus a summary page.

Nothing is invented: the body written is the resource's own `body_text` from the DB.
Idempotent: notes/pages are overwritten by title, so re-running refreshes them.

    python scripts/bridge_obsidian_wenlan.py --dry-run
    python scripts/bridge_obsidian_wenlan.py
    python scripts/bridge_obsidian_wenlan.py --limit 3 --only obsidian
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.db import get_db_path  # noqa: E402

OBSIDIAN_DIR = "NEXUS/Skills"
MAX_BODY = 12000


def load_skills(db_path: str | None = None, limit: int | None = None) -> list[dict]:
    con = sqlite3.connect(str(db_path or get_db_path()), timeout=30)
    con.row_factory = sqlite3.Row
    try:
        sql = ("SELECT name, description, body_text, cluster, repo_name, token_count "
               "FROM resources WHERE resource_type='skill' AND body_text != '' "
               "ORDER BY name")
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [dict(r) for r in con.execute(sql)]
    finally:
        con.close()


def to_markdown(skill: dict) -> str:
    """Obsidian note: frontmatter + the skill's own body."""
    body = (skill.get("body_text") or "")[:MAX_BODY].rstrip()
    meta = {
        "nexus_resource": skill["name"],
        "nexus_type": "skill",
        "repo": skill.get("repo_name") or "",
        "cluster": skill.get("cluster") or "",
        "tokens": skill.get("token_count") or 0,
        "source": "nexus",
    }
    front = "\n".join(f"{k}: {json.dumps(v)}" for k, v in meta.items())
    desc = (skill.get("description") or "").replace("\n", " ").strip()
    return f"---\n{front}\n---\n\n> {desc}\n\n{body}\n"


def to_plain(skill: dict) -> str:
    """Wenlan page content: no YAML frontmatter, title + body."""
    body = (skill.get("body_text") or "")[:MAX_BODY].rstrip()
    desc = (skill.get("description") or "").strip()
    return f"{desc}\n\n{body}\n"


def push_obsidian(skills: list[dict], dry_run: bool) -> dict:
    from server.integrations import obsidian_rest as O

    written, failed = [], []
    for s in skills:
        path = f"{OBSIDIAN_DIR}/{s['name']}.md"
        if dry_run:
            written.append(path)
            continue
        try:
            O.write_note(path, to_markdown(s))
            written.append(path)
        except Exception as exc:
            failed.append(f"{s['name']}: {type(exc).__name__}: {exc}")

    index = ["---", 'title: "NEXUS Skills"', 'source: "nexus"', "---", "",
             f"# NEXUS Skills ({len(skills)})", "",
             "Published from the NEXUS resource index.", ""]
    for s in skills:
        desc = (s.get("description") or "").replace("\n", " ")[:120]
        index.append(f"- [[{s['name']}]] — {desc}")
    if not dry_run:
        try:
            O.write_note(f"{OBSIDIAN_DIR}/_index.md", "\n".join(index) + "\n")
            written.append(f"{OBSIDIAN_DIR}/_index.md")
        except Exception as exc:
            failed.append(f"_index: {exc}")
    return {"written": written, "failed": failed}


def push_wenlan(skills: list[dict], dry_run: bool) -> dict:
    from server.integrations.wenlan_mcp import WenlanMCP

    written, failed = [], []
    if dry_run:
        return {"written": [f"page:{s['name']}" for s in skills], "failed": []}

    with WenlanMCP(timeout=60) as w:
        for s in skills:
            try:
                w.write_page(title=f"NEXUS skill: {s['name']}",
                             content=to_plain(s))
                written.append(f"page:{s['name']}")
                continue
            except Exception as exc:
                # Fall back to a memory capture when a page needs a Space/sources.
                try:
                    w.capture(content=f"NEXUS skill {s['name']}: "
                                      f"{(s.get('description') or '')[:200]}",
                              title=f"NEXUS skill {s['name']}")
                    written.append(f"capture:{s['name']}")
                    continue
                except Exception as exc2:
                    failed.append(f"{s['name']}: page={type(exc).__name__} capture={type(exc2).__name__}")

        # one summary page
        try:
            listing = "\n".join(f"- {s['name']}: {(s.get('description') or '')[:100]}"
                                for s in skills)
            w.write_page(title="NEXUS skills index",
                         content=f"NEXUS publishes {len(skills)} skills.\n\n{listing}\n")
            written.append("page:NEXUS skills index")
        except Exception as exc:
            failed.append(f"index: {exc}")
    return {"written": written, "failed": failed}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--only", choices=["obsidian", "wenlan"], default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    skills = load_skills(args.db, args.limit)
    print(f"skills found: {len(skills)}")
    if not skills:
        return 1

    results = {}
    if args.only in (None, "obsidian"):
        try:
            results["obsidian"] = push_obsidian(skills, args.dry_run)
        except Exception as exc:
            results["obsidian"] = {"error": f"{type(exc).__name__}: {exc}"}
    if args.only in (None, "wenlan"):
        try:
            results["wenlan"] = push_wenlan(skills, args.dry_run)
        except Exception as exc:
            results["wenlan"] = {"error": f"{type(exc).__name__}: {exc}"}

    for system, res in results.items():
        print(f"\n== {system} ==")
        if "error" in res:
            print("  ERROR:", res["error"])
            continue
        print(f"  written: {len(res['written'])}")
        for w_ in res["written"][:20]:
            print("    +", w_)
        if res["failed"]:
            print(f"  failed : {len(res['failed'])}")
            for f in res["failed"][:8]:
                print("    !", f)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
