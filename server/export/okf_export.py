from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

_SELF = Path(__file__).resolve().parent
_SERVER = _SELF.parent
if str(_SERVER) not in sys.path:
    sys.path.insert(0, str(_SERVER))

from router_v2 import unified_route  # noqa: E402

DEFAULT_DB_PATH = str(Path(__file__).resolve().parents[2] / "db" / "nexus.db")


def _connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    con.row_factory = sqlite3.Row
    return con


def _get_resource(con: sqlite3.Connection, resource_id: int) -> dict | None:
    row = con.execute(
        "SELECT id, name, description, body_text, resource_type, cluster, file_path "
        "FROM resources WHERE id=? AND status NOT IN ('disabled','deprecated','archived')",
        (resource_id,),
    ).fetchone()
    return dict(row) if row else None


def _get_sections(con: sqlite3.Connection, resource_id: int) -> list[dict]:
    rows = con.execute(
        "SELECT heading, content FROM resource_sections "
        "WHERE resource_id=? ORDER BY section_order ASC, id ASC",
        (resource_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _format_markdown(resource: dict, sections: list[dict]) -> str:
    parts = [f"# {resource['name']}"]
    parts.append(
        f"**Type**: {resource['resource_type']}  "
        f"**Cluster**: {resource['cluster']}"
    )
    desc = (resource.get("description") or "").strip()
    if desc:
        parts.append("")
        parts.append(desc)
    parts.append("")
    parts.append("---")
    parts.append("")
    for sec in sections:
        heading = sec.get("heading") or ""
        content = (sec.get("content") or "").strip()
        if heading:
            parts.append(f"## {heading}")
        if content:
            parts.append("")
            parts.append(content)
        parts.append("")
        parts.append("---")
        parts.append("")
    return "\n".join(parts).strip()


def export_resource(
    resource_id: int,
    db_path: str = DEFAULT_DB_PATH,
    format: str = "markdown",
) -> str:
    con = _connect(db_path)
    try:
        resource = _get_resource(con, resource_id)
        if resource is None:
            raise ValueError(f"Resource {resource_id} not found or is disabled")
        sections = _get_sections(con, resource_id)
        if format == "json":
            return json.dumps(
                {
                    "id": resource["id"],
                    "name": resource["name"],
                    "description": resource.get("description"),
                    "cluster": resource["cluster"],
                    "resource_type": resource["resource_type"],
                    "file_path": resource.get("file_path"),
                    "sections": [
                        {"heading": s.get("heading"), "content": s.get("content")}
                        for s in sections
                    ],
                },
                indent=2,
                ensure_ascii=False,
            )
        return _format_markdown(resource, sections)
    finally:
        con.close()


def export_all(
    db_path: str = DEFAULT_DB_PATH,
    cluster: str | None = None,
    format: str = "json",
) -> list[dict]:
    con = _connect(db_path)
    try:
        if cluster:
            rows = con.execute(
                "SELECT id FROM resources WHERE cluster=? "
                "AND status NOT IN ('disabled','deprecated','archived')",
                (cluster,),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT id FROM resources "
                "WHERE status NOT IN ('disabled','deprecated','archived')"
            ).fetchall()
        results: list[dict] = []
        for r in rows:
            resource = _get_resource(con, r["id"])
            if resource is None:
                continue
            sections = _get_sections(con, r["id"])
            results.append(
                {
                    "id": resource["id"],
                    "name": resource["name"],
                    "description": resource.get("description"),
                    "cluster": resource["cluster"],
                    "resource_type": resource["resource_type"],
                    "sections": [
                        {"heading": s.get("heading"), "content": s.get("content")}
                        for s in sections
                    ],
                }
            )
        return results
    finally:
        con.close()


def search_export(
    query: str,
    db_path: str = DEFAULT_DB_PATH,
    top_k: int = 10,
) -> list[dict]:
    result = unified_route(query, top_k=top_k, db_path=db_path)
    merged = result.get("merged", [])
    con = _connect(db_path)
    try:
        output: list[dict] = []
        for entry in merged:
            rid = entry.get("resource_id")
            if rid is None:
                continue
            resource = _get_resource(con, rid)
            if resource is None:
                continue
            sections = _get_sections(con, rid)
            output.append(
                {
                    "id": resource["id"],
                    "name": resource["name"],
                    "description": resource.get("description"),
                    "cluster": resource["cluster"],
                    "resource_type": resource["resource_type"],
                    "file_path": resource.get("file_path"),
                    "score": entry.get("final_score", entry.get("rrf_score")),
                    "sections": [
                        {"heading": s.get("heading"), "content": s.get("content")}
                        for s in sections
                    ],
                }
            )
        return output
    finally:
        con.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Export NEXUS resources")
    sub = parser.add_subparsers(dest="command", required=True)

    p1 = sub.add_parser("resource", help="Export a single resource")
    p1.add_argument("resource_id", type=int)
    p1.add_argument("--format", choices=["markdown", "json"], default="markdown")
    p1.add_argument("--db", default=DEFAULT_DB_PATH)

    p2 = sub.add_parser("all", help="Export all resources")
    p2.add_argument("--cluster")
    p2.add_argument("--format", choices=["json"], default="json")
    p2.add_argument("--db", default=DEFAULT_DB_PATH)

    p3 = sub.add_parser("search", help="Search and export resources")
    p3.add_argument("query")
    p3.add_argument("--top-k", type=int, default=10)
    p3.add_argument("--db", default=DEFAULT_DB_PATH)

    args = parser.parse_args()

    if args.command == "resource":
        print(export_resource(args.resource_id, db_path=args.db, format=args.format))
    elif args.command == "all":
        data = export_all(db_path=args.db, cluster=args.cluster, format=args.format)
        print(json.dumps(data, indent=2, ensure_ascii=False))
    elif args.command == "search":
        data = search_export(args.query, db_path=args.db, top_k=args.top_k)
        print(json.dumps(data, indent=2, ensure_ascii=False))
