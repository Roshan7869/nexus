"""NEXUS IsoEmbedder — ISO embeddings with pluggable backends.

Backends, in preference order (``NEXUS_EMBED_BACKEND`` = ``auto`` | ``onnx`` | ``st``):

* ``onnx`` — ``fastembed`` + ``onnxruntime``. ~150 MB, no torch, CPU-fast. Default
  when installed.
* ``st`` — ``sentence-transformers`` (pulls torch, ≈2.5 GB). Kept for compatibility.

This module used to import ``torch``, ``sentence_transformers`` and ``faiss`` at module
scope, which made it **unimportable** wherever torch was absent — and because every
consumer wrapped the import in ``try/except``, semantic routing was silently disabled
rather than loudly broken. Heavy imports are now lazy, and ``faiss`` is optional (a
numpy index stands in for it).

``DEFAULT_DB_PATH`` resolves through ``server.db.get_db_path()`` instead of the old
hardcoded ``/home/roshan/nexus/db/nexus.db``.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)

DIM = 384
MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"

try:  # resolve through the single DB resolver, not a hardcoded path
    from server.db import get_db_path as _resolve_db_path

    DEFAULT_DB_PATH = _resolve_db_path()
except Exception:  # pragma: no cover - server.db should always import
    DEFAULT_DB_PATH = os.environ.get("NEXUS_DB_PATH", "db/nexus.db")


class EmbedderUnavailableError(RuntimeError):
    """Raised when no embedding backend can be loaded."""


# ── optional faiss ───────────────────────────────────────────────────────────

def _faiss():
    """Return the faiss module, or None when it is not installed."""
    global _FAISS
    if _FAISS is None:
        try:
            import faiss as _f
            _FAISS = _f
        except Exception:
            _FAISS = False
    return _FAISS or None


_FAISS: Any = None


def _normalize(mat: np.ndarray) -> np.ndarray:
    """L2-normalise rows in place-safe fashion."""
    m = np.asarray(mat, dtype=np.float32)
    if m.ndim == 1:
        m = m.reshape(1, -1)
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    return m / np.maximum(norms, 1e-9)


class _NumpyIndex:
    """Minimal stand-in for ``faiss.IndexFlatIP`` when faiss is unavailable."""

    def __init__(self, dim: int) -> None:
        self.dim = dim
        self._vectors = np.zeros((0, dim), dtype=np.float32)
        self.ntotal = 0

    def add(self, vectors: np.ndarray) -> None:
        v = np.asarray(vectors, dtype=np.float32)
        if v.ndim == 1:
            v = v.reshape(1, -1)
        self._vectors = np.vstack([self._vectors, v]) if self.ntotal else v
        self.ntotal = int(self._vectors.shape[0])

    def search(self, queries: np.ndarray, k: int):
        q = np.asarray(queries, dtype=np.float32)
        if q.ndim == 1:
            q = q.reshape(1, -1)
        if self.ntotal == 0:
            empty = np.zeros((q.shape[0], 0), dtype=np.float32)
            return empty, np.zeros((q.shape[0], 0), dtype=np.int64)
        sims = q @ self._vectors.T
        k = min(k, self.ntotal)
        idx = np.argsort(-sims, axis=1)[:, :k]
        rows = np.arange(q.shape[0])[:, None]
        return sims[rows, idx], idx


# ── encoder backends ─────────────────────────────────────────────────────────

class _OnnxEncoder:
    """``fastembed.TextEmbedding`` adapted to the sentence-transformers call shape."""

    def __init__(self, model_id: str = MODEL_ID, dim: int = DIM) -> None:
        from fastembed import TextEmbedding  # lazy: only this backend needs it

        self._model = TextEmbedding(model_name=model_id)
        self.dim = dim

    def encode(self, texts, convert_to_numpy: bool = True,
               show_progress_bar: bool = False, **_kw) -> np.ndarray:
        if isinstance(texts, str):
            texts = [texts]
        vectors = list(self._model.embed(list(texts)))
        return np.asarray(vectors, dtype=np.float32)

    def get_sentence_embedding_dimension(self) -> int:
        return self.dim


class _SentenceTransformerEncoder:
    """Legacy backend; imported lazily so torch stays optional."""

    def __init__(self, model_id: str = MODEL_ID) -> None:
        import torch  # noqa: F401  (torch is required by sentence-transformers)
        from sentence_transformers import SentenceTransformer

        torch.set_num_threads(1)
        try:
            torch.set_num_interop_threads(1)
        except Exception:
            pass
        try:
            self._model = SentenceTransformer(model_id, local_files_only=True)
        except TypeError:
            self._model = SentenceTransformer(model_id)

    def encode(self, texts, convert_to_numpy: bool = True,
               show_progress_bar: bool = False, **_kw) -> np.ndarray:
        if isinstance(texts, str):
            texts = [texts]
        return np.asarray(self._model.encode(
            list(texts), convert_to_numpy=True, show_progress_bar=show_progress_bar),
            dtype=np.float32)

    def get_sentence_embedding_dimension(self) -> int:
        return int(self._model.get_sentence_embedding_dimension())


def _load_encoder(model_id: str) -> Any:
    """Load an encoder honouring ``NEXUS_EMBED_BACKEND``."""
    want = (os.environ.get("NEXUS_EMBED_BACKEND") or "auto").strip().lower()
    errors: list[str] = []

    order: tuple[str, ...]
    if want == "st":
        order = ("st",)
    elif want == "onnx":
        order = ("onnx",)
    else:
        order = ("onnx", "st")

    for backend in order:
        try:
            if backend == "onnx":
                return _OnnxEncoder(model_id)
            return _SentenceTransformerEncoder(model_id)
        except Exception as exc:
            errors.append(f"{backend}: {type(exc).__name__}: {exc}")
            logger.debug("embedder backend %s unavailable: %s", backend, exc)

    raise EmbedderUnavailableError(
        "no embedding backend available (" + "; ".join(errors) + "). "
        "Install one: `uv pip install fastembed onnxruntime`"
    )


class IsoEmbedder:
    """ISO embedder with a process-wide singleton per (db_path, model_id)."""

    DIM = DIM
    MODEL_ID = MODEL_ID

    _instances: dict[str, "IsoEmbedder"] = {}
    _shared_models: dict[str, Any] = getattr(sys, "_nexus_shared_models", {})
    setattr(sys, "_nexus_shared_models", _shared_models)
    _lock = threading.Lock()

    @classmethod
    def get(cls, db_path: str = DEFAULT_DB_PATH, model_id: str = MODEL_ID) -> "IsoEmbedder":
        """Return the process-wide singleton for ``db_path`` (loads model once)."""
        key = os.path.realpath(str(db_path)) + "|" + model_id
        inst = cls._instances.get(key)
        if inst is None:
            with cls._lock:
                inst = cls._instances.get(key)
                if inst is None:
                    inst = cls(db_path=db_path, model_id=model_id)
                    cls._instances[key] = inst
        return inst

    def __init__(self, db_path: str = DEFAULT_DB_PATH, model_id: str = MODEL_ID):
        self.db_path = db_path
        self.model_id = model_id
        self.model: Any = None
        self.index: Any = None
        self.resource_ids: list[int] = []

    # ── model ────────────────────────────────────────────────────────────
    def load(self, index_path: str = None):
        self.load_model()
        if self.index is None:
            path = index_path or os.path.join(
                os.path.dirname(os.path.abspath(str(self.db_path))), "faiss", "resources.index")
            self.load_index(path)

    def load_model(self):
        if self.model is not None:
            return
        if self.model_id in self._shared_models:
            self.model = self._shared_models[self.model_id]
            return
        with self._lock:
            if self.model_id in self._shared_models:
                self.model = self._shared_models[self.model_id]
                return
            loaded = _load_encoder(self.model_id)
            self._shared_models[self.model_id] = loaded
            self.model = loaded
            logger.info("embedder loaded: %s", type(loaded).__name__)

    # ── embedding ────────────────────────────────────────────────────────
    def embed_texts(self, texts: list[str]) -> np.ndarray:
        self.load_model()
        return np.asarray(self.model.encode(list(texts), convert_to_numpy=True,
                                            show_progress_bar=False), dtype=np.float32)

    def encode(self, text: str) -> np.ndarray:
        """Single-text encode, for routers expecting this helper."""
        return self.embed_texts([text])[0]

    def warmup(self) -> float:
        """Pre-warm the model with one probe encode; return elapsed seconds."""
        t0 = time.perf_counter()
        self.encode("warmup query")
        return time.perf_counter() - t0

    # ── index ────────────────────────────────────────────────────────────
    def build_index(self, descriptions: list[str], resource_ids: list[int]):
        embeddings = _normalize(self.embed_texts(descriptions))
        f = _faiss()
        if f is not None:
            self.index = f.IndexFlatIP(self.DIM)
        else:
            self.index = _NumpyIndex(self.DIM)
        self.index.add(embeddings.astype(np.float32))
        self.resource_ids = list(resource_ids)
        logger.info("index built: %s vectors, dim=%s, backend=%s",
                    getattr(self.index, "ntotal", 0), self.DIM,
                    "faiss" if f is not None else "numpy")

    def search(self, query: str, k: int = 5) -> list[tuple[int, float]]:
        self.load()
        qvec = _normalize(self.encode(query))
        distances, indices = self.index.search(qvec, k)
        out: list[tuple[int, float]] = []
        for j, i in enumerate(np.asarray(indices)[0]):
            i = int(i)
            if 0 <= i < len(self.resource_ids):
                out.append((self.resource_ids[i], float(np.asarray(distances)[0][j])))
        return out

    def save_index(self, path: str):
        if self.index is None:
            raise ValueError("No index to save")
        import hashlib
        import json

        f = _faiss()
        if f is not None and not isinstance(self.index, _NumpyIndex):
            f.write_index(self.index, path)
        else:  # persist the numpy fallback alongside
            np.save(path + ".npy", np.asarray(self.index._vectors, dtype=np.float32))
        checksum = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                checksum.update(chunk)
        meta = {
            "resource_ids": self.resource_ids,
            "dim": self.DIM,
            "model": self.model_id,
            "checksum_sha256": checksum.hexdigest(),
        }
        with open(path + ".meta.json", "w", encoding="utf-8") as fh:
            json.dump(meta, fh)

    def load_index(self, path: str):
        import json

        f = _faiss()
        if f is not None and os.path.exists(path):
            self.index = f.read_index(path)
        else:
            npy = path + ".npy"
            if not os.path.exists(npy):
                raise FileNotFoundError(f"no FAISS index and no numpy fallback at {npy}")
            idx = _NumpyIndex(self.DIM)
            idx.add(np.load(npy))
            self.index = idx
        with open(path + ".meta.json", encoding="utf-8") as fh:
            meta = json.load(fh)
        self.resource_ids = meta["resource_ids"]
        expected = meta.get("checksum_sha256")
        if expected and os.path.exists(path):
            import hashlib
            actual = hashlib.sha256()
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(65536), b""):
                    actual.update(chunk)
            if actual.hexdigest() != expected:
                logger.warning("FAISS index checksum mismatch at %s; run rebuild_faiss.py", path)

    def get_candidates(self, query: str, k: int = 10) -> list[int]:
        return [rid for rid, score in self.search(query, k) if score >= 0.3]
