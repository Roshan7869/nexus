#!/usr/bin/env python3
"""
Audit NEXUS resource ingestibility, discoverability, and triggerability.

Definitions used by this script:
  - ingestible: active resources with non-empty name/description/body_text/embedding
  - discoverable_default: resources included in universal_route() default type filter
  - triggerable_nexus: FastMCP-decorated tools exposed by NEXUS server files
  - indexed_mcp: DB rows typed as resource_type='mcp'
  - triggerable_artifacts: DB rows that may represent executable assets
    (mcp, command, cli_tool) but are not guaranteed to be mounted live
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "db" / "nexus.db"
SERVER_FILES = [
    ROOT / "server" / "nexus-mcp.py",
    ROOT / "server" / "nexus-mcp-v2.py",
]
DISCOVERABLE_DEFAULT_TYPES = (
    "skill",
    "command",
    "agent",
    "hook",
    "design_system",
    "doc",
)
TRIGGERABLE_ARTIFACT_TYPES = ("mcp", "command", "cli_tool")


def parse_fastmcp_tools(path: Path) -> list[str]:
    text = path.read_text()
    return re.findall(r"@mcp\.tool\(\)\s*\ndef\s+([A-Za-z0-9_]+)\(", text)


def fetch_scalar(conn: sqlite3.Connection, query: str, params: tuple = ()) -> int:
    return int(conn.execute(query, params).fetchone()[0])


def gather_report() -> dict:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    total_resources = fetch_scalar(
        conn, "select count(*) from resources where status='active'"
    )
    fully_ingested = fetch_scalar(
        conn,
        """
        select count(*)
        from resources
        where status='active'
          and trim(name) != ''
          and trim(description) != ''
          and trim(body_text) != ''
          and embedding is not null
          and length(embedding) > 0
        """,
    )
    discoverable_default = fetch_scalar(
        conn,
        f"""
        select count(*)
        from resources
        where status='active'
          and resource_type in ({",".join("?" for _ in DISCOVERABLE_DEFAULT_TYPES)})
        """,
        DISCOVERABLE_DEFAULT_TYPES,
    )
    indexed_mcp = fetch_scalar(
        conn,
        "select count(*) from resources where status='active' and resource_type='mcp'",
    )
    triggerable_artifacts = fetch_scalar(
        conn,
        f"""
        select count(*)
        from resources
        where status='active'
          and resource_type in ({",".join("?" for _ in TRIGGERABLE_ARTIFACT_TYPES)})
        """,
        TRIGGERABLE_ARTIFACT_TYPES,
    )

    counts_by_type = {
        row["resource_type"]: row["count"]
        for row in conn.execute(
            """
            select resource_type, count(*) as count
            from resources
            where status='active'
            group by resource_type
            order by count(*) desc
            """
        )
    }
    mcp_by_repo = [
        {"repo_name": row["repo_name"], "count": row["count"]}
        for row in conn.execute(
            """
            select repo_name, count(*) as count
            from resources
            where status='active' and resource_type='mcp'
            group by repo_name
            order by count(*) desc, repo_name asc
            """
        )
    ]
    sample_mcp = [
        {
            "name": row["name"],
            "repo_name": row["repo_name"],
            "description": (row["description"] or "")[:120],
        }
        for row in conn.execute(
            """
            select name, repo_name, description
            from resources
            where status='active' and resource_type='mcp'
            order by repo_name asc, name asc
            limit 15
            """
        )
    ]
    conn.close()

    nexus_servers = {}
    for path in SERVER_FILES:
        nexus_servers[str(path.relative_to(ROOT))] = parse_fastmcp_tools(path)

    return {
        "paths": {
            "root": str(ROOT),
            "db": str(DB_PATH),
        },
        "totals": {
            "active_resources": total_resources,
            "fully_ingested": fully_ingested,
            "discoverable_default": discoverable_default,
            "excluded_from_default_discovery": total_resources - discoverable_default,
            "indexed_mcp": indexed_mcp,
            "triggerable_artifacts": triggerable_artifacts,
        },
        "counts_by_type": counts_by_type,
        "discoverability": {
            "default_types": list(DISCOVERABLE_DEFAULT_TYPES),
            "excluded_types": sorted(
                set(counts_by_type) - set(DISCOVERABLE_DEFAULT_TYPES)
            ),
        },
        "nexus_fastmcp_servers": {
            rel_path: {
                "tool_count": len(tool_names),
                "tools": tool_names,
            }
            for rel_path, tool_names in nexus_servers.items()
        },
        "mcp_index": {
            "by_repo": mcp_by_repo,
            "sample": sample_mcp,
        },
        "assessment": {
            "note": (
                "Indexed resources are not the same as callable tools. "
                "Most of the 1686 entries are skills, commands, docs, agents, "
                "and design-system artifacts. Only a small subset is exposed as "
                "live FastMCP tools by NEXUS."
            )
        },
    }


def print_text_report(report: dict) -> None:
    totals = report["totals"]
    print("NEXUS Tool Access Audit")
    print("=======================")
    print(f"Active resources: {totals['active_resources']}")
    print(f"Fully ingested: {totals['fully_ingested']}")
    print(f"Discoverable by default universal routing: {totals['discoverable_default']}")
    print(
        f"Excluded from default discovery: {totals['excluded_from_default_discovery']}"
    )
    print(f"Indexed MCP rows: {totals['indexed_mcp']}")
    print(f"Potentially triggerable artifacts: {totals['triggerable_artifacts']}")
    print()
    print("Counts by type")
    for name, count in report["counts_by_type"].items():
        print(f"  - {name}: {count}")
    print()
    print("FastMCP-exposed NEXUS tools")
    for rel_path, info in report["nexus_fastmcp_servers"].items():
        print(f"  - {rel_path}: {info['tool_count']}")
        for tool in info["tools"]:
            print(f"    * {tool}")
    print()
    print("MCP rows by repo")
    for row in report["mcp_index"]["by_repo"]:
        print(f"  - {row['repo_name']}: {row['count']}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args()

    report = gather_report()
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_text_report(report)


if __name__ == "__main__":
    main()
