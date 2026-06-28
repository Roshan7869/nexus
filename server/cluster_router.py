"""NEXUS v4 Cluster Router.

Routes queries to domain clusters before v4 ISO scoring. This reduces the
search space from 1,097 resources to ~150-200 per activated cluster.

Pipeline: Query → [Cluster Affinity Scoring] → [Activated Clusters] →
          [Filtered FAISS] → [v4 ISO Scoring] → Results

Algorithm:
1. Encode query using all-MiniLM-L6-v2 (same model as FAISS index)
2. Compute cosine similarity between query embedding and each cluster centroid
3. Activate clusters where affinity >= min_affinity threshold
4. If no cluster activates, fall back to 'general' cluster + full search
5. Return activated cluster names and their resource IDs
"""

import json
import logging
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional

import numpy as np

_SELF_DIR = str(Path(__file__).parent)
if _SELF_DIR not in sys.path:
    sys.path.insert(0, _SELF_DIR)

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "db" / "nexus.db"


class ClusterRouter:
    """Routes queries to domain clusters using centroid-based affinity scoring."""

    def __init__(self, db_path: str = None, index_path: str = None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.index_path = Path(index_path) if index_path else None
        self._conn = None
        self._centroids: dict[str, np.ndarray] = {}
        self._centroids_loaded = False
        self._cluster_faiss = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _load_centroids(self):
        """Load cluster centroid embeddings from the database."""
        if self._centroids_loaded:
            return

        cursor = self.conn.execute(
            "SELECT name, centroid_embedding FROM cluster_definitions WHERE centroid_embedding IS NOT NULL"
        )
        for row in cursor.fetchall():
            centroid = np.frombuffer(row["centroid_embedding"], dtype=np.float32)
            if len(centroid) == 384:
                self._centroids[row["name"]] = centroid

        self._centroids_loaded = True
        logger.info(f"Loaded {len(self._centroids)} cluster centroids")

    def _get_cluster_faiss(self):
        """Lazy-load the ClusterNumpySearch module."""
        if self._cluster_faiss is None:
            from cluster_faiss import ClusterNumpySearch
            self._cluster_faiss = ClusterNumpySearch(str(self.db_path))
        return self._cluster_faiss

    def route_to_clusters(
        self,
        query: str,
        max_clusters: int = 2,
        min_affinity: float = 0.35,
        project: str = "default",
    ) -> dict:
        """Route a query to the top-K domain clusters.

        Args:
            query: Task description to route.
            max_clusters: Maximum number of clusters to activate.
            min_affinity: Minimum affinity score to activate a cluster.
            project: Project context for logging.

        Returns:
            Dict with keys:
                - query: Original query string
                - activated_clusters: List of {name, affinity, resource_count}
                - resource_ids: Set of all resource IDs in activated clusters
                - total_resources_scanned: Total resources in activated clusters
                - fallback: Whether fallback to full search was used
                - latency_ms: Time taken for routing
        """
        start_time = time.time()

        self._load_centroids()

        # Compute affinity scores
        affinities = self._compute_affinities(query)

        # Sort by affinity and activate top-K above threshold
        sorted_clusters = sorted(
            affinities.items(), key=lambda x: -x[1]
        )

        activated = []
        for cluster_name, affinity in sorted_clusters:
            if affinity >= min_affinity and len(activated) < max_clusters:
                activated.append({
                    "name": cluster_name,
                    "affinity": round(affinity, 4),
                    "resource_count": 0,  # filled below
                })

        # Centroid affinities are naturally low — centroids average 200+
        # resource embeddings, so a short query rarely scores >0.35 against
        # any centroid. If fewer than max_clusters activated, fill remaining
        # slots with the top clusters by affinity regardless of threshold.
        if len(activated) < max_clusters:
            for cluster_name, affinity in sorted_clusters:
                if len(activated) >= max_clusters:
                    break
                if not any(c["name"] == cluster_name for c in activated):
                    activated.append({
                        "name": cluster_name,
                        "affinity": round(affinity, 4),
                        "resource_count": 0,
                    })

        # If still no cluster activates, fall back to general
        fallback = len(activated) == 0
        if fallback:
            # Use general cluster + top cluster by keyword match
            from cluster_config import CLUSTER_DEFINITIONS
            activated.append({
                "name": "general",
                "affinity": 0.0,
                "resource_count": 0,
            })
            # Also try keyword-based fallback
            keyword_cluster = self._keyword_fallback(query)
            if keyword_cluster and keyword_cluster != "general":
                activated.append({
                    "name": keyword_cluster,
                    "affinity": 0.3,  # low confidence
                    "resource_count": 0,
                })

        # Get resource IDs for activated clusters
        cluster_names = [c["name"] for c in activated]
        cluster_faiss = self._get_cluster_faiss()
        resource_ids = cluster_faiss.get_cluster_resource_ids(cluster_names)

        # Update resource counts
        cluster_stats = cluster_faiss.get_cluster_stats()
        for c in activated:
            c["resource_count"] = cluster_stats.get(c["name"], 0)

        latency_ms = (time.time() - start_time) * 1000

        # Log activation
        self._log_activation(
            query, project, activated, affinities,
            len(resource_ids), latency_ms,
        )

        result = {
            "query": query,
            "activated_clusters": activated,
            "resource_ids": resource_ids,
            "total_resources_scanned": len(resource_ids),
            "fallback": fallback,
            "latency_ms": round(latency_ms, 2),
        }

        logger.info(
            f"Cluster routing: '{query[:50]}' → "
            + ", ".join(f"{c['name']}({c['affinity']:.2f})" for c in activated)
            + f" | {len(resource_ids)} resources | {latency_ms:.1f}ms"
        )

        return result

    def _compute_affinities(self, query: str) -> dict[str, float]:
        """Compute cosine similarity between query and each cluster centroid.

        Args:
            query: Query string to encode.

        Returns:
            Dict mapping cluster name to affinity score.
        """
        affinities = {}

        # Try semantic similarity using centroids
        if self._centroids:
            try:
                from iso_embedder import IsoEmbedder
                embedder = IsoEmbedder.get()
                query_embedding = embedder.encode(query)
                query_norm = query_embedding / (np.linalg.norm(query_embedding) + 1e-10)

                for cluster_name, centroid in self._centroids.items():
                    centroid_norm = centroid / (np.linalg.norm(centroid) + 1e-10)
                    affinity = float(np.dot(query_norm, centroid_norm))
                    affinities[cluster_name] = affinity
            except Exception as e:
                logger.warning(f"Semantic affinity computation failed: {e}")

        # If semantic similarity failed, fall back to keyword matching
        if not affinities:
            affinities = self._keyword_affinities(query)

        return affinities

    def _keyword_affinities(self, query: str) -> dict[str, float]:
        """Compute cluster affinities using keyword matching as fallback.

        Args:
            query: Query string.

        Returns:
            Dict mapping cluster name to affinity score.
        """
        from cluster_config import CLUSTER_DEFINITIONS

        affinities = {}
        query_lower = query.lower()

        for cluster_name, config in CLUSTER_DEFINITIONS.items():
            if cluster_name == "general":
                continue

            keyword_hits = sum(
                1 for kw in config.get("trigger_keywords", [])
                if kw.lower() in query_lower
            )

            if keyword_hits > 0:
                # Normalize: more keyword hits = higher affinity, max 0.85
                affinities[cluster_name] = min(0.3 + keyword_hits * 0.1, 0.85)
            else:
                affinities[cluster_name] = 0.0

        return affinities

    def _keyword_fallback(self, query: str) -> str:
        """Simple keyword-based fallback when no cluster activates.

        Returns the cluster name with the most keyword hits.
        """
        from cluster_config import CLUSTER_DEFINITIONS

        query_lower = query.lower()
        best_cluster = "general"
        best_hits = 0

        for cluster_name, config in CLUSTER_DEFINITIONS.items():
            if cluster_name == "general":
                continue
            hits = sum(1 for kw in config.get("trigger_keywords", []) if kw.lower() in query_lower)
            if hits > best_hits:
                best_hits = hits
                best_cluster = cluster_name

        return best_cluster

    def _log_activation(
        self,
        query: str,
        project: str,
        activated_clusters: list,
        affinity_scores: dict,
        total_resources: int,
        latency_ms: float,
    ):
        """Log cluster activation to the database."""
        try:
            self.conn.execute(
                """INSERT INTO cluster_activation_log
                (query, project, activated_clusters, affinity_scores,
                 total_resources_scanned, latency_ms)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    query[:500],
                    project,
                    json.dumps([c["name"] for c in activated_clusters]),
                    json.dumps(affinity_scores),
                    total_resources,
                    latency_ms,
                ),
            )
            self.conn.commit()
        except Exception as e:
            logger.warning(f"Failed to log cluster activation: {e}")

    def get_cluster_list(self) -> list[dict]:
        """List all clusters with their stats.

        Returns:
            List of dicts with cluster name, display name, description,
            trigger keywords, resource count, and centroid status.
        """
        from cluster_config import CLUSTER_DEFINITIONS, CLUSTER_ORDER

        self._load_centroids()
        cluster_faiss = self._get_cluster_faiss()
        stats = cluster_faiss.get_cluster_stats()

        result = []
        for name in CLUSTER_ORDER:
            config = CLUSTER_DEFINITIONS[name]
            result.append({
                "name": name,
                "display_name": config["display_name"],
                "description": config["description"],
                "trigger_keywords": config["trigger_keywords"][:10],
                "default_skill": config["default_skill"],
                "default_agents": config.get("default_agents", []),
                "resource_count": stats.get(name, 0),
                "has_centroid": name in self._centroids,
                "auto_activate_threshold": config.get("auto_activate_threshold", 0.35),
            })

        return result