"""Ingest pipeline: run a loader and persist chunks to ingestion_chunks + chunks_fts.

This is the glue between the polymorphic loaders (server.ingest.*) and the
router_v2 FTS5 index. ``nexus_ingest`` (MCP tool) and any CLI ingester call
into here.

Idempotency: ingestion_sources.source_path is UNIQUE, so re-ingesting the
same path reuses the source row; existing chunks for that source are wiped and
re-written (content_hash-based incremental ingest is a future enhancement; for
now a clean re-write is correct and simple).

resource_id linking: chunks are written with resource_id = NULL. Linking a
chunk to a resources row requires a classification/match step (by name or
file_path) which is out of scope for the ingest pipeline; FTS5 search works on
content regardless, and hydration uses a LEFT JOIN so NULL resource_id is fine.
This is documented as a known follow-up.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Iterator

from . import get_loader
from .base import Chunk

DEFAULT_DB_PATH = str(Path(__file__).resolve().parents[2] / "db" / "nexus.db")


def _connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    con.row_factory = sqlite3.Row
    return con


def _source_hash(source_path: str) -> str:
    """Best-effort content hash for a source root (fast, not cryptographic)."""
    import hashlib
    p = Path(source_path)
    if not p.exists():
        return "remote"
    h = hashlib.sha256()
    try:
        if p.is_file():
            h.update(p.read_bytes())
        else:
            # hash a sorted sample of the tree: file names + mtimes (cheap)
            count = 0
            for f in sorted(p.rglob("*")):
                if f.is_file():
                    st = f.stat()
                    h.update(f.name.encode())
                    h.update(str(int(st.st_mtime)).encode())
                    count += 1
                    if count >= 5000:
                        break
    except OSError:
        return "remote"
    return h.hexdigest()[:16]


def _get_or_create_source(
    con: sqlite3.Connection, source_type: str, source_path: str
) -> int:
    row = con.execute(
        "SELECT id FROM ingestion_sources WHERE source_path=?", (source_path,)
    ).fetchone()
    if row:
        sid = int(row["id"])
        con.execute(
            "UPDATE ingestion_sources SET source_type=?, status='ingesting', "
            "content_hash=?, updated_at=datetime('now') WHERE id=?",
            (source_type, _source_hash(source_path), sid),
        )
        # wipe old chunks + fts rows for this source (clean re-write)
        con.execute("DELETE FROM chunks_fts WHERE source_id=?", (sid,))
        con.execute("DELETE FROM ingestion_chunks WHERE source_id=?", (sid,))
        return sid
    cur = con.execute(
        "INSERT INTO ingestion_sources(source_type, source_path, content_hash, "
        "status, total_chunks, total_tokens) VALUES(?,?,?,?,0,0)",
        (source_type, source_path, _source_hash(source_path), "ingesting"),
    )
    return int(cur.lastrowid)


def ingest(
    source_type: str,
    source_path: str,
    db_path: str = DEFAULT_DB_PATH,
    max_files: int | None = None,
    max_chunks: int | None = None,
) -> dict:
    """Run the loader for (source_type, source_path) and persist all chunks.

    Args:
        source_type: one of server.ingest.registered_types().
        source_path: dir / url / repo / vault path.
        db_path: path to nexus.db.
        max_files: optional cap on files walked (loaders that accept it).
        max_chunks: optional cap on chunks written (testing / fast smoke).

    Returns:
        {source_id, total_chunks, total_tokens, status, duration_s}
    """
    t0 = time.perf_counter()
    loader = get_loader(source_type, source_path)
    if max_files is not None and hasattr(loader, "max_files"):
        loader.max_files = max_files

    con = _connect(db_path)
    try:
        sid = _get_or_create_source(con, source_type, source_path)
        total_chunks = 0
        total_tokens = 0
        fts_rows: list[tuple] = []

        def flush() -> None:
            nonlocal fts_rows
            if not fts_rows:
                return
            # chunks_fts rowid mirrors ingestion_chunks.id (set per-insert below)
            con.executemany(
                "INSERT INTO chunks_fts(rowid, content, heading, resource_id, "
                "chunk_type, source_id) VALUES (?,?,?,?,?,?)",
                fts_rows,
            )
            fts_rows = []

        global_idx = 0
        chunk_iter: Iterator[Chunk] = loader.ingest()
        for chunk in chunk_iter:
            # chunk.chunk_index is per-file; the UNIQUE(source_id, chunk_index)
            # constraint requires a globally-unique per-source index.
            cur = con.execute(
                "INSERT INTO ingestion_chunks(source_id, resource_id, chunk_index, "
                "chunk_type, heading, content, content_hash, token_count, "
                "start_line, end_line) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (sid, chunk.resource_id, global_idx, chunk.chunk_type,
                 chunk.heading, chunk.content, chunk.content_hash,
                 chunk.token_count, chunk.start_line, chunk.end_line),
            )
            global_idx += 1
            cid = int(cur.lastrowid)
            fts_rows.append((cid, chunk.content, chunk.heading, chunk.resource_id,
                             chunk.chunk_type, sid))
            total_chunks += 1
            total_tokens += chunk.token_count
            if max_chunks is not None and total_chunks >= max_chunks:
                break
            if total_chunks % 500 == 0:
                flush()
        flush()

        con.execute(
            "UPDATE ingestion_sources SET status='ready', total_chunks=?, "
            "total_tokens=?, last_ingested_at=datetime('now'), "
            "updated_at=datetime('now') WHERE id=?",
            (total_chunks, total_tokens, sid),
        )
        con.commit()
        return {
            "source_id": sid,
            "source_type": source_type,
            "source_path": source_path,
            "total_chunks": total_chunks,
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


def ingest_target(name: str, db_path: str = DEFAULT_DB_PATH, **kw) -> dict:
    """ Convenience: ingest one of the named targets from TARGET_MAP. """
    from . import TARGET_MAP
    if name not in TARGET_MAP:
        raise ValueError(f"unknown target {name!r}; known: {sorted(TARGET_MAP)}")
    stype = TARGET_MAP[name]
    # resolve default path for well-known local targets
    path = kw.pop("source_path", f"/home/roshan/{name}")
    return ingest(stype, path, db_path=db_path, **kw)