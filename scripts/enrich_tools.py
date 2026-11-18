#!/usr/bin/env python3
"""Enrich tool stubs (mcp / cli_tool / hook) with their real definitions.

The legacy snapshot recorded these 130 records with only a name and a one-line
description — `tokens: 2`, so the vault entry for `clickhouse` was literally its own
title. Skills and commands were fine; tools were names only, which made "how do I
install clickhouse MCP" unanswerable from any interface.

Sources, in order:
  1. the resource's own `file_path`, rebased onto a local clone
  2. a directory path → its README / index / contained markdown
  3. for `mcp` — the owning repo's `.mcp.json` / `mcp.json` /
     `claude_desktop_config.json` block for that server name
  4. for `cli_tool` — the script plus any usage/help text found inside it

    python scripts/enrich_tools.py --dry-run
    python scripts/enrich_tools.py --types mcp,cli_tool
    python scripts/enrich_tools.py            # all tool types
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = ROOT.parent
sys.path.insert(0, str(ROOT))

from server.db import get_db_path  # noqa: E402

TOOL_TYPES = ("mcp", "cli_tool", "hook")

CLONES = {
    "get-shit-done": "get-shit-done",
    "elephant-goldfish-model": "elephant-goldfish-model",
    "agent-skills": "agent-skills-own",
    "awesome-claude-code": "awesome-claude-code",
    "browserbase-skills": "browserbase-skills",
    "ruflo": "ruflo",
    "claude-helpers": "ruflo",
    "gstack": "gstack",
    "everything-claude-code": "everything-claude-code",
    "code-review-graph": "code-review-graph",
    "my-brain-is-full-crew": "My-Brain-Is-Full-Crew",
    "agent-orchestrator": "agent-orchestrator",
    "codeskeleton": "codeskeleton",
    "algotrading": "algotrading",
    "skills": "skills",
    "kaari-weaves": "kaari-weaves",
    "Aashiyaana": "Aashiyaana",
}

PREFIXES = [
    r"^/home/roshan/wiki/[^/]+/",
    r"^/home/roshan/Downloads/[^/]+/",
    r"^~/wiki/[^/]+/",
    r"^/home/roshan/",
    r"^~/",
]

MCP_CONFIG_FILES = (".mcp.json", "mcp.json", "claude_desktop_config.json",
                    ".cursor/mcp.json", ".vscode/mcp.json", ".claude/mcp.json",
                    "mcp-configs/mcp-servers.json", "mcp-servers.json")

MAX_BODY = 9000


def rebase(file_path: str, clone: Path) -> Path | None:
    tail = file_path or ""
    for p in PREFIXES:
        tail = re.sub(p, "", tail)
    if not tail:
        return None
    cand = clone / tail.rstrip("/")
    if cand.exists():
        return cand
    # fall back to suffix search by name (including .js -> .cjs/.mjs variants)
    name = Path(tail.rstrip("/")).name
    if name:
        hits = [h for h in clone.rglob(name) if ".git" not in h.parts and "node_modules" not in h.parts]
        if not hits and Path(name).suffix == ".js":
            stem = Path(name).stem
            for ext in (".cjs", ".mjs", ".ts"):
                hits = [h for h in clone.rglob(stem + ext) if ".git" not in h.parts and "node_modules" not in h.parts]
                if hits:
                    break
        if hits:
            hits.sort(key=lambda h: len(h.parts))
            return hits[0]
    return None


def read_any(p: Path) -> str:
    if p.is_dir():
        parts: list[str] = []
        for cand in ("README.md", "README", "index.md", "SKILL.md"):
            f = p / cand
            if f.is_file():
                parts.append(f.read_text(encoding="utf-8", errors="replace"))
                break
        if not parts:
            for f in sorted(p.glob("*.md"))[:3]:
                parts.append(f.read_text(encoding="utf-8", errors="replace"))
        if not parts:
            listing = sorted(x.name for x in p.iterdir() if x.name != ".git")[:20]
            parts.append(f"# Directory {p.name}\n\n" + "\n".join(f"- {n}" for n in listing))
        return "\n\n".join(parts)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if p.suffix in (".sh", ".bash", ".py", ".js", ".ts", ".tsx", ".json"):
        usage = re.findall(r"(?:#|//|\*)\s*(?:Usage|usage|USAGE)[:\s].{0,200}", text)
        head = text[:MAX_BODY]
        return head + ("\n\n" + "\n".join(usage[:5]) if usage else "")
    return text[:MAX_BODY]


def find_mcp_block(clone: Path, name: str) -> str | None:
    """Extract one server's config from any MCP config file in the repo.

    Repos move these files around (`.mcp.json` -> `.claude/mcp.json`,
    `mcp-config/` -> `mcp-configs/mcp-servers.json`), so the known list is tried
    first and then anything whose filename mentions MCP.
    """
    candidates: list[Path] = [clone / rel for rel in MCP_CONFIG_FILES if (clone / rel).is_file()]
    for pattern in ("*mcp*.json", "*mcp*.yaml", "*mcp*.yml"):
        try:
            candidates.extend(p for p in clone.rglob(pattern)
                              if p.is_file() and ".git" not in p.parts
                              and "node_modules" not in p.parts)
        except OSError:
            pass

    seen: set[Path] = set()
    for p in candidates:
        if p in seen:
            continue
        seen.add(p)
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            data = json.loads(text)
        except (OSError, ValueError):
            continue
        servers = data.get("mcpServers") or data.get("servers") or {}
        if not isinstance(servers, dict):
            continue
        for key, block in servers.items():
            if key.lower() == name.lower() or name.lower() in key.lower():
                rel = p.relative_to(clone).as_posix()
                return (f"# MCP server: {key}\n\n"
                        f"Source: `{rel}`\n\n```json\n"
                        f"{json.dumps(block, indent=2)[:MAX_BODY]}\n```\n")
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--types", default=",".join(TOOL_TYPES))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    types = [t.strip() for t in args.types.split(",") if t.strip()]
    db = Path(args.db or get_db_path())
    con = sqlite3.connect(str(db), timeout=60)
    con.row_factory = sqlite3.Row
    rows = list(con.execute(
        "SELECT id, name, resource_type, repo_name, file_path, description "
        "FROM resources WHERE resource_type IN (" + ",".join("?" * len(types)) + ") "
        "AND length(coalesce(body_text,'')) < 300 ORDER BY resource_type, name", types))
    if args.limit:
        rows = rows[:args.limit]
    print(f"db: {db}\ntool stubs: {len(rows)}")

    enriched = skipped = 0
    for r in rows:
        dirname = CLONES.get(r["repo_name"])
        clone = WORKSPACE / dirname if dirname else None
        if not clone or not clone.is_dir():
            skipped += 1
            continue

        body = ""
        if r["resource_type"] == "mcp":
            block = find_mcp_block(clone, r["name"])
            if block:
                body = block
        if not body:
            target = rebase(r["file_path"], clone)
            if not target:
                skipped += 1
                continue
            body = read_any(target)
        if not body or len(body) < 120:
            skipped += 1
            continue

        header = (f"# {r['name']}\n\n"
                  f"Type: {r['resource_type']} · Repo: {r['repo_name']} · "
                  f"Source: `{r['file_path']}`\n\n")
        if args.dry_run:
            enriched += 1
            if enriched <= 5:
                print(f"  would enrich {r['name'][:30]:32} -> {len(body)} B")
            continue
        full = (header + body)[:MAX_BODY]
        con.execute(
            "UPDATE resources SET body_text=?, token_count=?, updated_at=datetime('now') "
            "WHERE id=?",
            (full, max(1, len(full.split())), r["id"]))
        enriched += 1

    if not args.dry_run:
        con.commit()
    total_now = con.execute(
        "SELECT count(*) FROM resources WHERE resource_type IN (" +
        ",".join("?" * len(types)) + ") AND length(coalesce(body_text,'')) >= 300",
        types).fetchone()[0]
    con.close()

    print(f"\n{'would enrich' if args.dry_run else 'enriched'}: {enriched}")
    print(f"skipped (no clone / no source): {skipped}")
    if not args.dry_run:
        print(f"{'/'.join(types)} records with >=300 B of body: {total_now}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
