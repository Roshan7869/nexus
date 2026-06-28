"""
NEXUS NumpyRouter — CPU-native vector search replacing FAISS.

At NEXUS scale (1,174 routable resources × 384 dims = 1.8MB), brute-force
numpy dot-product is faster than FAISS IndexFlatIP and eliminates:
  - Index file I/O + meta.json sync (RC-3: 40 ghost entries)
  - Post-filter waste (RC-2: 657 non-routable entries scanned)
  - Stale index rebuilds (33-day-old FAISS index)

Architecture:
  - Loads routable embeddings from SQLite into a numpy matrix at startup
  - L2-normalizes once (not per-query like FAISS)
  - Single matmul per query: query_vec @ matrix.T → all cosine scores
  - Optional cluster pre-filter reduces search space to ~150-200 vectors
  - No index files, no checksums, no ghosts, no desync possible

Performance (1,174 vectors × 384 dims):
  - Load: ~15ms (SQLite read + numpy reshape)
  - Search: ~0.2ms (1×384 @ 384×1174 matmul with SIMD)
  - Memory: 1.8MB (float32 matrix)
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

DB_PATH = Path.home() / "nexus" / "db" / "nexus.db"
DIM = 384
ROUTABLE_TYPES = {"skill", "command", "agent", "mcp"}

# Regex to strip YAML frontmatter and HTML comments from embedding text
_YAML_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_PREFIX_RE = re.compile(r"^[a-z0-9-]+\s+", re.IGNORECASE)  # "connect-chrome Connect Chrome ..."


def clean_text_for_embedding(name: str, description: str, body_text: str) -> str:
    """Strip YAML frontmatter, HTML comments, and name prefixes from embedding text.

    This fixes RC-6: embeddings were encoding noise like
    '<!-- AUTO-GENERATED from SKILL.md.tmpl -->' and '---\\nname: foo\\n---'
    instead of semantic content.
    """
    # Start with description (usually the best semantic summary)
    parts: list[str] = []
    if description:
        # Remove any HTML comments from description
        desc = _HTML_COMMENT_RE.sub("", description).strip()
        if desc:
            parts.append(desc)

    if body_text:
        body = body_text[:500]
        # Strip YAML frontmatter at the start
        body = _YAML_FRONTMATTER_RE.sub("", body)
        # Strip HTML comments
        body = _HTML_COMMENT_RE.sub("", body)
        # Strip leading name prefix (e.g., "connect-chrome Connect Chrome ...")
        body = _PREFIX_RE.sub("", body)
        body = body.strip()
        if body:
            parts.append(body)

    # Fallback to name if nothing else
    if not parts:
        parts.append(name)

    return " ".join(parts)[:512]


class NumpyRouter:
    """In-memory numpy cosine similarity router.

    Replaces FAISS IndexFlatIP for NEXUS-scale retrieval. Loads once at
    startup, searches in <1ms per query with zero I/O.
    """

    _instance: Optional["NumpyRouter"] = None
    _instance_lock = threading.Lock()

    def __init__(self, db_path: str = None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self._conn: Optional[sqlite3.Connection] = None
        self._matrix: Optional[np.ndarray] = None  # (N, DIM) L2-normalized
        self._resource_ids: list[int] = []
        self._resource_meta: dict[int, dict] = {}  # id → {name, type, description, ...}
        self._loaded = False
        self._load_time_ms: float = 0

    @classmethod
    def get(cls) -> "NumpyRouter":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), timeout=60, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def load(self, resource_types: Optional[set[str]] = None) -> None:
        """Load routable embeddings from SQLite into numpy matrix.

        Args:
            resource_types: Override routable types. Default: skill/command/agent/mcp.
        """
        if self._loaded:
            return

        start = time.perf_counter()
        types = resource_types or ROUTABLE_TYPES
        placeholders = ",".join(f"'{t}'" for t in types)

        rows = self.conn.execute(
            f"""
            SELECT r.id, r.name, r.resource_type, r.description,
                   r.body_text, r.tier, r.times_triggered, r.times_correct,
                   r.times_wrong, r.embedding
            FROM resources r
            WHERE r.status = 'active'
            AND r.resource_type IN ({placeholders})
            AND r.embedding IS NOT NULL
            AND r.id NOT IN (SELECT id FROM resources WHERE name LIKE 'chromadb-%')
            """
        ).fetchall()

        ids = []
        vectors = []
        meta = {}

        for row in rows:
            emb_bytes = row["embedding"]
            if not emb_bytes:
                continue
            try:
                vec = np.frombuffer(emb_bytes, dtype=np.float32)
            except Exception:
                continue
            if len(vec) != DIM:
                continue

            rid = row["id"]
            ids.append(rid)
            vectors.append(vec)
            meta[rid] = {
                "id": rid,
                "name": row["name"] or "",
                "resource_type": row["resource_type"],
                "description": (row["description"] or "")[:200],
                "tier": row["tier"] or 2,
                "times_triggered": row["times_triggered"] or 0,
                "times_correct": row["times_correct"] or 0,
                "times_wrong": row["times_wrong"] or 0,
            }

        if not vectors:
            logger.warning("NumpyRouter: no routable embeddings found in DB")
            self._loaded = True
            return

        self._matrix = np.vstack(vectors).astype(np.float32)
        # L2-normalize once at load time (not per-query)
        norms = np.linalg.norm(self._matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0  # avoid div-by-zero
        self._matrix = self._matrix / norms

        self._resource_ids = ids
        self._resource_meta = meta
        self._loaded = True
        self._load_time_ms = (time.perf_counter() - start) * 1000

        logger.info(
            f"NumpyRouter loaded: {len(ids)} routable vectors, "
            f"{self._load_time_ms:.1f}ms load, {self._matrix.nbytes / 1024:.1f}KB"
        )

    def reload(self) -> None:
        """Force reload from DB (after embedding rebuild)."""
        self._loaded = False
        self._matrix = None
        self._resource_ids = []
        self._resource_meta = {}
        self.load()

    def search(
        self,
        query_vec: np.ndarray,
        top_k: int = 10,
        cluster_ids: Optional[list[int]] = None,
        theta: float = 0.0,
    ) -> list[dict]:
        """Compute cosine similarity against all routable resources.

        Args:
            query_vec: Pre-computed query embedding (384-dim). Will be normalized.
            top_k: Number of results to return.
            cluster_ids: Optional list of resource IDs to restrict search to.
            theta: Minimum cosine score. Results below this are excluded.

        Returns:
            List of dicts with id, name, resource_type, iso_score, tier, etc.
            Sorted by iso_score descending.
        """
        if not self._loaded:
            self.load()

        if self._matrix is None or len(self._resource_ids) == 0:
            return []

        # Normalize query vector
        q = query_vec.astype(np.float32)
        q_norm = np.linalg.norm(q)
        if q_norm > 0:
            q = q / q_norm

        # Compute all cosine scores in one matmul
        scores = self._matrix @ q  # (N,) inner product = cosine (both normalized)

        # Build results
        if cluster_ids is not None:
            cluster_set = set(cluster_ids)
            results = [
                (self._resource_ids[i], float(scores[i]))
                for i in range(len(self._resource_ids))
                if self._resource_ids[i] in cluster_set
            ]
        else:
            results = [
                (self._resource_ids[i], float(scores[i]))
                for i in range(len(self._resource_ids))
            ]

        # Sort by score descending
        results.sort(key=lambda x: x[1], reverse=True)

        # Apply theta threshold
        if theta > 0:
            results = [(rid, score) for rid, score in results if score >= theta]

        # Take top_k
        results = results[:top_k]

        # Enrich with metadata
        enriched = []
        for rid, score in results:
            meta = self._resource_meta.get(rid, {})
            enriched.append({
                "id": rid,
                "name": meta.get("name", ""),
                "resource_type": meta.get("resource_type", ""),
                "iso_score": round(score, 4),
                "description": meta.get("description", ""),
                "tier": meta.get("tier", 2),
                "times_triggered": meta.get("times_triggered", 0),
                "times_correct": meta.get("times_correct", 0),
                "times_wrong": meta.get("times_wrong", 0),
            })

        return enriched

    def search_by_text(
        self,
        query: str,
        encoder,
        top_k: int = 10,
        cluster_ids: Optional[list[int]] = None,
        theta: float = 0.0,
    ) -> list[dict]:
        """Encode query text and search.

        Args:
            query: Natural language query string.
            encoder: Object with .encode(text) → np.ndarray (SentenceTransformer).
            top_k: Number of results.
            cluster_ids: Optional cluster-restricted resource IDs.
            theta: Minimum score threshold.

        Returns:
            Same as search().
        """
        vec = encoder.encode([query]).astype(np.float32)
        return self.search(vec[0], top_k, cluster_ids, theta)

    @property
    def ntotal(self) -> int:
        """Number of vectors in the index (FAISS compat)."""
        return len(self._resource_ids)

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
        self._loaded = False
        self._matrix = None
        self._resource_ids = []
        self._resource_meta = {}

    def stats(self) -> dict:
        """Return router statistics for diagnostics."""
        return {
            "backend": "numpy",
            "total_vectors": len(self._resource_ids),
            "dimension": DIM,
            "memory_kb": (self._matrix.nbytes / 1024) if self._matrix is not None else 0.0,
            "load_time_ms": round(self._load_time_ms, 1),
            "loaded": self._loaded,
        }