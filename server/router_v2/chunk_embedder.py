"""Chunk embedder + cache for the 66,495 ingestion_chunks.

The caching layer the chunk retrieval path needs. Two tables:

  chunk_embeddings(chunk_id INT PK, text_hash TEXT, embedding BLOB)
      - one float32(384) vector per chunk (1536 bytes), used for cosine rerank
        AND as the persistent store so the 66k backfill is resumable by chunk_id.
  chunk_embed_cache(text_hash TEXT PK, embedding BLOB, created_at TEXT)
      - content-dedup cache: identical chunk texts (repeated headings, boilerplate)
        share one encode. Also makes re-runs and live queries skip the model.

The model itself is the LazyEmbedder singleton (all-MiniLM-L6-v2, 384-dim),
loaded once per process (~36 s cold import + ~1.4 s load; ~50 ms warm).

Read-only w.r.t. the live MCP: these tables are chunk-retrieval infrastructure,
not touched by the resource router or Thompson tables. vec0 is loaded only here.
"""
from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import sqlite_vec

from router_v2.lazy_embedder import LazyEmbedder, EmbedderUnavailableError

DIM = 384
_FLOAT_BYTES = DIM * 4  # 1536 bytes for float32(384)
_HASH_LEN = 16

_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS chunk_embeddings (
        chunk_id   INTEGER PRIMARY KEY,
        text_hash  TEXT NOT NULL,
        embedding  BLOB NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS chunk_embed_cache (
        text_hash   TEXT PRIMARY KEY,
        embedding   BLOB NOT NULL,
        created_at  TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_chunk_emb_hash ON chunk_embeddings(text_hash)",
]


def _connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path, timeout=30)
    con.enable_load_extension(True)
    try:
        sqlite_vec.load(con)
    finally:
        con.enable_load_extension(False)
    con.row_factory = sqlite3.Row
    for stmt in _SCHEMA:
        con.execute(stmt)
    con.commit()
    return con


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:_HASH_LEN]


def _to_blob(vec: np.ndarray) -> bytes:
    return np.asarray(vec, dtype=np.float32).ravel().tobytes()


def _from_blob(blob: bytes) -> np.ndarray:
    arr = np.frombuffer(blob, dtype=np.float32)
    if arr.shape[0] != DIM:
        raise ValueError(f"embedding dim {arr.shape[0]} != {DIM}")
    return arr.copy()


class ChunkEmbedder:
    """Cached, singleton-backed batch embedder for chunk content.

    Reuses one LazyEmbedder model instance and a persistent content-dedup
    cache. encode_batch only calls the model for texts whose hash is not
    already in chunk_embed_cache; results are written back for next time.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self._emb = LazyEmbedder.get()
        self._lock = threading.Lock()

    # ---- schema / bootstrap --------------------------------------------

    def ensure_schema(self) -> None:
        con = _connect(self.db_path)
        con.close()

    # ---- cache-backed batch encode -------------------------------------

    def _cache_lookup(self, con: sqlite3.Connection, hashes: list[str]) -> dict[str, np.ndarray]:
        if not hashes:
            return {}
        ph = ",".join("?" for _ in hashes)
        out: dict[str, np.ndarray] = {}
        for r in con.execute(
            f"SELECT text_hash, embedding FROM chunk_embed_cache WHERE text_hash IN ({ph})",
            tuple(hashes),
        ).fetchall():
            try:
                out[r["text_hash"]] = _from_blob(r["embedding"])
            except Exception:
                continue  # corrupt/legacy blob → re-encode
        return out

    def _cache_store(self, con: sqlite3.Connection, items: list[tuple[str, np.ndarray]]) -> None:
        if not items:
            return
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        con.executemany(
            "INSERT OR IGNORE INTO chunk_embed_cache(text_hash, embedding, created_at) "
            "VALUES (?,?,?)",
            [(h, _to_blob(v), ts) for h, v in items],
        )

    def encode_batch(self, texts: list[str], batch_size: int = 128,
                     con: sqlite3.Connection | None = None) -> np.ndarray:
        """Encode a list of texts to a (N, 384) float32 array, cache-backed.

        Only uncached texts are passed through the model; cached ones are
        read from chunk_embed_cache. All results are written back to the cache.

        Args:
            con: optional shared connection (e.g. the backfill's idx.con) to
                avoid opening+closing a DB connection (with vec0 load) per call.
        """
        if not texts:
            return np.zeros((0, DIM), dtype=np.float32)
        hashes = [text_hash(t) for t in texts]
        own_con = con is None
        con = con if con is not None else _connect(self.db_path)
        try:
            cached = self._cache_lookup(con, hashes)
        finally:
            if own_con:
                con.close()

        out = np.zeros((len(texts), DIM), dtype=np.float32)
        to_encode: list[int] = []
        for i, h in enumerate(hashes):
            v = cached.get(h)
            if v is not None:
                out[i] = v
            else:
                to_encode.append(i)

        misses: list[tuple[str, np.ndarray]] = []
        if to_encode:
            for start in range(0, len(to_encode), batch_size):
                idxs = to_encode[start:start + batch_size]
                batch_texts = [texts[i] for i in idxs]
                vecs = self._emb.encode(batch_texts)  # (n, 384) float32
                for j, i in enumerate(idxs):
                    out[i] = vecs[j]
                    misses.append((hashes[i], vecs[j]))
            own_con2 = con is None or own_con
            con2 = con if not own_con2 else _connect(self.db_path)
            try:
                self._cache_store(con2, misses)
                con2.commit()
            finally:
                if own_con2:
                    con2.close()
        return out

    def encode_one(self, text: str) -> np.ndarray:
        return self.encode_batch([text])[0]

    # ---- chunk-level persistent store ----------------------------------

    def stored_chunk_ids(self, con: sqlite3.Connection) -> set[int]:
        return {int(r[0]) for r in con.execute("SELECT chunk_id FROM chunk_embeddings").fetchall()}

    def store_chunks(self, con: sqlite3.Connection, items: list[tuple[int, str, np.ndarray]]) -> int:
        """Persist (chunk_id, text_hash, vec) rows. Returns rows inserted."""
        if not items:
            return 0
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        con.executemany(
            "INSERT OR REPLACE INTO chunk_embeddings(chunk_id, text_hash, embedding, created_at) "
            "VALUES (?,?,?,?)",
            [(int(cid), h, _to_blob(v), ts) for cid, h, v in items],
        )
        return len(items)

    def load_float(self, con: sqlite3.Connection, chunk_ids: list[int]) -> dict[int, np.ndarray]:
        if not chunk_ids:
            return {}
        ph = ",".join("?" for _ in chunk_ids)
        out: dict[int, np.ndarray] = {}
        for r in con.execute(
            f"SELECT chunk_id, embedding FROM chunk_embeddings WHERE chunk_id IN ({ph})",
            tuple(chunk_ids),
        ).fetchall():
            try:
                out[int(r["chunk_id"])] = _from_blob(r["embedding"])
            except Exception:
                continue
        return out

    def stats(self) -> dict[str, Any]:
        con = _connect(self.db_path)
        try:
            n_chunks = con.execute("SELECT COUNT(*) FROM chunk_embeddings").fetchone()[0]
            n_cache = con.execute("SELECT COUNT(*) FROM chunk_embed_cache").fetchone()[0]
        finally:
            con.close()
        return {
            "chunk_embeddings": int(n_chunks),
            "cache_entries": int(n_cache),
            "embedder": self._emb.stats(),
            "dim": DIM,
        }