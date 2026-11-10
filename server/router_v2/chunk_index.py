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

from router_v2.chunk_embedder import ChunkEmbedder, DIM, text_hash

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

# ---- section-grain sibling (OKF corpus arm, decision D1) -------------------

_SECTION_TABLE = "vec_sections_content"
_SECTION_FLOATS = "section_embeddings"


class SectionIndex:
    """Hamming bit-vector index over ``resource_sections`` embeddings.

    Sibling of :class:`ChunkIndex`, keyed by ``resource_sections.id`` instead of
    ``chunk_id`` — the section-grain vec arm for the OKF corpus (D1). Mirrors
    ChunkIndex exactly: vec0 ``bit[384]`` Hamming scan + cosine rerank from the
    float table (``section_embeddings`` instead of ``chunk_embeddings``).

    The embedder is shared machinery (``ChunkEmbedder``): its persistent cache is
    text-hash-keyed (``chunk_embed_cache``), so sections and chunks with
    identical text hit the same cache entry — no model duplication.

    Legacy note: migration 006 created ``vec_sections_content`` as a *plain*
    (never populated) table. ANN requires a vec0 virtual table, so ``__init__``
    drops the plain shell when present (safe: zero rows, never written).
    """

    def __init__(self, db_path: str, embedder: "ChunkEmbedder | None" = None):
        self.db_path = str(db_path)
        self.con = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.enable_load_extension(True)
        sqlite_vec.load(self.con)
        self.con.enable_load_extension(False)
        row = self.con.execute(
            "SELECT sql FROM sqlite_master WHERE name = ?", (_SECTION_TABLE,)
        ).fetchone()
        if row and "VIRTUAL" not in (row[0] or "").upper():
            # legacy plain table from migration 006 (empty by construction)
            self.con.execute(f"DROP TABLE IF EXISTS {_SECTION_TABLE}")
        self.con.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {_SECTION_TABLE} "
            f"USING vec0(embedding bit[{DIM}])"
        )
        self.con.execute(
            f"CREATE TABLE IF NOT EXISTS {_SECTION_FLOATS} ("
            "section_id INTEGER PRIMARY KEY, "
            "text_hash TEXT NOT NULL, "
            "embedding BLOB NOT NULL)"
        )
        self.con.commit()
        if embedder is None:
            embedder = ChunkEmbedder(self.db_path)
        self.embedder = embedder

    def store_floats(self, items: list[tuple[int, str, np.ndarray]]) -> None:
        """Persist (section_id, text, vec) as hash + float32 blob."""
        rows = [
            (int(sid), text_hash(txt), np.asarray(v, dtype=np.float32).tobytes())
            for sid, txt, v in items
        ]
        self.con.executemany(
            f"INSERT OR REPLACE INTO {_SECTION_FLOATS} "
            "(section_id, text_hash, embedding) VALUES (?, ?, ?)",
            rows,
        )
        self.con.commit()

    def add_many(self, items: list[tuple[int, np.ndarray]]) -> None:
        """Insert (section_id, float32[384]) pairs, sign-quantized to bits."""
        from sqlite_vec import serialize_float32
        rows = [
            (int(sid), serialize_float32(np.asarray(v, dtype=np.float32).ravel().tolist()))
            for sid, v in items
        ]
        self.con.executemany(
            f"INSERT OR REPLACE INTO {_SECTION_TABLE}(rowid, embedding) "
            "VALUES (?, vec_quantize_binary(?))",
            rows,
        )
        self.con.commit()

    def search(self, query_embedding: np.ndarray, k: int = 20) -> list[dict]:
        """Hamming nearest-neighbor scan -> section_id, distance (lower=closer)."""
        from sqlite_vec import serialize_float32
        buf = serialize_float32(np.asarray(query_embedding, dtype=np.float32).ravel().tolist())
        cur = self.con.execute(
            f"SELECT rowid AS section_id, distance FROM {_SECTION_TABLE} "
            "WHERE embedding MATCH vec_quantize_binary(?) ORDER BY distance LIMIT ?",
            (buf, int(k)),
        )
        return [{"chunk_id": int(sid), "distance": float(d)}
                for sid, d in cur.fetchall()]

    def rerank(self, query_vec: np.ndarray, candidates: list[dict], k: int = 10) -> list[dict]:
        """Cosine rerank of Hamming candidates via section_embeddings floats.

        Returns rows keyed ``chunk_id`` (= section id) so they drop straight
        into the shared ``_rrf`` alongside FTS rows.
        """
        sids = [c["chunk_id"] for c in candidates]
        if not sids:
            return []
        ph = ",".join("?" for _ in sids)
        rows = self.con.execute(
            f"SELECT section_id, embedding FROM {_SECTION_FLOATS} "
            f"WHERE section_id IN ({ph})",
            tuple(sids),
        ).fetchall()
        floats = {int(r[0]): np.frombuffer(r[1], dtype=np.float32) for r in rows}
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
        return int(self.con.execute(
            f"SELECT count(*) FROM {_SECTION_TABLE}").fetchone()[0])

    def __enter__(self) -> "SectionIndex":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is None:
                self.con.commit()
        finally:
            self.con.close()
