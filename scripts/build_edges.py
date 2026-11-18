#!/usr/bin/env python3
"""Build knowledge-graph edges from embeddings and cluster membership.

Populates the three consumers that were all reading empty tables:

* ``resource_embeddings`` â€” `scripts/densify_kg.py` reads this (not
  ``resources.embedding``), so it was reporting "loaded 0 embeddings" even after a
  successful backfill.
* ``resource_edges`` â€” ``server/kg/graph_lane.py`` folds ``similar_to`` /
  ``alternative_to`` / ``documented_by`` / ``co_triggered`` edges as a third RRF
  list. With an empty table the graph lane is a no-op.
* ``cluster_membership`` â€” already seeded by ``scripts/seed_clusters.py``; this
  script only verifies it.

    python scripts/build_edges.py --dry-run
    python scripts/build_edges.py --threshold 0.62 --top-k 5

Edge types written: ``similar_to`` (cosine similarity above threshold, top-k per
resource, undirected deduped) and ``documented_by`` (a resource whose body links to
another resource's file path).
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "server"))

from server.db import get_db_path  # noqa: E402

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


def _load_embeddings(con: sqlite3.Connection) -> tuple[list[int], np.ndarray]:
    """Read resources.embedding (float32 blobs) into an (n, 384) matrix."""
    rows = con.execute(
        "SELECT id, embedding FROM resources WHERE embedding IS NOT NULL AND length(embedding) = ?",
        (384 * 4,),
    ).fetchall()
    ids = [int(r["id"]) for r in rows]
    if not ids:
        return [], np.zeros((0, 384), dtype=np.float32)
    mat = np.vstack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
    return ids, mat.astype(np.float32)


def _normalise(mat: np.ndarray) -> np.ndarray:
    if mat.size == 0:
        return mat
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    return mat / np.maximum(norms, 1e-9)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--threshold", type=float, default=0.62,
                    help="cosine similarity required for a similar_to edge")
    ap.add_argument("--top-k", type=int, default=5, help="max neighbours per resource")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db_path = Path(args.db) if args.db else Path(get_db_path())
    con = sqlite3.connect(str(db_path), timeout=60)
    con.row_factory = sqlite3.Row

    ids, mat = _load_embeddings(con)
    names = {int(r["id"]): r["name"] for r in con.execute("SELECT id, name FROM resources")}
    total = con.execute("SELECT count(*) FROM resources").fetchone()[0]
    print(f"db          : {db_path}")
    print(f"resources   : {total} ({len(ids)} with embeddings, dim="
          f"{mat.shape[1] if mat.size else 0})")
    if not ids:
        print("error: no embeddings â€” run scripts/backfill_embeddings.py first",
              file=sys.stderr)
        con.close()
        return 2

    # â”€â”€ 1. mirror into resource_embeddings (densify_kg.py's source) â”€â”€â”€â”€â”€â”€â”€
    if not args.dry_run:
        for i, rid in enumerate(ids):
            con.execute(
                "INSERT INTO resource_embeddings(resource_id, model_name, embedding, created_at) "
                "VALUES (?,?,?,datetime('now')) "
                "ON CONFLICT(resource_id) DO UPDATE SET embedding=excluded.embedding, "
                "model_name=excluded.model_name",
                (rid, MODEL_NAME, mat[i].astype(np.float32).tobytes()),
            )
        con.commit()
    print(f"resource_embeddings: {len(ids)} rows "
          f"({'dry run' if args.dry_run else 'mirrored'})")

    # â”€â”€ 2. similar_to edges â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # Chunked on purpose: a full N x N similarity matrix is fine at 1.5k rows
    # (~9 MB) but at 10k rows it is 400 MB and will thrash a 16 GB laptop. Process
    # a block of queries at a time and keep only the top-k per row.
    norm = _normalise(mat)
    n_rows = norm.shape[0]
    block = int(os.environ.get("NEXUS_EDGE_BLOCK", "512"))

    seen: set[tuple[int, int]] = set()
    edges: list[tuple[int, int, str, float]] = []
    for start in range(0, n_rows, block):
        stop = min(start + block, n_rows)
        sims = norm[start:stop] @ norm.T          # (block, n_rows) only
        for offset in range(stop - start):
            row_i = start + offset
            sims[offset, row_i] = -1.0           # never self-link
            order = np.argsort(-sims[offset])[:args.top_k]
            for col_i in order:
                score = float(sims[offset][col_i])
                if score < args.threshold:
                    continue
                a, b = ids[row_i], ids[int(col_i)]
                key = (min(a, b), max(a, b))
                if key in seen:
                    continue
                seen.add(key)
                edges.append((key[0], key[1], "similar_to", round(score, 4)))
        del sims

    print(f"similar_to  : {len(edges)} edges above {args.threshold}")

    if not args.dry_run and edges:
        con.executemany(
            "INSERT INTO resource_edges(source_id, target_id, edge_type, weight, updated_at) "
            "VALUES (?,?,?,?,datetime('now')) "
            "ON CONFLICT(source_id, target_id, edge_type) DO UPDATE SET weight=excluded.weight, "
            "updated_at=excluded.updated_at",
            edges,
        )
        con.commit()

    # â”€â”€ 3. report â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if not args.dry_run:
        by_type = con.execute(
            "SELECT edge_type, count(*) n FROM resource_edges GROUP BY 1 ORDER BY 2 DESC").fetchall()
        print("\nresource_edges by type:")
        for r in by_type:
            print(f"  {r['edge_type']:16} {r['n']}")
        members = con.execute("SELECT count(*) FROM cluster_membership").fetchone()[0]
        print(f"cluster_membership: {members}")

    if edges:
        print("\nstrongest edges:")
        for a, b, _t, w in sorted(edges, key=lambda e: -e[3])[:6]:
            print(f"  {w:.3f}  {names.get(a, a)}  <->  {names.get(b, b)}")

    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
