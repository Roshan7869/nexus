#!/usr/bin/env python3
"""
NEXUS Cluster Numpy Search — Vector similarity via numpy dot-product.

This is the fallback path for cluster-scoped search when operating
outside the main FAISS index. Computes cosine similarity by reading
embedding BLOBs from SQLite and using numpy operations.

NOTE: Despite the name, this does NOT use FAISS for search even when
the library is available. The FAISS library check is retained for
possible future integration. For FAISS-based search, use IsoEmbedder
or ToolAttention classes instead.
"""

import json
import logging
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "db" / "nexus.db"


class ClusterNumpySearch:
    """Numpy-backed vector search for cluster routing.

    Reads embedding BLOBs from SQLite and computes cosine similarity
    via numpy dot-product. Used when cluster-scoped search needs to
    filter on cluster_membership table (which the main FAISS index
    does not support).
    """

    def __init__(self, db_path: str = None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self._conn = None
        self._index = None
        self._id_map = {}

        try:
            import faiss
            logger.info("FAISS library available (not used by this class)")
        except ImportError:
            pass

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def get_cluster_resource_ids(
        self, cluster_names: List[str], limit: int = 200
    ) -> List[int]:
        """Get resource IDs for resources in specified clusters.

        Uses the resources.cluster column directly (populated by
        ClusterClassifier) for fast cluster-scoped routing.

        Args:
            cluster_names: List of cluster names to include.
            limit: Max number of resource IDs to return.

        Returns:
            List of resource IDs matching the clusters.
        """
        if not cluster_names:
            return []

        try:
            placeholders = ",".join("?" for _ in cluster_names)
            rows = self.conn.execute(
                f"SELECT id FROM resources "
                f"WHERE cluster IN ({placeholders}) AND status='active' "
                f"ORDER BY cluster_confidence DESC "
                f"LIMIT {limit}",
                cluster_names,
            ).fetchall()
            return [row[0] for row in rows]
        except Exception as e:
            logger.warning(f"get_cluster_resource_ids failed: {e}")
            return []

    def search(
        self,
        query_embedding: np.ndarray,
        cluster_names: List[str] = None,
        top_k: int = 20,
    ) -> List[Dict]:
        """Search for similar resources within specified clusters.

        Args:
            query_embedding: Query vector (384-dim).
            cluster_names: Limit search to these clusters. None = all.
            top_k: Number of results to return.

        Returns:
            List of dicts with keys: resource_id, name, score, cluster_name
        """
        results = []

        try:
            # Build query filter for clusters
            if cluster_names:
                placeholders = ",".join("?" for _ in cluster_names)
                sql = f"""
                    SELECT r.id, r.name, r.embedding, cm.cluster_name
                    FROM resources r
                    JOIN cluster_membership cm ON r.id = cm.resource_id
                    WHERE cm.cluster_name IN ({placeholders})
                    AND r.embedding IS NOT NULL
                    AND r.status = 'active'
                """
                rows = self.conn.execute(sql, cluster_names).fetchall()
            else:
                sql = """
                    SELECT r.id, r.name, r.embedding, cm.cluster_name
                    FROM resources r
                    JOIN cluster_membership cm ON r.id = cm.resource_id
                    WHERE r.embedding IS NOT NULL
                    AND r.status = 'active'
                """
                rows = self.conn.execute(sql).fetchall()

            if not rows:
                return results

            # Compute cosine similarity using numpy
            query_norm = query_embedding / (np.linalg.norm(query_embedding) + 1e-8)

            for row in rows:
                embedding_bytes = row["embedding"]
                if not embedding_bytes:
                    continue

                try:
                    emb = np.frombuffer(embedding_bytes, dtype=np.float32)
                    if len(emb) != len(query_embedding):
                        continue
                    emb_norm = emb / (np.linalg.norm(emb) + 1e-8)
                    score = float(np.dot(query_norm, emb_norm))

                    results.append({
                        "resource_id": row["id"],
                        "name": row["name"],
                        "score": score,
                        "cluster_name": row["cluster_name"],
                    })
                except Exception:
                    continue

            # Sort by score descending
            results.sort(key=lambda x: x["score"], reverse=True)
            return results[:top_k]

        except Exception as e:
            logger.warning(f"ClusterNumpySearch search failed: {e}")
            return results

    def get_cluster_stats(self) -> Dict[str, int]:
        """Get resource count per cluster from the resources table.

        Returns:
            Dict mapping cluster_name → count of active resources.
        """
        try:
            rows = self.conn.execute(
                "SELECT cluster, COUNT(*) FROM resources "
                "WHERE cluster IS NOT NULL AND status='active' "
                "GROUP BY cluster"
            ).fetchall()
            return {row[0]: row[1] for row in rows}
        except Exception as e:
            logger.warning(f"get_cluster_stats failed: {e}")
            return {}

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None