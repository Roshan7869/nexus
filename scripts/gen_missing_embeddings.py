"""Generate embeddings for resources that lack them (P2 Step 2.1).

Uses the existing LazyEmbedder singleton (IsoEmbedder wrapping
all-MiniLM-L6-v2, 384-dim). Read-only on code; writes only to
resource_embeddings. Idempotent — safe to re-run.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "server"))
os.environ.setdefault("NEXUS_DB_PATH", str(REPO / "db" / "nexus.db"))

from router_v2.lazy_embedder import LazyEmbedder  # noqa: E402

DB = REPO / "db" / "nexus.db"


def main() -> None:
    emb = LazyEmbedder()
    conn = sqlite3.connect(str(DB), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")

    rows = conn.execute(
        """
        SELECT r.id, r.name, COALESCE(r.description, r.name) AS text
        FROM resources r
        LEFT JOIN resource_embeddings e ON r.id = e.resource_id
        WHERE e.embedding IS NULL OR e.resource_id IS NULL
        """
    ).fetchall()
    n = len(rows)
    print(f"[gen-emb] {n} resources need embedding (all-MiniLM-L6-v2, 384-dim)", flush=True)
    if not n:
        conn.close()
        return

    t0 = time.perf_counter()
    done = 0
    for rid, name, text in rows:
        vec = emb.encode_single(text or name)  # L2-normalized np.float32(384,)
        conn.execute(
            "INSERT OR REPLACE INTO resource_embeddings "
            "(resource_id, model_name, embedding, created_at) "
            "VALUES (?, 'all-MiniLM-L6-v2', ?, datetime('now'))",
            (rid, vec.tobytes()),
        )
        done += 1
        if done % 50 == 0:
            conn.commit()
            elapsed = time.perf_counter() - t0
            print(f"  {done}/{n}  ({elapsed:.1f}s)", flush=True)
    conn.commit()
    conn.close()
    print(f"[gen-emb] done: {done}/{n} in {time.perf_counter()-t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()