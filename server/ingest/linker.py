"""Link ingestion_chunks to resources rows by content_hash + file_path.

The missing infrastructure row from the chunk reachability table: 0/66,495
chunks had a resource_id. This backfill closes that gap, best-effort:

  1. Re-walk each local_dir source with the SAME LocalDirLoader logic, tagging
     every yielded chunk with its file_path (the loader itself doesn't store it).
  2. Match each yielded chunk to ingestion_chunks by (source_id, content_hash)
     — robust, not positional. Duplicate-content chunks share a hash and all
     get linked in one pass.
  3. Map file_path -> resources.id by exact path, extension-stripped path, or
     parent dir (resource.file_path often has no .md suffix). Chunks whose file
     isn't a resource stay resource_id=NULL (still retrievable as a finding).

F1a (path-based pass, decoupled from content_hash): ``link_by_path`` backfills
``source_file_path`` on every chunk (by content_hash, with a chunk_index
fallback for drifted chunks) then links chunks to resources by file path
ALONE — recovering the ~29 wiki file-resources whose content_hash drifted
between ingest and re-walk, which the hash-gated pass above cannot reach.

Idempotent: only writes where resource_id / source_file_path changes. Rollback
is ``UPDATE ingestion_chunks SET resource_id=NULL`` (+ chunks_fts.resource_id=NULL).

Read-only w.r.t. the live MCP: only touches ingestion_chunks / chunks_fts
resource_id / source_file_path columns, which the live resource router never reads.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from ingest.local_dir import LocalDirLoader
from ingest.base import Chunk

_EXT_STRIP = (".md", ".markdown", ".rst", ".txt", ".py", ".sh", ".ts", ".js", ".yaml", ".yml", ".json")


def _connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def _build_resource_path_index(con: sqlite3.Connection) -> dict[str, int]:
    """file_path -> resource_id, plus extension-stripped variants."""
    idx: dict[str, int] = {}
    for r in con.execute("SELECT id, file_path FROM resources WHERE file_path IS NOT NULL"):
        fp = r["file_path"]
        idx[fp] = r["id"]
        for ext in _EXT_STRIP:
            if fp.endswith(ext):
                idx[fp[: -len(ext)]] = r["id"]
    return idx


def _match_path(path: Path, resource_by_path: dict[str, int]) -> int | None:
    s = str(path)
    if s in resource_by_path:
        return resource_by_path[s]
    # strip trailing extension(s)
    for ext in _EXT_STRIP:
        if s.endswith(ext):
            if s[: -len(ext)] in resource_by_path:
                return resource_by_path[s[: -len(ext)]]
    # parent dir (resource points at a folder)
    parent = str(path.parent)
    if parent in resource_by_path:
        return resource_by_path[parent]
    return None


def _walk_with_paths(loader: LocalDirLoader) -> list[tuple[Path, Chunk]]:
    """Replay LocalDirLoader.ingest() but retain each chunk's source file path."""
    root = Path(loader.source_path)
    out: list[tuple[Path, Chunk]] = []
    seen = 0
    for path in sorted(root.rglob("*")):
        if loader.max_files is not None and seen >= loader.max_files:
            break
        if not path.is_file():
            continue
        if any(part in loader.SKIP_DIRS or part.startswith(".git") for part in path.parts):
            continue
        if not loader._include_path(path):
            continue
        seen += 1
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for chunk in loader.chunk_file(path, text):
            out.append((path, chunk))
    return out


def link_chunks_to_resources(db_path: str, source_id: int | None = None,
                             dry_run: bool = False) -> dict[str, Any]:
    """Backfill ingestion_chunks.resource_id (+ chunks_fts.resource_id).

    Args:
        db_path: path to nexus.db.
        source_id: restrict to one ingestion source (None = all local_dir sources).
        dry_run: if True, report counts but write nothing.

    Returns: stats dict {sources, chunks_scanned, hash_matches, resource_links,
        null_links, updates_written}.
    """
    con = _connect(db_path)
    try:
        where = "WHERE source_type='local_dir'"
        params: list = []
        if source_id is not None:
            where += " AND id=?"
            params.append(source_id)
        sources = con.execute(
            f"SELECT id, source_path FROM ingestion_sources {where}", tuple(params)
        ).fetchall()

        resource_by_path = _build_resource_path_index(con)
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        stats = {"sources": len(sources), "chunks_scanned": 0, "hash_matches": 0,
                 "resource_links": 0, "null_links": 0, "updates_written": 0}

        for src in sources:
            sid = src["id"]
            src_path = src["source_path"]
            if not Path(src_path).exists():
                continue
            loader = LocalDirLoader(src_path)
            walked = _walk_with_paths(loader)

            # batch: (chunk_id, resource_id) for chunks whose link changed
            updates: list[tuple[int, int | None]] = []
            for path, chunk in walked:
                stats["chunks_scanned"] += 1
                rid = _match_path(path, resource_by_path)
                # find DB chunk rows with this (source_id, content_hash)
                rows = con.execute(
                    "SELECT id, resource_id FROM ingestion_chunks "
                    "WHERE source_id=? AND content_hash=?",
                    (sid, chunk.content_hash),
                ).fetchall()
                if not rows:
                    continue
                stats["hash_matches"] += len(rows)
                if rid is not None:
                    stats["resource_links"] += len(rows)
                else:
                    stats["null_links"] += len(rows)
                for row in rows:
                    if row["resource_id"] != rid:
                        updates.append((int(row["id"]), rid))

            if dry_run or not updates:
                continue
            # write ingestion_chunks + chunks_fts in one transaction
            con.execute("BEGIN")
            try:
                for chunk_id, rid in updates:
                    con.execute(
                        "UPDATE ingestion_chunks SET resource_id=? WHERE id=?",
                        (rid, chunk_id),
                    )
                    con.execute(
                        "UPDATE chunks_fts SET resource_id=? WHERE rowid=?",
                        (rid, chunk_id),
                    )
                con.commit()
                stats["updates_written"] += len(updates)
            except Exception:
                con.rollback()
                raise

        return stats
    finally:
        con.close()


def _build_resource_path_multi(con: sqlite3.Connection) -> tuple[dict[str, int], dict[str, list[int]]]:
    """Path index that does NOT shadow directory-shared resources.

    Returns (exact_index, dir_index):
      exact_index: file_path -> resource_id for unique file resources (and
        extension-stripped variants). Only populated when exactly one resource
        claims that path — avoids silently picking one of N.
      dir_index: file_path -> [resource_ids] for paths claimed by >1 resource
        (shared file / directory). Disambiguation is the caller's job.
    """
    by_path: dict[str, list[int]] = {}
    for r in con.execute("SELECT id, file_path FROM resources WHERE file_path IS NOT NULL"):
        by_path.setdefault(r["file_path"], []).append(r["id"])
    exact: dict[str, int] = {}
    dirs: dict[str, list[int]] = {}
    for fp, rids in by_path.items():
        if len(rids) == 1:
            exact[fp] = rids[0]
            for ext in _EXT_STRIP:
                if fp.endswith(ext):
                    exact[fp[: -len(ext)]] = rids[0]
        else:
            dirs[fp] = rids
    return exact, dirs


def link_source_file_paths(db_path: str, source_id: int | None = None,
                           dry_run: bool = False) -> dict[str, Any]:
    """Backfill ingestion_chunks.source_file_path (migration 011).

    Re-walks each local_dir source; for each yielded chunk, sets
    source_file_path on the DB row matched by (source_id, content_hash), with a
    (source_id, chunk_index) fallback for hash-drifted chunks. Idempotent.
    """
    con = _connect(db_path)
    try:
        where = "WHERE source_type='local_dir'"
        params: list = []
        if source_id is not None:
            where += " AND id=?"; params.append(source_id)
        sources = con.execute(
            f"SELECT id, source_path FROM ingestion_sources {where}", tuple(params)
        ).fetchall()
        stats = {"sources": len(sources), "paths_set": 0, "hash_matches": 0, "index_matches": 0}
        for src in sources:
            sid = src["id"]
            if not Path(src["source_path"]).exists():
                continue
            loader = LocalDirLoader(src["source_path"])
            walked = _walk_with_paths(loader)
            updates: dict[int, str] = {}  # chunk_id -> source_file_path (dedup by id)
            for path, chunk in walked:
                rows = con.execute(
                    "SELECT id, source_file_path FROM ingestion_chunks "
                    "WHERE source_id=? AND content_hash=?",
                    (sid, chunk.content_hash),
                ).fetchall()
                matched = rows
                if not matched:  # hash drifted — try positional (chunk_index)
                    stats["index_matches"] += 1
                    matched = con.execute(
                        "SELECT id, source_file_path FROM ingestion_chunks "
                        "WHERE source_id=? AND chunk_index=?",
                        (sid, chunk.chunk_index if hasattr(chunk, "chunk_index") else 0),
                    ).fetchall()
                else:
                    stats["hash_matches"] += len(rows)
                sp = str(path)
                for row in matched:
                    if row["source_file_path"] != sp and int(row["id"]) not in updates:
                        updates[int(row["id"])] = sp
            if dry_run or not updates:
                stats["paths_set"] += len(updates); continue
            con.execute("BEGIN")
            try:
                for cid, sp in updates.items():
                    con.execute(
                        "UPDATE ingestion_chunks SET source_file_path=? WHERE id=?",
                        (sp, cid),
                    )
                con.commit()
            except Exception:
                con.rollback(); raise
            stats["paths_set"] += len(updates)
        return stats
    finally:
        con.close()


def link_by_path(db_path: str, source_id: int | None = None,
                 dry_run: bool = False) -> dict[str, Any]:
    """F1a: link chunks to resources by source_file_path, decoupled from content_hash.

    Requires source_file_path to be backfilled (run link_source_file_paths first).
    Uses _build_resource_path_multi so directory-shared resources are not
    shadowed: a chunk whose file maps to a unique resource is linked directly;
    chunks mapping to a shared path are skipped (need heading-level
    disambiguation, out of scope). Idempotent.
    """
    con = _connect(db_path)
    try:
        exact, _dirs = _build_resource_path_multi(con)
        where = "WHERE source_file_path IS NOT NULL"
        params: list = []
        if source_id is not None:
            where += " AND source_id=?"; params.append(source_id)
        rows = con.execute(
            f"SELECT id, source_file_path, resource_id FROM ingestion_chunks {where}",
            tuple(params),
        ).fetchall()
        stats = {"candidates": len(rows), "linked": 0, "already": 0, "skipped_shared": 0, "no_match": 0, "updates_written": 0}
        updates: list[tuple[int, int]] = []
        for r in rows:
            rid = _match_path(Path(r["source_file_path"]), exact)
            if rid is None:
                stats["no_match"] += 1; continue
            if r["resource_id"] == rid:
                stats["already"] += 1; continue
            updates.append((rid, int(r["id"])))
            stats["linked"] += 1
        if not dry_run and updates:
            con.execute("BEGIN")
            try:
                for rid, cid in updates:
                    con.execute("UPDATE ingestion_chunks SET resource_id=? WHERE id=?", (rid, cid))
                    con.execute("UPDATE chunks_fts SET resource_id=? WHERE rowid=?", (rid, cid))
                con.commit(); stats["updates_written"] = len(updates)
            except Exception:
                con.rollback(); raise
        return stats
    finally:
        con.close()


if __name__ == "__main__":
    import argparse, json
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="db/nexus.db")
    ap.add_argument("--source-id", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--mode", default="hash",
                    choices=["hash", "backfill-paths", "path", "all"],
                    help="hash=legacy content_hash link; backfill-paths=set source_file_path; "
                         "path=F1a path-based link; all=backfill-paths then path")
    args = ap.parse_args()
    if args.mode == "hash":
        print(json.dumps(link_chunks_to_resources(args.db, args.source_id, args.dry_run), indent=2))
    elif args.mode == "backfill-paths":
        print(json.dumps(link_source_file_paths(args.db, args.source_id, args.dry_run), indent=2))
    elif args.mode == "path":
        print(json.dumps(link_by_path(args.db, args.source_id, args.dry_run), indent=2))
    else:  # all
        b = link_source_file_paths(args.db, args.source_id, args.dry_run)
        p = link_by_path(args.db, args.source_id, args.dry_run)
        print(json.dumps({"backfill": b, "path_link": p}, indent=2))