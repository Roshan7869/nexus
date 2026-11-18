#!/usr/bin/env python3
"""Backfill `resources.embedding` (float32 blobs, 384-dim) via IsoEmbedder.

The semantic lane reads `resources.embedding` with `np.frombuffer(..., float32)`,
so vectors are stored as raw little-endian float32 bytes — the same layout
`sqlite_vec.serialize_float32` produces.

    python scripts/backfill_embeddings.py --limit 5 --dry-run
    python scripts/backfill_embeddings.py
    python scripts/backfill_embeddings.py --force        # re-embed everything
    python scripts/backfill_embeddings.py --verify "pedantic quality auditor"

Backend is selected by NEXUS_EMBED_BACKEND (auto|onnx|st); `auto` prefers the
ONNX fastembed backend, which needs no torch.
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

DIM = 384


def _resource_text(name: str, description: str | None, body: str | None,
                   max_chars: int = 2000) -> str:
    """Compose the embedded text: name + description lead, body fills the rest."""
    parts = [name or ""]
    if description:
        parts.append(description)
    if body:
        parts.append(body)
    text = "\n".join(p for p in parts if p).strip()
    return text[:max_chars]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None, help="override DB path")
    ap.add_argument("--limit", type=int, default=None, help="max resources to embed")
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--force", action="store_true", help="re-embed rows that already have a vector")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify", default=None, help="query to test after backfill")
    args = ap.parse_args()

    db_path = Path(args.db) if args.db else Path(get_db_path())
    if not db_path.exists():
        print(f"error: database not found: {db_path}", file=sys.stderr)
        return 2

    import sqlite3

    from server.iso_embedder import IsoEmbedder

    con = sqlite3.connect(str(db_path), timeout=60)
    con.row_factory = sqlite3.Row
    where = "" if args.force else "WHERE embedding IS NULL OR length(embedding)=0"
    sql = (f"SELECT id, name, description, body_text FROM resources {where} "
           f"ORDER BY id")
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"
    rows = con.execute(sql).fetchall()

    print(f"db          : {db_path}")
    print(f"to embed    : {len(rows)} resources "
          f"({'all, forced' if args.force else 'missing vectors only'})")
    if not rows:
        con.close()
        print("nothing to do")
        return 0

    embedder = IsoEmbedder(db_path=str(db_path))
    t0 = time.perf_counter()
    embedder.load_model()
    load_s = time.perf_counter() - t0
    print(f"backend     : {type(embedder.model).__name__} (load {load_s:.1f}s)")

    if args.dry_run:
        sample = _resource_text(rows[0]["name"], rows[0]["description"], rows[0]["body_text"])
        print(f"\n[dry run] first text ({len(sample)} chars):\n  {sample[:200]}")
        con.close()
        return 0

    written = 0
    t_embed = time.perf_counter()
    for start in range(0, len(rows), args.batch):
        batch = rows[start:start + args.batch]
        texts = [_resource_text(r["name"], r["description"], r["body_text"]) for r in batch]
        vectors = embedder.embed_texts(texts)
        if vectors.shape[1] != DIM:
            print(f"error: backend returned dim {vectors.shape[1]}, expected {DIM}",
                  file=sys.stderr)
            con.close()
            return 3
        for row, vec in zip(batch, vectors):
            con.execute("UPDATE resources SET embedding=?, updated_at=datetime('now') "
                        "WHERE id=?", (vec.astype("float32").tobytes(), row["id"]))
            written += 1
        con.commit()
        print(f"  {written}/{len(rows)} embedded")
    elapsed = time.perf_counter() - t_embed
    con.close()

    print(f"\nembedded    : {written} in {elapsed:.1f}s "
          f"({elapsed / max(1, written) * 1000:.0f} ms/resource)")

    if args.verify:
        con = sqlite3.connect(str(db_path), timeout=60)
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT id, name, embedding FROM resources "
                           "WHERE embedding IS NOT NULL").fetchall()
        q = embedder.encode(args.verify)
        qn = q / max(1e-9, float((q * q).sum() ** 0.5))
        scored = []
        for r in rows:
            v = __import__("numpy").frombuffer(r["embedding"], dtype="float32")
            vn = v / max(1e-9, float((v * v).sum() ** 0.5))
            scored.append((float(qn @ vn), r["name"]))
        scored.sort(reverse=True)
        con.close()
        print(f"\nsemantic top-5 for {args.verify!r}:")
        for score, name in scored[:5]:
            print(f"  {score:.4f}  {name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
