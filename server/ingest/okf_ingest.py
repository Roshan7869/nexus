from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path

from .base import Chunk, content_hash, estimate_tokens

DEFAULT_DB_PATH = str(Path(__file__).resolve().parents[2] / "db" / "nexus.db")

SECTION_RE = re.compile(r"^(#{2,3}) .+$", re.MULTILINE)
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    con.row_factory = sqlite3.Row
    return con


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    raw = m.group(1)
    body = text[m.end():]
    meta: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            meta[key.strip()] = val.strip()
    return meta, body.strip()


def _split_sections(body: str) -> list[dict]:
    matches = list(SECTION_RE.finditer(body))
    if not matches:
        return [{"heading": None, "content": body.strip()}]
    sections: list[dict] = []
    for i, m in enumerate(matches):
        heading = m.group().strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        content = body[start:end].strip()
        sections.append({"heading": heading, "content": content})
    return sections


def _get_or_create_source(con: sqlite3.Connection, file_path: str) -> int:
    row = con.execute(
        "SELECT id FROM ingestion_sources WHERE source_path=? AND source_type='okf'",
        (file_path,),
    ).fetchone()
    if row:
        sid = int(row["id"])
        con.execute(
            "UPDATE ingestion_sources SET content_hash=?, status='ingesting', "
            "updated_at=datetime('now') WHERE id=?",
            (content_hash(file_path), sid),
        )
        con.execute("DELETE FROM chunks_fts WHERE source_id=?", (sid,))
        con.execute("DELETE FROM ingestion_chunks WHERE source_id=?", (sid,))
        return sid
    cur = con.execute(
        "INSERT INTO ingestion_sources(source_type, source_path, content_hash, "
        "status, total_chunks, total_tokens) VALUES('okf',?,?,?,0,0)",
        (file_path, content_hash(file_path), "ingesting"),
    )
    return int(cur.lastrowid)


def ingest_okf(
    resource_id: int,
    file_path: str,
    db_path: str = DEFAULT_DB_PATH,
) -> dict:
    t0 = time.perf_counter()
    p = Path(file_path)
    if not p.is_file():
        raise FileNotFoundError(f"SKILL.md not found: {file_path}")

    raw_text = p.read_text(encoding="utf-8")
    frontmatter, body = _parse_frontmatter(raw_text)
    name = frontmatter.get("name", p.stem)
    description = frontmatter.get("description", "")

    sections = _split_sections(body)

    con = _connect(db_path)
    sid = _get_or_create_source(con, file_path)

    try:
        total_tokens = 0
        fts_rows: list[tuple] = []

        def flush() -> None:
            nonlocal fts_rows
            if not fts_rows:
                return
            con.executemany(
                "INSERT INTO chunks_fts(rowid, content, heading, resource_id, "
                "chunk_type, source_id) VALUES (?,?,?,?,?,?)",
                fts_rows,
            )
            fts_rows = []

        for idx, sec in enumerate(sections):
            heading = sec["heading"]
            content = sec["content"]
            tok = estimate_tokens(content)
            ch = Chunk(
                content=content,
                chunk_type="section",
                heading=heading,
                chunk_index=idx,
                source_id=sid,
                resource_id=resource_id,
                token_count=tok,
                content_hash=content_hash(content),
            )
            cur = con.execute(
                "INSERT INTO ingestion_chunks(source_id, resource_id, chunk_index, "
                "chunk_type, heading, content, content_hash, token_count, "
                "start_line, end_line) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (sid, resource_id, idx, ch.chunk_type, ch.heading, ch.content,
                 ch.content_hash, ch.token_count, ch.start_line, ch.end_line),
            )
            cid = int(cur.lastrowid)
            fts_rows.append((cid, ch.content, ch.heading, resource_id, "section", sid))
            total_tokens += tok

        flush()

        body_text = raw_text[:10000] if len(raw_text) > 10000 else raw_text
        con.execute(
            "UPDATE resources SET description=?, body_text=?, "
            "token_count=?, updated_at=datetime('now') WHERE id=?",
            (description, body_text, total_tokens, resource_id),
        )

        con.execute(
            "UPDATE ingestion_sources SET status='ready', total_chunks=?, "
            "total_tokens=?, last_ingested_at=datetime('now'), "
            "updated_at=datetime('now') WHERE id=?",
            (len(sections), total_tokens, sid),
        )
        con.commit()

        return {
            "resource_id": resource_id,
            "file_path": file_path,
            "n_sections": len(sections),
            "total_tokens": total_tokens,
            "status": "ready",
            "duration_s": round(time.perf_counter() - t0, 2),
        }
    except Exception:
        con.execute(
            "UPDATE ingestion_sources SET status='failed', "
            "updated_at=datetime('now') WHERE id=?",
            (sid,),
        )
        con.commit()
        raise
    finally:
        con.close()
