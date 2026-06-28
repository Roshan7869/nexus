"""Backfill vec_chunks_content + chunk_embeddings for all 66,495 ingestion_chunks.

Resumable: skips chunk_ids already present in chunk_embeddings. Run in the
background; re-run anytime to pick up where it stopped.

  python scripts/backfill_chunk_index.py [--db db/nexus.db] [--batch 256] [--max N]

Read-only w.r.t. the live MCP (writes only to chunk_embeddings / vec_chunks_content).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "server"))

from router_v2.chunk_embedder import ChunkEmbedder, text_hash  # noqa: E402
from router_v2.chunk_index import ChunkIndex  # noqa: E402


def fetch_pending(con: sqlite3.Connection, batch: int, max_n: int | None) -> list[tuple[int, str]]:
    sql = (
        "SELECT c.id, c.content FROM ingestion_chunks c "
        "LEFT JOIN chunk_embeddings e ON e.chunk_id = c.id "
        "WHERE e.chunk_id IS NULL ORDER BY c.id "
    )
    if max_n is not None:
        sql += f"LIMIT {int(max_n)}"
    else:
        sql += f"LIMIT {int(batch)}"
    return [(int(r[0]), r[1]) for r in con.execute(sql).fetchall()]


def backfill(db_path: str, batch: int = 256, max_n: int | None = None,
             verbose: bool = True) -> dict:
    embedder = ChunkEmbedder(db_path)
    embedder.ensure_schema()
    idx = ChunkIndex(db_path, embedder=embedder)
    con = idx.con  # single shared connection (vec0 loaded, avoids self-deadlock)
    try:
        con.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass

    total = con.execute("SELECT COUNT(*) FROM ingestion_chunks").fetchone()[0]
    done = embedder.stored_chunk_ids(con)
    start_done = len(done)
    t0 = time.perf_counter()
    processed = 0

    while True:
        rows = fetch_pending(con, batch, max_n - processed if max_n else None)
        if not rows:
            break
        texts = [r[1] for r in rows]
        vecs = embedder.encode_batch(texts, con=con)
        items = [(r[0], text_hash(r[1]), vecs[i]) for i, r in enumerate(rows)]
        embedder.store_chunks(con, items)
        idx.add_many([(r[0], vecs[i]) for i, r in enumerate(rows)])
        con.commit()
        processed += len(rows)
        if verbose and (processed % 1024 == 0 or processed < batch * 2):
            elapsed = time.perf_counter() - t0
            rate = processed / elapsed if elapsed > 0 else 0
            print(f"  {start_done + processed}/{total} chunks "
                  f"({rate:.0f}/s, {elapsed:.0f}s)", flush=True)
        if max_n is not None and processed >= max_n:
            break

    elapsed = time.perf_counter() - t0
    n_indexed = idx.ntotal()
    idx.con.close()
    return {
        "total_chunks": total,
        "already_indexed": start_done,
        "newly_indexed": processed,
        "index_size": n_indexed,
        "elapsed_s": round(elapsed, 1),
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "db" / "nexus.db"))
    ap.add_argument("--batch", type=int, default=256)
    ap.add_argument("--max", type=int, default=None, help="cap chunks this run")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    res = backfill(args.db, args.batch, args.max, verbose=not args.quiet)
    import json
    print(json.dumps(res, indent=2))