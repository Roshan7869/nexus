"""Paper → NEXUS resource ingestion (ResourceDoc pipeline).

Phase 3: arXiv / OpenAlex / SSRN results become searchable ``doc`` resources
with FTS5 chunks so ``search_resources`` and routing can surface them later.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from typing import Any

from server.db import db_conn, get_db_path
from server.site_registry import slugify

logger = logging.getLogger(__name__)


def _paper_body(paper: dict, *, source: str) -> str:
    title = paper.get("title") or "Untitled"
    authors = paper.get("authors") or []
    if isinstance(authors, list) and authors and isinstance(authors[0], dict):
        authors = [a.get("name", str(a)) for a in authors]
    author_str = ", ".join(str(a) for a in authors[:8])
    abstract = paper.get("abstract") or paper.get("summary") or ""
    meta = {
        "source": source,
        "doi": paper.get("doi"),
        "arxiv_id": paper.get("arxiv_id"),
        "url": paper.get("url") or paper.get("open_access_url"),
        "year": paper.get("year"),
        "venue": paper.get("venue") or paper.get("container_title"),
    }
    lines = [
        f"# {title}",
        f"Authors: {author_str}" if author_str else "",
        f"Year: {meta.get('year')}" if meta.get("year") else "",
        f"Venue: {meta.get('venue')}" if meta.get("venue") else "",
        f"DOI: {meta.get('doi')}" if meta.get("doi") else "",
        f"URL: {meta.get('url')}" if meta.get("url") else "",
        "",
        abstract,
        "",
        f"<!-- meta: {json.dumps({k: v for k, v in meta.items() if v}, default=str)} -->",
    ]
    return "\n".join(line for line in lines if line is not None).strip()


def _resource_name(paper: dict, *, source: str) -> str:
    if paper.get("doi"):
        return f"doc:doi:{paper['doi'].lower()}"
    if paper.get("arxiv_id"):
        return f"doc:arxiv:{paper['arxiv_id'].split('v')[0]}"
    if paper.get("pmid"):
        return f"doc:pmid:{paper['pmid']}"
    url = paper.get("url") or ""
    m = re.search(r"abstract_id=(\d+)", url)
    if m:
        return f"doc:ssrn:{m.group(1)}"
    return f"doc:{source}:{slugify(paper.get('title', 'paper'))}"


def ingest_paper(
    paper: dict,
    *,
    project: str = "default",
    source: str = "research",
    cluster: str = "knowledge_wiki",
) -> dict[str, Any]:
    """Upsert one paper as a ``doc`` resource + FTS chunk."""
    if not paper or not (paper.get("title") or paper.get("abstract")):
        return {"status": "skipped", "reason": "empty paper"}

    name = _resource_name(paper, source=source)
    body = _paper_body(paper, source=source)
    desc = (paper.get("abstract") or paper.get("title") or "")[:500]
    tokens = max(1, len(body.split()))
    source_path = f"research://{project}/{name}"

    with db_conn() as conn:
        row = conn.execute(
            """
            INSERT INTO resources (
                name, resource_type, description, body_text, repo_name, file_path,
                status, tier, cluster, token_count, times_triggered, times_correct, times_wrong
            ) VALUES (?, 'doc', ?, ?, ?, ?, 'active', 2, ?, ?, 1, 0, 0)
            ON CONFLICT(name) DO UPDATE SET
                description = excluded.description,
                body_text = excluded.body_text,
                repo_name = excluded.repo_name,
                file_path = excluded.file_path,
                cluster = excluded.cluster,
                token_count = excluded.token_count,
                status = 'active',
                updated_at = datetime('now')
            RETURNING id
            """,
            (name, desc, body, project, source_path, cluster, tokens),
        ).fetchone()
        resource_id = int(row["id"])

        conn.execute(
            """
            INSERT INTO cluster_membership (cluster_name, resource_id, affinity)
            VALUES (?, ?, 0.75)
            ON CONFLICT DO NOTHING
            """,
            (cluster, resource_id),
        )

        source_id = _ensure_ingestion_source(conn, source_path, project)
        chunk_hash = hashlib.sha256(body.encode()).hexdigest()[:16]
        conn.execute("DELETE FROM ingestion_chunks WHERE source_id = ? AND resource_id = ?", (source_id, resource_id))
        cur = conn.execute(
            """
            INSERT INTO ingestion_chunks (
                source_id, resource_id, chunk_index, chunk_type, heading, content, content_hash, token_count
            ) VALUES (?, ?, 0, 'section', ?, ?, ?, ?)
            """,
            (source_id, resource_id, paper.get("title", "")[:200], body, chunk_hash, tokens),
        )
        chunk_id = int(cur.lastrowid)
        try:
            conn.execute("DELETE FROM chunks_fts WHERE rowid = ?", (chunk_id,))
            conn.execute(
                "INSERT INTO chunks_fts (rowid, content, heading) VALUES (?, ?, ?)",
                (chunk_id, body, paper.get("title", "")[:200]),
            )
        except sqlite3.OperationalError as exc:
            logger.debug("chunks_fts insert skipped: %s", exc)

    return {
        "status": "ingested",
        "resource_id": resource_id,
        "name": name,
        "project": project,
        "source": source,
    }


def ingest_papers(
    papers: list[dict],
    *,
    project: str = "default",
    source: str = "research",
) -> dict[str, Any]:
    """Batch-ingest normalized paper dicts."""
    results: list[dict[str, Any]] = []
    for paper in papers:
        if not isinstance(paper, dict):
            continue
        src = paper.get("source") or source
        results.append(ingest_paper(paper, project=project, source=src))
    ingested = sum(1 for r in results if r.get("status") == "ingested")
    return {"ingested": ingested, "total": len(results), "results": results}


def ingest_from_fused_payload(payload: dict, *, project: str = "default") -> dict[str, Any]:
    """Ingest papers from harness_smart_research fused output."""
    papers: list[dict] = []
    for row in payload.get("fused") or []:
        if isinstance(row, dict):
            papers.append(row)
    for block in payload.get("per_channel") or []:
        if not isinstance(block, dict):
            continue
        for row in block.get("results") or []:
            if isinstance(row, dict) and row.get("title"):
                row = dict(row)
                row.setdefault("source", block.get("channel", "research"))
                papers.append(row)
    # dedupe by title
    seen: set[str] = set()
    unique: list[dict] = []
    for p in papers:
        key = (p.get("doi") or p.get("arxiv_id") or p.get("title") or "").lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(p)
    return ingest_papers(unique, project=project)


def _ensure_ingestion_source(conn: sqlite3.Connection, source_path: str, project: str) -> int:
    row = conn.execute(
        "SELECT id FROM ingestion_sources WHERE source_path = ?", (source_path,)
    ).fetchone()
    if row:
        return int(row["id"])
    cur = conn.execute(
        """
        INSERT INTO ingestion_sources (source_type, source_path, content_hash, status, metadata)
        VALUES ('research_paper', ?, 'remote', 'complete', ?)
        """,
        (source_path, json.dumps({"project": project})),
    )
    return int(cur.lastrowid)