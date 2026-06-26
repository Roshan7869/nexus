"""Lazy-loading embedder wrapper for the NEXUS v2 router.

Wraps ``server.iso_embedder.IsoEmbedder`` so the heavy ``sentence_transformers``
import (≈36 s cold, measured 2026-06-25) and model load (≈1.4 s) are deferred
until the first ``encode`` call. This is what lets the MCP server boot in
<200 ms: nothing on the default FTS5 path imports this module.

The import is deferred inside ``_load`` — *not* at module top — because the
cost is dominated by the ``sentence_transformers`` import itself, not the
model file load. ``IsoEmbedder`` imports ``sentence_transformers`` at its
module top level, so we must avoid importing ``IsoEmbedder`` until needed.

Thread-safety: the singleton load is guarded by a process-wide lock; the
SentenceTransformer itself is thread-safe for inference after load.
"""
from __future__ import annotations

import threading
from functools import lru_cache
from typing import Union

import numpy as np

EncodedLike = Union[np.ndarray, list[list[float]]]


class EmbedderUnavailableError(RuntimeError):
    """Raised when the semantic embedder/model cannot be loaded."""


class LazyEmbedder:
    """Singleton lazy wrapper around IsoEmbedder (all-MiniLM-L6-v2, 384-dim)."""

    DIM = 384

    def __init__(self) -> None:
        self._iso = None          # IsoEmbedder instance (set on first load)
        self._model = None        # underlying SentenceTransformer
        self._loaded = False
        self._load_s: float | None = None
        self._lock = threading.Lock()

    # ---- singleton accessor --------------------------------------------

    @classmethod
    @lru_cache(maxsize=1)
    def get(cls) -> "LazyEmbedder":
        return cls()

    # ---- loading -------------------------------------------------------

    def _load(self) -> None:
        """Import + load the embedder on first use. Idempotent + guarded."""
        if self._loaded:
            return
        with self._lock:
            if self._loaded:           # double-checked locking
                return
            import time
            t0 = time.perf_counter()
            # Deferred import: triggers the ~36 s sentence_transformers import
            # only here, on the first dense-search call.
            try:
                from server.iso_embedder import IsoEmbedder
            except Exception as e:  # pragma: no cover - import path failure
                raise EmbedderUnavailableError(
                    f"Unable to import IsoEmbedder: {e}"
                ) from e
            iso = IsoEmbedder()
            try:
                iso.load_model()        # loads SentenceTransformer (no FAISS)
            except Exception as e:
                raise EmbedderUnavailableError(
                    f"Unable to load embedding model '{iso.model_id}': {e}"
                ) from e
            self._iso = iso
            self._model = iso.model
            self._load_s = time.perf_counter() - t0
            self._loaded = True

    # ---- public API ----------------------------------------------------

    def is_available(self) -> bool:
        """True iff the embedder has been loaded in this process."""
        return self._loaded

    def ensure_loaded(self) -> None:
        """Force-load the embedder (e.g. for a warm-up call)."""
        self._load()

    def encode(self, texts: Union[str, list[str]]) -> np.ndarray:
        """Encode a string or list of strings to a 384-dim float32 array.

        Returns shape (D,) for a single string, (N, D) for a list.
        First call pays the cold-import cost; subsequent calls are ~50 ms.
        """
        self._load()
        single = isinstance(texts, str)
        batch = [texts] if single else list(texts)
        vecs = self._model.encode(
            batch, convert_to_numpy=True, show_progress_bar=False
        ).astype(np.float32, copy=False)
        return vecs[0] if single else vecs

    def encode_single(self, text: str) -> np.ndarray:
        """Encode one string → 384-dim float32 vector (L2-normalized)."""
        vec = self.encode(text)
        # IsoEmbedder's model outputs normalized vectors for all-MiniLM-L6-v2;
        # normalise defensively in case a future model doesn't.
        n = np.linalg.norm(vec)
        if n > 0:
            vec = vec / n
        return vec.astype(np.float32, copy=False)

    def stats(self) -> dict:
        """Return load state + last cold-load wall time (None if not loaded)."""
        return {
            "loaded": self._loaded,
            "load_seconds": self._load_s,
            "dim": self.DIM,
            "model": getattr(self._iso, "model_id", None) if self._iso else None,
        }

    def unload(self) -> None:
        """Release the model (frees ~300 MB). Next encode() reloads."""
        with self._lock:
            self._iso = None
            self._model = None
            self._loaded = False
            self._load_s = None