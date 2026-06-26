"""Bit-packed vector index over sqlite-vec.

Stores 384-dim embeddings as packed bits (sign quantization) in a vec0
virtual table and provides Hamming nearest-neighbor search plus a float32
cosine rerank pass over candidate vectors.
"""

from __future__ import annotations

import sqlite3
from typing import Iterable

import numpy as np
import sqlite_vec
from sqlite_vec import serialize_float32

DIM = 384
_PACKED_BYTES = DIM // 8  # 48 bytes for 384 bits


class BitIndex:
    """Hamming bit-vector index backed by sqlite-vec `bit[384]` column.

    The index stores sign-quantized (bit-packed) embeddings for fast Hamming
    search. Reranking operates on the original float vectors using cosine
    similarity.
    """

    DIM = DIM

    def __init__(self, db_path: str) -> None:
        """Open or create the index at ``db_path`` and load sqlite_vec."""
        self.db_path = db_path
        self.con = sqlite3.connect(db_path)
        self.con.enable_load_extension(True)
        sqlite_vec.load(self.con)
        self.con.enable_load_extension(False)
        self.con.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks "
            "USING vec0(embedding bit[384])"
        )
        self.con.commit()

    @staticmethod
    def pack(float_vec: np.ndarray | list[float]) -> bytes:
        """Quantize ``float_vec`` to a 48-byte packed bit vector.

        Sign quantization: each dim becomes 1 if positive else 0, then packed
        via ``np.packbits``.

        Raises:
            ValueError: If input length is not exactly ``DIM``.
        """
        arr = np.asarray(float_vec, dtype=np.float32).ravel()
        if arr.shape[0] != DIM:
            raise ValueError(
                f"embedding length {arr.shape[0]} != required DIM {DIM}"
            )
        bits = (arr > 0).astype(np.uint8)
        packed = np.packbits(bits).tobytes()
        return packed

    @staticmethod
    def unpack(data: bytes) -> np.ndarray:
        """Unpack a 48-byte bit vector into a 0/1 array of length ``DIM``."""
        if len(data) != _PACKED_BYTES:
            raise ValueError(
                f"packed length {len(data)} != expected {_PACKED_BYTES}"
            )
        return np.unpackbits(np.frombuffer(data, dtype=np.uint8)).astype(
            np.uint8
        )

    def add(self, resource_id: int, embedding: np.ndarray) -> None:
        """Insert one (resource_id, embedding) pair, ignoring duplicates.

        The float vector is sign-quantized to bits via sqlite-vec's
        ``vec_quantize_binary`` at insert time.
        """
        buf = serialize_float32(np.asarray(embedding, dtype=np.float32).ravel().tolist())
        self.con.execute(
            "INSERT OR IGNORE INTO vec_chunks(rowid, embedding) "
            "VALUES (?, vec_quantize_binary(?))",
            (int(resource_id), buf),
        )
        self.con.commit()

    def add_many(
        self, pairs: Iterable[tuple[int, np.ndarray]]
    ) -> None:
        """Insert many (resource_id, embedding) pairs in one transaction."""
        rows = [
            (
                int(rid),
                serialize_float32(
                    np.asarray(vec, dtype=np.float32).ravel().tolist()
                ),
            )
            for rid, vec in pairs
        ]
        self.con.executemany(
            "INSERT OR IGNORE INTO vec_chunks(rowid, embedding) "
            "VALUES (?, vec_quantize_binary(?))",
            rows,
        )
        self.con.commit()

    def search(
        self, query_embedding: np.ndarray, k: int = 10
    ) -> list[dict]:
        """Return top-``k`` nearest rows by Hamming distance.

        The query is sign-quantized via ``vec_quantize_binary`` and matched
        against the stored bit column. Each result is
        ``{"resource_id": int, "distance": float}`` where distance is the
        Hamming distance.
        """
        buf = serialize_float32(
            np.asarray(query_embedding, dtype=np.float32).ravel().tolist()
        )
        cur = self.con.execute(
            "SELECT rowid AS resource_id, distance "
            "FROM vec_chunks WHERE embedding MATCH vec_quantize_binary(?) "
            "ORDER BY distance LIMIT ?",
            (buf, int(k)),
        )
        return [
            {"resource_id": int(rid), "distance": float(dist)}
            for rid, dist in cur.fetchall()
        ]

    def rerank(
        self,
        candidates: list[tuple[int, np.ndarray]],
        query_vec: np.ndarray,
        k: int = 10,
    ) -> list[dict]:
        """Rerank candidate float vectors by cosine similarity to query.

        Args:
            candidates: List of (resource_id, float_embedding) pairs.
            query_vec: Query float embedding.
            k: Number of top results to return.

        Returns:
            Top-``k`` results as ``{"resource_id": int, "cos": float}``,
            sorted descending by cosine similarity.
        """
        q = np.asarray(query_vec, dtype=np.float32).ravel()
        qn = np.linalg.norm(q)
        if qn == 0.0:
            qn = 1.0
        q_unit = q / qn
        scored: list[dict] = []
        for rid, vec in candidates:
            v = np.asarray(vec, dtype=np.float32).ravel()
            vn = np.linalg.norm(v)
            if vn == 0.0:
                vn = 1.0
            v_unit = v / vn
            cos = float(np.dot(q_unit, v_unit))
            scored.append({"resource_id": int(rid), "cos": cos})
        scored.sort(key=lambda r: r["cos"], reverse=True)
        return scored[:k]

    def ntotal(self) -> int:
        """Return the number of indexed rows."""
        cur = self.con.execute("SELECT count(*) FROM vec_chunks")
        return int(cur.fetchone()[0])

    def __enter__(self) -> "BitIndex":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is None:
                self.con.commit()
        finally:
            self.con.close()