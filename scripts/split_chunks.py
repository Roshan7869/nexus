#!/usr/bin/env python3
"""E4b: split oversized chunks into passage-sized windows and embed them.

Why (measured 2026-11-19, before this script):
  * p50 chunk = 5,690 chars ≈ 1,400 tokens — 10× the 64-128 token unit that
    Dense-X (arXiv:2312.06648) found optimal (+10.1 R@20 vs a single doc vector).
  * 61.3% of chunks exceed 4,000 chars: one blurry average vector per resource.
  * every resource had EXACTLY ONE chunk, so the chunk lane was 1:1 with the
    resource lane and added no granularity at all.
  * 547 active resources had NO chunk, making them invisible to the chunk lane.

What it does (additive and trivially revertible):
  * for each parent chunk longer than --min-chars, write child chunks
    (chunk_index >= 1) of ~--target-chars with --overlap, each prefixed with
    "name — description" (the Dense-X metadata anchor, which closes the
    query→passage style gap for natural-language prompts).
  * children are inserted into ingestion_chunks + chunks_fts and embedded into
    chunk_embeddings + vec_chunks_content (float blob + bit[384]).
  * parents are KEPT: their whole-document vector stays as an additional signal
    and nothing is destroyed, so revert = delete chunk_index > 0.
  * resources with no chunk get a chunk_index=0 chunk from body_text.

Revert:
    DELETE FROM ingestion_chunks WHERE chunk_index > 0;
    DELETE FROM chunk_embeddings WHERE chunk_id NOT IN (SELECT id FROM ingestion_chunks);
    DELETE FROM vec_chunks_content WHERE rowid NOT IN (SELECT id FROM ingestion_chunks);
    DELETE FROM chunks_fts WHERE rowid NOT IN (SELECT id FROM ingestion_chunks);

    python scripts/split_chunks.py --dry-run
    python scripts/split_chunks.py --limit 200
    python scripts/split_chunks.py
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "server"))

from server.db import get_db_path  # noqa: E402


def split_text(text: str, target: int, overlap: int, max_parts: int) -> list[str]:
    """Split on paragraph boundaries near `target` chars, falling back to hard cuts."""
    text = (text or "").strip()
    if len(text) <= target:
        return [text] if text else []
    parts: list[str] = []
    pos = 0
    n = len(text)
    while pos < n and len(parts) < max_parts:
        end = min(n, pos + target)
        if end < n:
            # prefer a paragraph/sentence break in the last 30% of the window
            floor = pos + int(target * 0.7)
            window = text[floor:end]
            best = -1
            for sep in ("\n\n", "\n", ". ", "。", "; "):
                idx = window.rfind(sep)
                if idx >= 0:
                    best = max(best, floor + idx + len(sep))
            if best > pos:
                end = best
        parts.append(text[pos:end].strip())
        if end >= n:
            break
        pos = max(end - overlap, pos + 1)
    return [p for p in parts if p]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--min-chars", type=int, default=2000,
                    help="only split parent chunks longer than this")
    ap.add_argument("--target-chars", type=int, default=1024,
                    help="child window size in characters (~256 tokens)")
    ap.add_argument("--overlap", type=int, default=128)
    ap.add_argument("--max-parts", type=int, default=8)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    import sqlite3

    from server.iso_embedder import IsoEmbedder

    db_path = Path(args.db or get_db_path())
    con = sqlite3.connect(str(db_path), timeout=60)
    con.row_factory = sqlite3.Row

    # Parents to split: one chunk per resource, long, not already split.
    parents = con.execute("""
        SELECT ic.id, ic.resource_id, ic.source_id, ic.chunk_type, ic.heading,
               ic.content, r.name, r.description, r.resource_type
          FROM ingestion_chunks ic
          JOIN resources r ON r.id = ic.resource_id
         WHERE ic.chunk_index = 0
           AND length(ic.content) > ?
           AND NOT EXISTS (SELECT 1 FROM ingestion_chunks c2
                            WHERE c2.resource_id = ic.resource_id AND c2.chunk_index > 0)
         ORDER BY length(ic.content) DESC
    """, (args.min_chars,)).fetchall()

    # Resources with no chunk at all.
    orphan = con.execute("""
        SELECT r.id, r.name, r.description, r.body_text, r.resource_type, NULL AS source_id
          FROM resources r
         WHERE r.status='active'
           AND NOT EXISTS (SELECT 1 FROM ingestion_chunks ic WHERE ic.resource_id = r.id)
           AND length(coalesce(r.body_text,'')) > 40
    """).fetchall()

    if args.limit:
        parents = parents[:args.limit]
        orphan = orphan[:args.limit]

    est = sum(min(args.max_parts, max(1, len(split_text(p["content"], args.target_chars,
                                                         args.overlap, args.max_parts))))
              for p in parents)
    print(f"db                : {db_path}")
    print(f"parents to split  : {len(parents)} (> {args.min_chars} chars, unsplit)")
    print(f"orphan resources  : {len(orphan)} (no chunk at all)")
    print(f"est. child chunks : ~{est}")

    if args.dry_run:
        if parents:
            p = parents[0]
            parts = split_text(p["content"], args.target_chars, args.overlap, args.max_parts)
            print(f"\n[dry-run] sample: {p['name']} ({len(p['content'])} chars -> {len(parts)} parts)")
            print(f"  part[1] = {parts[0][:200]!r}")
            if len(parts) > 1:
                print(f"  part[2] = {parts[1][:200]!r}")
        con.close()
        return 0

    from server.router_v2.chunk_embedder import text_hash
    from server.router_v2.chunk_index import ChunkIndex

    idx = ChunkIndex(db_path)
    embedder = IsoEmbedder.get(str(db_path))
    embedder.load_model()
    print(f"backend           : {type(embedder.model).__name__}")

    t0 = time.perf_counter()
    pending: list[tuple[int, int, str, str]] = []   # (chunk_id, resource_id, text, hash)

    def flush() -> int:
        """Embed + write the pending queue, then clear it."""
        if not pending:
            return 0
        texts = [t for _, _, t, _ in pending]
        vecs = embedder.embed_texts(texts)
        pairs = []
        for (cid, rid, _, h), v in zip(pending, vecs):
            con.execute(
                "INSERT OR REPLACE INTO chunk_embeddings"
                "(chunk_id, text_hash, embedding, resource_id, created_at) "
                "VALUES (?,?,?,?,datetime('now'))",
                (cid, h, v.astype("float32").tobytes(), rid))
            pairs.append((cid, v))
        con.commit()
        idx.add_many(pairs)
        n = len(pending)
        pending.clear()
        return n

    written = 0

    # 1) split long parents
    for p in parents:
        rid = int(p["resource_id"])
        header = f"{p['name']} — {p['description'] or ''}".strip()
        parts = split_text(p["content"], args.target_chars, args.overlap, args.max_parts)
        for i, part in enumerate(parts, start=1):
            body = f"{header}\n{part}"[:6000]
            h = text_hash(body)
            cur = con.execute(
                """INSERT INTO ingestion_chunks(source_id, resource_id, chunk_index,
                       chunk_type, heading, content, content_hash, token_count)
                   VALUES(?,?,?,'section',?,?,?,?)""",
                (p["source_id"], rid, i, p["heading"], body, h, len(body) // 4))
            cid = int(cur.lastrowid)
            try:
                con.execute(
                    "INSERT INTO chunks_fts(rowid, content, heading, resource_id, "
                    "chunk_type, source_id) VALUES(?,?,?,?,?,?)",
                    (cid, body, p["heading"], rid, p["chunk_type"], p["source_id"]))
            except sqlite3.OperationalError:
                pass
            pending.append((cid, rid, body, h))
            written += 1
            if len(pending) >= args.batch:
                flush()
                el = time.perf_counter() - t0
                print(f"  split {written} children, {len(parents)} parents queued "
                      f"({el / max(1, written) * 1000:.0f} ms/chunk)")
        con.commit()   # commit chunk rows before the next flush writes vec rows

    # 2) orphan resources -> a chunk_index=0 chunk from body_text.
    # ingestion_chunks.source_id is NOT NULL, so backfill rows need a source row.
    orphan_source_id = None
    if orphan:
        row = con.execute(
            "SELECT id FROM ingestion_sources WHERE source_type='chunk_split_backfill' LIMIT 1"
        ).fetchone()
        if row:
            orphan_source_id = int(row["id"])
        else:
            cur = con.execute(
                """INSERT INTO ingestion_sources(source_type, source_path, content_hash,
                       status, created_at, updated_at)
                   VALUES('chunk_split_backfill','(e4b orphan backfill)',?,'active',
                          datetime('now'), datetime('now'))""",
                (hashlib.sha1(b"e4b-orphan-backfill").hexdigest(),))
            orphan_source_id = int(cur.lastrowid)
            con.commit()
        print(f"orphan source_id  : {orphan_source_id}")

    for r in orphan:
        rid = int(r["id"])
        header = f"{r['name']} — {r['description'] or ''}".strip()
        body = f"{header}\n{(r['body_text'] or '')}"[:6000]
        h = text_hash(body)
        cur = con.execute(
            """INSERT INTO ingestion_chunks(source_id, resource_id, chunk_index,
                   chunk_type, heading, content, content_hash, token_count)
               VALUES(?,?,0,'section',?,?,?,?)""",
            (r["source_id"] or orphan_source_id, rid, r["name"], body, h, len(body) // 4))
        cid = int(cur.lastrowid)
        try:
            con.execute(
                "INSERT INTO chunks_fts(rowid, content, heading, resource_id, "
                "chunk_type, source_id) VALUES(?,?,?,?,?,?)",
                (cid, body, r["name"], rid, r["resource_type"], r["source_id"]))
        except sqlite3.OperationalError:
            pass
        pending.append((cid, rid, body, h))
        written += 1
        if len(pending) >= args.batch:
            flush()
    flush()

    elapsed = time.perf_counter() - t0
    n_chunks = con.execute("SELECT count(*) FROM ingestion_chunks").fetchone()[0]
    n_emb = con.execute("SELECT count(*) FROM chunk_embeddings").fetchone()[0]
    n_vec = idx.con.execute("SELECT count(*) FROM vec_chunks_content").fetchone()[0]
    con.close()
    idx.con.close()

    print(f"\nnew chunks        : {written} in {elapsed:.1f}s "
          f"({elapsed / max(1, written) * 1000:.0f} ms/chunk)")
    print(f"ingestion_chunks  : {n_chunks}")
    print(f"chunk_embeddings  : {n_emb}")
    print(f"vec_chunks_content: {n_vec}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
