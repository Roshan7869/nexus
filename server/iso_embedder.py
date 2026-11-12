"""NEXUS v3 IsoEmbedder — sentence-transformers + FAISS ISO embeddings."""
import os
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
import time
import torch
torch.set_num_threads(1)
torch.set_num_interop_threads(1)
import sqlite3, threading, numpy as np, faiss
from sentence_transformers import SentenceTransformer
from typing import Optional

DEFAULT_DB_PATH = "/home/roshan/nexus/db/nexus.db"


class EmbedderUnavailableError(RuntimeError):
    """Raised when semantic embedder/model cannot be loaded."""


class IsoEmbedder:
    """Sentence-transformers + FAISS ISO embedder.

    Use :meth:`get` (process-wide singleton, keyed by db_path) instead of the
    constructor. Constructing ``IsoEmbedder()`` per call reloads the
    all-MiniLM-L6-v2 model (~12-15s cold) on first use — the standard
    sentence-transformers anti-pattern (see TEI/Infinity: production embedding
    servers load once per process, never per request). The FAISS index is
    read-only after load, so sharing one instance across callers is safe.
    """
    DIM = 384
    MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"

    _instances: dict[str, "IsoEmbedder"] = {}
    _shared_models: dict[str, Any] = getattr(sys, "_nexus_shared_models", {})
    setattr(sys, "_nexus_shared_models", _shared_models)
    _lock = threading.Lock()

    @classmethod
    def get(cls, db_path: str = DEFAULT_DB_PATH, model_id: str = MODEL_ID) -> "IsoEmbedder":
        """Return the process-wide singleton for ``db_path`` (loads model once).

        The key is normalized via ``os.path.realpath`` so callers passing a
        relative path, an absolute path, or a ``Path``-str all hit the same
        instance (avoids one cold load per path string-form).
        """
        key = os.path.realpath(str(db_path))
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
        self.model = None
        self.index = None
        self.resource_ids = []

    def load(self, index_path: str = None):
        self.load_model()
        if self.index is None:
            path = index_path or "/home/roshan/nexus/db/faiss/resources.index"
            self.load_index(path)

    def load_model(self):
        if self.model is None:
            if self.model_id in self._shared_models:
                self.model = self._shared_models[self.model_id]
                return
            with self._lock:
                if self.model_id in self._shared_models:
                    self.model = self._shared_models[self.model_id]
                    return
                print(f"[IsoEmbedder] Loading {self.model_id}...")
                # Prefer local cache only for reliable offline usage in TUI/sandbox sessions.
                try:
                    loaded = SentenceTransformer(self.model_id, local_files_only=True)
                except TypeError:
                    # Compatibility with older sentence-transformers versions.
                    loaded = SentenceTransformer(self.model_id)
                except Exception as e:
                    raise EmbedderUnavailableError(
                        f"Unable to load embedding model '{self.model_id}' from local cache: {e}"
                    ) from e
                self._shared_models[self.model_id] = loaded
                self.model = loaded

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        self.load_model()
        return self.model.encode(texts, convert_to_numpy=True, show_progress_bar=True)

    def encode(self, text: str) -> np.ndarray:
        """Compatibility helper for routers expecting single-text encode()."""
        self.load_model()
        vec = self.model.encode([text], convert_to_numpy=True, show_progress_bar=False)
        return vec[0]

    def warmup(self) -> float:
        """Pre-warm model and encode a probe query; return elapsed seconds."""
        t0 = time.perf_counter()
        self.load_model()
        _ = self.encode("warmup query")
        return time.perf_counter() - t0

    def build_index(self, descriptions: list[str], resource_ids: list[int]):
        embeddings = self.embed_texts(descriptions)
        faiss.normalize_L2(embeddings)
        self.index = faiss.IndexFlatIP(self.DIM)
        self.index.add(embeddings.astype(np.float32))
        self.resource_ids = resource_ids
        print(f"[IsoEmbedder] Index built: {self.index.ntotal} vectors, dim={self.DIM}")

    def search(self, query: str, k: int = 5) -> list[tuple[int, float]]:
        self.load()
        qvec = self.model.encode([query]).astype(np.float32)
        faiss.normalize_L2(qvec)
        distances, indices = self.index.search(qvec, k)
        return [(self.resource_ids[i], float(distances[0][j]))
                for j, i in enumerate(indices[0]) if 0 <= i < len(self.resource_ids)]

    def save_index(self, path: str):
        if self.index is None:
            raise ValueError("No index to save")
        faiss.write_index(self.index, path)
        import hashlib
        checksum = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                checksum.update(chunk)
        meta = {
            "resource_ids": self.resource_ids,
            "dim": self.DIM,
            "model": self.model_id,
            "checksum_sha256": checksum.hexdigest(),
        }
        import json
        with open(path + ".meta.json", "w") as f:
            json.dump(meta, f)

    def load_index(self, path: str):
        self.index = faiss.read_index(path)
        import json
        with open(path + ".meta.json") as f:
            meta = json.load(f)
        self.resource_ids = meta["resource_ids"]
        if "checksum_sha256" in meta:
            import hashlib
            expected = meta["checksum_sha256"]
            actual = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    actual.update(chunk)
            if actual.hexdigest() != expected:
                import logging
                logging.getLogger(__name__).warning(
                    f"FAISS index checksum mismatch at {path}. "
                    f"Expected {expected}, got {actual.hexdigest()}. "
                    "The index file may be corrupted. Run rebuild_faiss.py."
                )

    def get_candidates(self, query: str, k: int = 10) -> list[int]:
        results = self.search(query, k)
        return [rid for rid, score in results if score >= 0.3]
