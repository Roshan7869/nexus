"""Chunk-keyed bit[384] vector index (sqlite-vec) for semantic chunk retrieval.

Mirrors router_v2.bitvec.BitIndex but keyed by chunk_id instead of resource_id,
on a separate vec0 table ``vec_chunks_content`` so it does not collide with the
resource-keyed ``vec_chunks``. Float vectors for cosine rerank live in
``chunk_embeddings`` (written by ChunkEmbedder).

  vec_chunks_content : vec0(embedding bit[384]), rowid = chunk_id
  chunk_embeddings   : (chunk_id PK, text_hash, embedding BLOB float32)

Search = Hamming candidate scan (fast) -> cosine rerank over the candidates'
float vectors (read from chunk_embeddings via ChunkEmbedder.load_float).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import sqlite_vec

from router_v2.chunk_embedder import ChunkEmbedder, DIM

_TABLE = "vec_chunks_content"


class ChunkIndex:
    """Hamming bit-vector index over chunk embeddings, chunk_id-keyed."""

    DIM = DIM

    def __init__(self, db_path: str | Path, embedder: ChunkEmbedder | None = None) -> None:
        self.db_path = str(db_path)
        self.con = sqlite3.connect(db_path, timeout=30)
        self.con.row_factory = sqlite3.Row  # load_float indexes rows by name
        self.con.enable_load_extension(True)
        sqlite_vec.load(self.con)
        self.con.enable_load_extension(False)
        self.con.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {_TABLE} "
            "USING vec0(embedding bit[384])"
        )
        self.con.commit()
        self.embedder = embedder or ChunkEmbedder(db_path)

    def add_many(self, pairs: list[tuple[int, np.ndarray]]) -> int:
        """Insert (chunk_id, float_vec) pairs, sign-quantized to bits."""
        from sqlite_vec import serialize_float32
        rows = [
            (int(cid), serialize_float32(np.asarray(v, dtype=np.float32).ravel().tolist()))
            for cid, v in pairs
        ]
        self.con.executemany(
            f"INSERT OR IGNORE INTO {_TABLE}(rowid, embedding) "
            "VALUES (?, vec_quantize_binary(?))",
            rows,
        )
        self.con.commit()
        return len(rows)

    def search(self, query_embedding: np.ndarray, k: int = 20) -> list[dict]:
        """Hamming nearest-neighbor scan -> chunk_id, distance (lower=closer)."""
        from sqlite_vec import serialize_float32
        buf = serialize_float32(np.asarray(query_embedding, dtype=np.float32).ravel().tolist())
        cur = self.con.execute(
            f"SELECT rowid AS chunk_id, distance FROM {_TABLE} "
            "WHERE embedding MATCH vec_quantize_binary(?) ORDER BY distance LIMIT ?",
            (buf, int(k)),
        )
        return [{"chunk_id": int(cid), "distance": float(d)} for cid, d in cur.fetchall()]

    def rerank(self, query_vec: np.ndarray, candidates: list[dict], k: int = 10) -> list[dict]:
        """Cosine rerank of Hamming candidates using float vectors from chunk_embeddings."""
        cids = [c["chunk_id"] for c in candidates]
        floats = self.embedder.load_float(self.con, cids)
        q = np.asarray(query_vec, dtype=np.float32).ravel()
        qn = np.linalg.norm(q) or 1.0
        q_unit = q / qn
        scored: list[dict] = []
        for c in candidates:
            v = floats.get(c["chunk_id"])
            if v is None:
                continue
            vn = np.linalg.norm(v) or 1.0
            cos = float(np.dot(q_unit, v / vn))
            scored.append({"chunk_id": c["chunk_id"], "cos": cos, "hamming": c["distance"]})
        scored.sort(key=lambda r: r["cos"], reverse=True)
        return scored[:k]

    def ntotal(self) -> int:
        return int(self.con.execute(f"SELECT count(*) FROM {_TABLE}").fetchone()[0])

    def __enter__(self) -> "ChunkIndex":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is None:
                self.con.commit()
        finally:
            self.con.close()