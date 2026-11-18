#!/usr/bin/env python3
"""E4: backfill the chunk vector lane (chunk_embeddings + vec_chunks_content).

The chunk-keyed semantic arm was empty (chunk_embeddings = 0 rows, verified twice),
which made the L2 hybrid lexical-only and collapsed recall on the chunk lane
(golden-set 1.2% hit@5 pre-fix). This backfill gives it life at the CURRENT chunk
grain (one chunk per absorbed resource, from ingestion_chunks):

  * embed `"{name} — {description}\\n{content}"` — the Dense X metadata anchor
    (2312.06648): prefixing the resource's name/description before the body
    closes the query-document style gap for natural-language prompts.
  * write float32 blob to chunk_embeddings (cosine rerank source)
  * sign-quantize into vec_chunks_content bit[384] (Hamming candidate scan)

Finer 64-128-token re-chunking is P1's full work and stays deferred; this is the
minimal viable semantic arm, gated by the E2 eval harness.

    python scripts/backfill_chunk_embeddings.py --dry-run
    python scripts/backfill_chunk_embeddings.py
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "server"))

from server.db import get_db_path  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    import sqlite3

    from server.iso_embedder import IsoEmbedder

    db_path = Path(args.db or get_db_path())
    con = sqlite3.connect(str(db_path), timeout=60)
    con.row_factory = sqlite3.Row

    rows = con.execute(
        """SELECT ic.id AS chunk_id, ic.resource_id, ic.content,
                  r.name, r.description
             FROM ingestion_chunks ic
             JOIN resources r ON r.id = ic.resource_id
            WHERE ic.content IS NOT NULL AND length(ic.content) > 40
            ORDER BY ic.id""").fetchall()
    if args.limit:
        rows = rows[:args.limit]

    # skip chunks already embedded
    done = {r[0] for r in con.execute("SELECT chunk_id FROM chunk_embeddings")}
    todo = [r for r in rows if r["chunk_id"] not in done]

    print(f"db               : {db_path}")
    print(f"ingestion_chunks : {len(rows)}   already embedded: {len(done)}   todo: {len(todo)}")
    if not todo or args.dry_run:
        if todo and args.dry_run:
            sample = (f"{todo[0]['name']} — {(todo[0]['description'] or '')[:60]}\n"
                      f"{(todo[0]['content'] or '')[:120]}")
            print(f"[dry-run] first embed text:\n  {sample}")
        con.close()
        return 0

    # ensure the vec0 table exists (ChunkIndex creates + loads sqlite-vec)
    from server.router_v2.chunk_index import ChunkIndex
    from server.router_v2.chunk_embedder import text_hash
    idx = ChunkIndex(db_path)

    embedder = IsoEmbedder.get(str(db_path))
    embedder.load_model()
    print(f"backend          : {type(embedder.model).__name__}")

    t0 = time.perf_counter()
    written = 0
    for start in range(0, len(todo), args.batch):
        batch = todo[start:start + args.batch]
        texts = [f"{r['name']} — {r['description'] or ''}\n{r['content'] or ''}"[:6000]
                 for r in batch]
        vecs = embedder.embed_texts(texts)
        pairs = []
        for i, (r, v) in enumerate(zip(batch, vecs)):
            h = text_hash(texts[i])
            blob = v.astype("float32").tobytes()
            con.execute(
                "INSERT OR REPLACE INTO chunk_embeddings"
                "(chunk_id, text_hash, embedding, resource_id, created_at) "
                "VALUES (?,?,?,?,datetime('now'))",
                (int(r["chunk_id"]), h, blob, int(r["resource_id"])))
            pairs.append((int(r["chunk_id"]), v))
            written += 1
        # Commit the float rows FIRST: idx.add_many writes through its own
        # connection, and an open transaction here deadlock-locks it.
        con.commit()
        idx.add_many(pairs)   # sign-quantized bit[384] + commit inside
        if (start // args.batch) % 10 == 0:
            el = time.perf_counter() - t0
            print(f"  {written}/{len(todo)}  ({el / max(1, written) * 1000:.0f} ms/chunk)")

    elapsed = time.perf_counter() - t0
    # vec0 virtual tables need sqlite_vec loaded — read via idx's connection.
    n_vec = idx.con.execute("SELECT count(*) FROM vec_chunks_content").fetchone()[0]
    n_emb = con.execute("SELECT count(*) FROM chunk_embeddings").fetchone()[0]
    con.close()
    idx.con.close()

    print(f"\nembedded  : {written} in {elapsed:.1f}s ({elapsed / max(1, written) * 1000:.0f} ms/chunk)")
    print(f"chunk_embeddings   : {n_emb}")
    print(f"vec_chunks_content : {n_vec}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
